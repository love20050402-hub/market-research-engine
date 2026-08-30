from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from collectors.base import NORMALIZED_RECORD_FIELDS, CollectorError
from collectors.hackernews import HackerNewsCollector
from collectors.reddit import RedditCollector
from pipeline.prefilter import candidate_prefilter
from pipeline.scoring import calculate_score
from pipeline.validator import prepare_pain_signals, validate_opportunities
from providers.llm_provider import MockLLMProvider, ProviderError
from providers.ollama_provider import OllamaProvider
from providers.schema_validation import SchemaValidationError, validate_json_schema
from run import run_pipeline


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def make_config() -> dict:
    return {
        "hackernews": {
            "endpoint": "https://hn.algolia.com/api/v1/search_by_date",
            "query": "wish",
            "tags": "ask_hn",
            "hits_per_page": 20,
            "timeout_seconds": 30,
            "user_agent": "market-research-engine-test/1.0",
        },
        "prefilter": {"max_candidates": 20},
        "llm": {
            "provider": "mock",
            "request_delay_seconds": 0,
            "ollama": {
                "model": "qwen3:8b",
                "base_url": "http://127.0.0.1:11434",
            },
        },
        "output": {
            "directory": "output",
            "raw_signals": "raw_signals.json",
            "pain_signals": "pain_signals.json",
            "opportunities": "opportunities.json",
            "report": "market_opportunity_report.md",
        },
    }


class EngineTests(unittest.TestCase):
    def test_hn_normalization_and_prefilter_match_existing_logic(self) -> None:
        payload = {
            "hits": [
                {
                    "objectID": "1",
                    "title": "Ask HN: A better way to reconcile reports?",
                    "story_text": (
                        "We manually copy paste three spreadsheets every week. "
                        "It takes five hours and the current tool is too expensive. "
                        "This tedious problem blocks the weekly close."
                    ),
                    "author": "a",
                    "created_at": "2026-08-01T00:00:00Z",
                    "num_comments": 8,
                    "points": 12,
                },
                {"objectID": "2", "title": "tiny", "story_text": "short"},
            ]
        }
        captured: dict[str, object] = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        collector = HackerNewsCollector(make_config()["hackernews"], opener=opener)
        normalized = collector.fetch_and_normalize()
        candidates = candidate_prefilter(normalized)

        self.assertIn("query=wish", str(captured["url"]))
        self.assertIn("tags=ask_hn", str(captured["url"]))
        self.assertIn("hitsPerPage=20", str(captured["url"]))
        self.assertEqual(normalized[0]["url"], "https://news.ycombinator.com/item?id=1")
        self.assertEqual(set(normalized[0]), set(NORMALIZED_RECORD_FIELDS))
        self.assertEqual(normalized[0]["source"], "hackernews")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["raw_data_count"], 2)
        self.assertGreater(candidates[0]["candidate_priority_score"], 0)

    def test_reddit_oauth_search_normalizes_to_shared_schema(self) -> None:
        calls = []
        search_payload = {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "name": "t3_abc123",
                            "id": "abc123",
                            "title": "Is there a tool for reconciling supplier invoices?",
                            "selftext": (
                                "I manually compare every invoice and it takes me hours each week."
                            ),
                            "author": "tester",
                            "created_utc": 1785542400,
                            "permalink": "/r/smallbusiness/comments/abc123/example/",
                            "subreddit": "smallbusiness",
                            "num_comments": 14,
                            "score": 22,
                            "over_18": False,
                        },
                    }
                ]
            }
        }

        def opener(request, timeout):
            del timeout
            calls.append(request)
            if request.full_url.endswith("/api/v1/access_token"):
                return FakeResponse(json.dumps({"access_token": "token"}).encode("utf-8"))
            return FakeResponse(json.dumps(search_payload).encode("utf-8"))

        collector = RedditCollector(
            {
                "queries": ["is there a tool"],
                "limit_per_query": 10,
                "max_records": 10,
                "request_delay_seconds": 0,
            },
            opener=opener,
            environ={
                "REDDIT_CLIENT_ID": "client",
                "REDDIT_CLIENT_SECRET": "secret",
                "REDDIT_USER_AGENT": "windows:test:v1 (by /u/test)",
            },
            sleeper=lambda seconds: None,
        )
        records = collector.fetch_and_normalize()

        self.assertEqual(len(records), 1)
        self.assertEqual(set(records[0]), set(NORMALIZED_RECORD_FIELDS))
        self.assertEqual(records[0]["source"], "reddit")
        self.assertEqual(records[0]["source_context"], "r/smallbusiness")
        self.assertEqual(records[0]["search_keyword"], "is there a tool")
        self.assertTrue(calls[0].get_header("Authorization").startswith("Basic "))
        self.assertEqual(calls[1].get_header("Authorization"), "bearer token")

    def test_reddit_missing_oauth_has_actionable_error(self) -> None:
        collector = RedditCollector(
            {"queries": ["manually"]},
            environ={},
            sleeper=lambda seconds: None,
        )
        with self.assertRaisesRegex(CollectorError, "REDDIT_CLIENT_ID"):
            collector.fetch()

    def test_pain_qualification_requires_clusterable_type_and_exact_quote(self) -> None:
        base = {
            "source": "reddit",
            "source_title": "Manual reconciliation",
            "source_url": "https://www.reddit.com/r/test/comments/abc/example/",
            "source_date": "2026-08-01T00:00:00Z",
            "target_user": "營運人員",
            "pain_statement": "每週手動核對資料",
            "current_solution": "spreadsheet",
            "current_solution_problem": "仍需人工核對",
            "workaround": "manual reconciliation",
            "payment_signal": "unknown",
            "economic_cost": "five hours every week",
            "frequency_signal": "每週",
            "evidence_summary": "每週人工核對五小時",
            "evidence_strength": "STRONG",
            "possible_category": "營運",
            "signal_type": "OPERATIONAL_PAIN",
            "qualification_evidence": "I manually reconcile this for five hours every week",
            "qualification_basis": [
                "REPETITIVE_MANUAL_WORK",
                "MEASURABLE_TIME_COST",
            ],
            "qualifies_pain_signal": True,
            "is_sentinel": False,
            "_source_text": (
                "Manual reconciliation\n"
                "I manually reconcile this for five hours every week and need a better way."
            ),
        }
        self.assertEqual(len(prepare_pain_signals([base])), 1)
        self.assertEqual(
            prepare_pain_signals([base])[0]["signal_type"], "OPERATIONAL_PAIN"
        )

        opinion = {**base, "signal_type": "OPINION"}
        unsupported = {**base, "qualification_evidence": "invented quotation"}
        no_basis = {**base, "qualification_basis": []}
        self.assertEqual(prepare_pain_signals([opinion, unsupported, no_basis]), [])

    def test_cross_source_opportunity_is_marked(self) -> None:
        common_signal = {
            "target_user": "小型財務團隊",
            "pain_statement": "每週人工核對發票",
            "current_solution": "spreadsheet",
            "current_solution_problem": "仍需人工處理",
            "workaround": "人工核對",
            "payment_signal": "unknown",
            "economic_cost": "每週五小時",
            "frequency_signal": "每週",
            "evidence_strength": "STRONG",
            "source_title": "Manual invoice reconciliation",
        }
        signals = [
            {
                **common_signal,
                "signal_id": "PS-001",
                "source": "hackernews",
                "source_url": "https://news.ycombinator.com/item?id=1",
            },
            {
                **common_signal,
                "signal_id": "PS-002",
                "source": "reddit",
                "source_url": "https://www.reddit.com/r/test/comments/abc/example/",
            },
        ]
        cluster = {
            "opportunity_name": "小型團隊人工發票核對",
            "target_customer": "小型財務團隊",
            "core_problem": "每週人工核對發票",
            "source_signal_ids": ["PS-001", "PS-002"],
            "strongest_evidence": "兩個來源都提到每週人工核對",
            "repeated_patterns": ["人工核對", "每週重複"],
            "current_alternatives": ["spreadsheet"],
            "alternative_weakness": "仍需人工處理",
            "payment_evidence": "unknown",
            "payment_evidence_level": "NONE",
            "suggested_solution_direction": "先以人工協助驗證",
            "confidence": "MEDIUM",
            "key_unknowns": ["資料格式差異"],
            "why_it_matters": "每週占用工時",
            "channel": "HN 與 Reddit 財務社群",
            "durability": "長期週期性工作",
            "low_cost_entry": "低成本訪談",
            "validation_method": "7-14 天訪談",
        }
        opportunities = validate_opportunities({"clusters": [cluster]}, signals)
        self.assertEqual(opportunities[0]["source_diversity"], "CROSS_SOURCE_VALIDATED")

    def test_score_caps_and_evidence_gates(self) -> None:
        result = calculate_score(
            {
                "payment_evidence_level": "NONE",
                "alternative_weakness": "unknown",
                "durability": "長期存在",
                "channel": "HN",
                "low_cost_entry": "不需庫存",
                "validation_method": "兩週內訪談",
                "target_customer": "小型財務團隊",
                "suggested_solution_direction": "簡單報表轉換服務",
                "opportunity_name": "人工報表整併",
                "core_problem": "人工整理財務資料",
            },
            [
                {
                    "evidence_strength": "STRONG",
                    "economic_cost": "每週五小時",
                    "frequency_signal": "每週",
                    "workaround": "人工 copy paste",
                    "payment_signal": "unknown",
                    "current_solution_problem": "unknown",
                    "source_title": "Manual reporting",
                    "pain_statement": "人工整理報表",
                    "current_solution": "spreadsheet",
                }
            ],
        )
        self.assertEqual(result["score_breakdown"]["pain_severity"], 19)
        self.assertEqual(result["score_breakdown"]["payment_evidence"], 0)
        self.assertEqual(result["score_breakdown"]["solution_gap"], 0)
        self.assertEqual(result["opportunity_score"], 60)
        self.assertEqual(result["decision"], "WATCH")
        self.assertTrue(result["commercial_evidence_weak"])

    def test_payment_gate_rejects_model_direct_without_direct_source_evidence(self) -> None:
        result = calculate_score(
            {
                "payment_evidence_level": "DIRECT",
                "durability": "中等",
                "alternative_weakness": "仍需手動處理",
                "channel": "HN 社群",
                "target_customer": "開發者",
                "low_cost_entry": "低成本訪談",
                "validation_method": "7-14 天訪談",
                "suggested_solution_direction": "流程改善",
            },
            [
                {
                    "evidence_strength": "STRONG",
                    "payment_signal": "未知",
                    "economic_cost": "花費大量時間和精力",
                    "frequency_signal": "unknown",
                    "workaround": "手動修正",
                    "current_solution_problem": "品質不佳",
                },
                {
                    "evidence_strength": "MEDIUM",
                    "payment_signal": "unknown",
                    "economic_cost": (
                        "Time and effort spent applying AI techniques feel obsolete."
                    ),
                    "frequency_signal": "unknown",
                    "workaround": "unknown",
                    "current_solution_problem": "unknown",
                }
            ],
        )
        self.assertEqual(result["payment_evidence_level"], "NONE")
        self.assertEqual(result["score_breakdown"]["payment_evidence"], 0)
        self.assertTrue(result["commercial_evidence_weak"])

    def test_payment_gate_accepts_explicit_monetary_cost(self) -> None:
        result = calculate_score(
            {"payment_evidence_level": "NONE"},
            [
                {
                    "evidence_strength": "MEDIUM",
                    "payment_signal": "unknown",
                    "economic_cost": "每年需支付更多訂閱費用",
                    "frequency_signal": "每年",
                    "workaround": "unknown",
                    "current_solution_problem": "價格持續上升",
                }
            ],
        )
        self.assertEqual(result["payment_evidence_level"], "DIRECT")
        self.assertGreaterEqual(result["score_breakdown"]["payment_evidence"], 10)
        self.assertFalse(result["commercial_evidence_weak"])

    def test_payment_summary_with_no_evidence_caps_score(self) -> None:
        result = calculate_score(
            {
                "payment_evidence": "未提及具體金錢成本",
                "current_alternatives": ["付費工具"],
            },
            [
                {
                    "evidence_strength": "STRONG",
                    "payment_signal": "每月已支付 100 美元",
                    "economic_cost": "每月 100 美元",
                    "frequency_signal": "每月",
                    "workaround": "unknown",
                    "current_solution_problem": "unknown",
                }
            ],
        )
        self.assertLessEqual(result["score_breakdown"]["payment_evidence"], 5)
        self.assertEqual(result["score_breakdown"]["payment_evidence"], 0)
        self.assertTrue(result["commercial_evidence_weak"])

    def test_unknown_current_solution_caps_solution_gap(self) -> None:
        result = calculate_score(
            {
                "alternative_weakness": "現有工具太貴、太慢且功能不足",
                "current_alternatives": ["unknown"],
            },
            [
                {
                    "evidence_strength": "STRONG",
                    "payment_signal": "unknown",
                    "economic_cost": "每週五小時",
                    "frequency_signal": "每週",
                    "workaround": "每週手動處理",
                    "current_solution_problem": "工具太慢",
                }
            ],
        )
        self.assertLessEqual(result["score_breakdown"]["solution_gap"], 5)

    def test_three_critical_unknowns_force_low_confidence_and_no_do_now(self) -> None:
        signal = {
            "signal_id": "PS-001",
            "source": "hackernews",
            "source_url": "https://news.ycombinator.com/item?id=1",
            "source_title": "Paid weekly reconciliation problem",
            "target_user": "財務團隊",
            "pain_statement": "每週人工核對",
            "current_solution": "付費工具",
            "current_solution_problem": "仍需人工處理",
            "workaround": "每週手動核對",
            "payment_signal": "每月已支付 100 美元",
            "economic_cost": "每月 100 美元",
            "frequency_signal": "每週",
            "evidence_strength": "STRONG",
        }
        candidate = {
            "opportunity_name": "人工核對流程",
            "target_customer": "財務團隊",
            "core_problem": "每週人工核對",
            "source_signal_ids": ["PS-001"],
            "strongest_evidence": "每週人工核對且已付費",
            "repeated_patterns": ["人工核對"],
            "current_alternatives": ["付費工具"],
            "alternative_weakness": "仍需人工處理",
            "payment_evidence": "每月已支付 100 美元",
            "payment_evidence_level": "DIRECT",
            "suggested_solution_direction": "先人工協助驗證",
            "confidence": "HIGH",
            "key_unknowns": [
                "使用者付費意願",
                "具體的經濟成本",
                "問題頻率",
                "現有解法",
            ],
            "why_it_matters": "占用工時",
            "channel": "HN 社群",
            "durability": "長期",
            "low_cost_entry": "低成本人工驗證",
            "validation_method": "7-14 天訪談",
        }
        opportunity = validate_opportunities({"clusters": [candidate]}, [signal])[0]
        self.assertGreaterEqual(opportunity["critical_unknown_count"], 3)
        self.assertEqual(opportunity["confidence"], "LOW")
        self.assertNotEqual(opportunity["decision"], "DO NOW")

    def test_low_cost_and_small_team_gate_forces_team_mismatch(self) -> None:
        candidate = {
            "opportunity_name": "工廠硬體檢測",
            "target_customer": "工廠",
            "core_problem": "hardware manufacturing factory 每週停線",
            "current_alternatives": ["付費硬體系統"],
            "alternative_weakness": "仍需人工處理",
            "payment_evidence": "每月已支付 1000 美元",
            "durability": "長期",
            "channel": "公司名單",
            "low_cost_entry": "需要硬體製造與庫存",
            "validation_method": "7-14 天訪談",
            "suggested_solution_direction": "製造 hardware sensor",
        }
        signals = [
            {
                "evidence_strength": "STRONG",
                "payment_signal": "每月已支付 1000 美元",
                "economic_cost": "每次停線損失 1000 美元",
                "frequency_signal": "每週",
                "workaround": "人工巡檢",
                "current_solution_problem": "仍會停線",
                "source_title": "Factory hardware failure",
                "pain_statement": "工廠停線",
                "current_solution": "hardware monitoring",
            }
        ]
        result = calculate_score(candidate, signals)
        self.assertLessEqual(result["score_breakdown"]["low_cost_entry"], 3)
        self.assertLessEqual(result["score_breakdown"]["small_team_fit"], 2)
        self.assertEqual(
            result["decision"], "MARKET INTERESTING / TEAM MISMATCH"
        )
        self.assertNotEqual(result["decision"], "DO NOW")

    def test_market_good_team_mismatch_requires_sufficient_evidence(self) -> None:
        candidate = {
            "opportunity_name": "工廠硬體檢測",
            "target_customer": "工廠",
            "core_problem": "hardware manufacturing factory 每週停線",
            "current_alternatives": ["付費硬體系統"],
            "alternative_weakness": "仍需人工處理",
            "payment_evidence": "每月已支付 1000 美元",
            "durability": "長期",
            "channel": "公司名單",
            "low_cost_entry": "需要硬體製造與庫存",
            "validation_method": "7-14 天訪談",
            "suggested_solution_direction": "製造 hardware sensor",
            "confidence": "MEDIUM",
        }
        signal = {
            "evidence_strength": "STRONG",
            "payment_signal": "每月已支付 1000 美元",
            "economic_cost": "每次停線損失 1000 美元",
            "frequency_signal": "每週",
            "workaround": "人工巡檢",
            "current_solution_problem": "仍會停線",
            "source_title": "Factory hardware failure",
            "pain_statement": "工廠停線",
            "current_solution": "hardware monitoring",
        }
        result = calculate_score(candidate, [signal, dict(signal)])
        self.assertLessEqual(result["critical_unknown_count"], 2)
        self.assertGreater(result["score_breakdown"]["payment_evidence"], 5)
        self.assertEqual(result["decision"], "MARKET GOOD / TEAM MISMATCH")

    def test_final_decision_is_consistent_with_detailed_scores(self) -> None:
        candidate = {
            "opportunity_name": "人工報表核對",
            "target_customer": "小型財務團隊",
            "core_problem": "每週人工核對報表",
            "current_alternatives": ["付費報表工具"],
            "alternative_weakness": "工具仍需大量人工修正",
            "payment_evidence": "兩個團隊已支付工具與承包商費用",
            "durability": "長期週期性工作",
            "channel": "公司名單與 email",
            "low_cost_entry": "可先人工驗證，不需庫存",
            "validation_method": "7-14 天訪談與人工測試",
            "suggested_solution_direction": "先以人工服務驗證",
            "key_unknowns": [],
        }
        signal = {
            "evidence_strength": "STRONG",
            "payment_signal": "已支付工具與承包商",
            "economic_cost": "每月支付 500 美元",
            "frequency_signal": "每週",
            "workaround": "每週人工修正",
            "current_solution_problem": "工具格式不足",
            "source_title": "Weekly paid reporting workaround",
            "pain_statement": "每週人工核對",
            "current_solution": "付費報表工具",
        }
        result = calculate_score(candidate, [signal, dict(signal)])
        allowed = {
            "DO NOW",
            "VALIDATE",
            "WATCH",
            "MARKET INTERESTING / TEAM MISMATCH",
            "MARKET GOOD / TEAM MISMATCH",
            "REJECT",
        }
        self.assertIn(result["decision"], allowed)
        self.assertEqual(result["decision"], "DO NOW")
        self.assertGreaterEqual(result["score_breakdown"]["payment_evidence"], 10)
        self.assertGreater(result["score_breakdown"]["solution_gap"], 5)
        self.assertGreaterEqual(result["score_breakdown"]["low_cost_entry"], 6)
        self.assertGreaterEqual(result["score_breakdown"]["small_team_fit"], 4)

    def test_schema_validation_rejects_missing_and_extra_fields(self) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        validate_json_schema({"name": "valid"}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "missing required field"):
            validate_json_schema({}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "unexpected fields"):
            validate_json_schema({"name": "valid", "invented": 1}, schema)

    def test_ollama_not_running_has_actionable_error(self) -> None:
        def offline_opener(request, timeout):
            del request, timeout
            raise URLError("connection refused")

        with self.assertRaisesRegex(ProviderError, "ollama serve"):
            OllamaProvider(
                base_url="http://127.0.0.1:11434",
                model="qwen3:8b",
                opener=offline_opener,
            )

    def test_ollama_invalid_schema_retries_then_succeeds(self) -> None:
        responses = iter(
            [
                {"message": {"content": "{\"wrong\": true}"}},
                {"message": {"content": "{\"name\": \"ok\"}"}},
            ]
        )
        calls = []

        def opener(request, timeout):
            del timeout
            calls.append(request)
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        provider = OllamaProvider(
            base_url="http://127.0.0.1:11434",
            model="qwen3:8b",
            max_retries=1,
            opener=opener,
            check_ready=False,
        )
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        self.assertEqual(
            provider.generate_json(prompt="Return a name", schema=schema, purpose="test"),
            {"name": "ok"},
        )
        self.assertEqual(len(calls), 2)

    def test_full_mock_pipeline_writes_nonempty_report(self) -> None:
        payload = {
            "hits": [
                {
                    "objectID": "1",
                    "title": "Ask HN: A better way to reconcile reports?",
                    "story_text": (
                        "We manually copy paste three spreadsheets every week. "
                        "It takes five hours and the current tool is too expensive. "
                        "This tedious problem blocks the weekly close."
                    ),
                    "author": "a",
                    "created_at": "2026-08-01T00:00:00Z",
                    "num_comments": 8,
                    "points": 12,
                },
                {
                    "objectID": "2",
                    "title": "Ask HN: Alternative billing export tool?",
                    "story_text": (
                        "We pay for a service but it cannot export the format our accountant "
                        "needs, so a contractor fixes it by hand every week."
                    ),
                    "author": "b",
                    "created_at": "2026-08-02T00:00:00Z",
                    "num_comments": 6,
                    "points": 9,
                },
            ]
        }

        def opener(request, timeout):
            del request, timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        extraction_responses = [
            {
                "target_user": "小型財務團隊",
                "pain_statement": "每週人工合併多份報表",
                "current_solution": "spreadsheet 與昂貴工具",
                "current_solution_problem": "仍需人工 copy paste",
                "workaround": "每週手動處理五小時",
                "payment_signal": "unknown",
                "economic_cost": "每週五小時",
                "frequency_signal": "每週",
                "evidence_summary": "團隊每週手動整理報表五小時",
                "evidence_strength": "STRONG",
                "possible_category": "財務作業",
                "signal_type": "OPERATIONAL_PAIN",
                "qualification_evidence": (
                    "We manually copy paste three spreadsheets every week."
                ),
                "qualification_basis": [
                    "REPETITIVE_MANUAL_WORK",
                    "MEASURABLE_TIME_COST",
                ],
                "qualifies_pain_signal": True,
                "exclusion_reason": "unknown",
            },
            {
                "target_user": "小型財務團隊",
                "pain_statement": "付費工具匯出格式仍需人工修正",
                "current_solution": "付費 billing service",
                "current_solution_problem": "無法匯出會計所需格式",
                "workaround": "承包商每週人工修正",
                "payment_signal": "已支付服務與承包商",
                "economic_cost": "unknown",
                "frequency_signal": "每週",
                "evidence_summary": "現有付費工具後仍需承包商處理",
                "evidence_strength": "STRONG",
                "possible_category": "財務作業",
                "signal_type": "COMMERCIAL_PAIN",
                "qualification_evidence": (
                    "We pay for a service but it cannot export the format our accountant needs"
                ),
                "qualification_basis": [
                    "EXISTING_SOLUTION_DISSATISFACTION",
                    "EXPLICIT_PAYMENT_OR_BUDGET",
                ],
                "qualifies_pain_signal": True,
                "exclusion_reason": "unknown",
            },
        ]
        cluster_response = {
            "clusters": [
                {
                    "opportunity_name": "中小企業人工財務報表整併",
                    "target_customer": "小型財務團隊",
                    "core_problem": "多來源財務資料需反覆人工轉換與合併",
                    "source_signal_ids": ["PS-001", "PS-002", "PS-999"],
                    "strongest_evidence": "每週五小時，且另一訊號已支付服務與承包商",
                    "repeated_patterns": ["人工轉換格式", "付費工具後仍需人工處理"],
                    "current_alternatives": ["spreadsheet", "付費服務", "承包商"],
                    "alternative_weakness": "unknown",
                    "payment_evidence": "一個訊號直接提到已支付服務與承包商",
                    "payment_evidence_level": "WEAK",
                    "suggested_solution_direction": "先以受控檔案轉換服務驗證問題",
                    "confidence": "MEDIUM",
                    "key_unknowns": ["不同資料格式是否足夠一致"],
                    "why_it_matters": "工作每週重複並占用人工時間",
                    "channel": "HN 與公開財務營運社群",
                    "durability": "週期性報表整併不依賴短期新聞",
                    "low_cost_entry": "可先人工協助少量客戶驗證，不需庫存",
                    "validation_method": "7–14 天訪談 5 位財務人員並測試真實報表",
                }
            ]
        }
        provider = MockLLMProvider(
            {
                "pain_extraction": extraction_responses,
                "opportunity_clustering": [cluster_response],
            }
        )
        collector = HackerNewsCollector(make_config()["hackernews"], opener=opener)

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_pipeline(
                config=make_config(),
                provider=provider,
                collector=collector,
                output_dir=Path(temp_dir),
                progress=lambda message: None,
            )
            self.assertEqual(len(result["raw_signals"]), 2)
            self.assertEqual(len(result["pain_signals"]), 2)
            self.assertEqual(len(result["opportunities"]), 1)
            opportunity = result["opportunities"][0]
            self.assertEqual(opportunity["opportunity_score"], 78)
            self.assertEqual(opportunity["score_breakdown"]["payment_evidence"], 13)
            self.assertEqual(opportunity["score_breakdown"]["solution_gap"], 0)
            self.assertEqual(opportunity["risks"]["regulation_risk"], "MEDIUM")
            self.assertFalse(opportunity["commercial_evidence_weak"])
            self.assertTrue(opportunity["publish_eligible"])
            self.assertEqual(opportunity["source_diversity"], "HN_ONLY")
            self.assertIn("來源狀態是「只有 Hacker News」", result["report"])
            self.assertIn("**VALIDATE**", result["report"])
            self.assertIn("#### 一句話結論", result["report"])
            self.assertIn("**Total：78/100**", result["report"])

            for filename in (
                "raw_signals.json",
                "pain_signals.json",
                "opportunities.json",
                "market_opportunity_report.md",
            ):
                path = Path(temp_dir) / filename
                self.assertTrue(path.is_file(), filename)
                self.assertGreater(path.stat().st_size, 0, filename)


if __name__ == "__main__":
    unittest.main()
