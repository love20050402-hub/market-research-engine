"""Evidence-only Pain Signal extraction using the completed workflow prompt."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from providers.llm_provider import LLMProvider, ProviderError


PAIN_SIGNAL_FIELDS = (
    "target_user",
    "pain_statement",
    "current_solution",
    "current_solution_problem",
    "workaround",
    "payment_signal",
    "economic_cost",
    "frequency_signal",
    "evidence_summary",
    "evidence_strength",
    "possible_category",
    "signal_type",
    "qualification_evidence",
    "qualification_basis",
    "qualifies_pain_signal",
    "exclusion_reason",
)

SIGNAL_TYPES = (
    "COMMERCIAL_PAIN",
    "OPERATIONAL_PAIN",
    "SOLUTION_REQUEST",
    "WORKAROUND",
    "PAYMENT_SIGNAL",
    "GENERAL_PROBLEM",
    "OPINION",
)

QUALIFICATION_BASES = (
    "REPETITIVE_MANUAL_WORK",
    "MEASURABLE_TIME_COST",
    "MONETARY_COST",
    "OPERATIONAL_LOSS",
    "ACTIVE_SOLUTION_SEEKING",
    "WORKAROUND_EVIDENCE",
    "EXISTING_SOLUTION_DISSATISFACTION",
    "EXPLICIT_PAYMENT_OR_BUDGET",
)

PAIN_SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_user": {"type": "string"},
        "pain_statement": {"type": "string"},
        "current_solution": {"type": "string"},
        "current_solution_problem": {"type": "string"},
        "workaround": {"type": "string"},
        "payment_signal": {"type": "string"},
        "economic_cost": {"type": "string"},
        "frequency_signal": {"type": "string"},
        "evidence_summary": {"type": "string"},
        "evidence_strength": {
            "type": "string",
            "enum": ["STRONG", "MEDIUM", "WEAK", "UNKNOWN"],
        },
        "possible_category": {"type": "string"},
        "signal_type": {"type": "string", "enum": list(SIGNAL_TYPES)},
        "qualification_evidence": {"type": "string"},
        "qualification_basis": {
            "type": "array",
            "items": {"type": "string", "enum": list(QUALIFICATION_BASES)},
        },
        "qualifies_pain_signal": {"type": "boolean"},
        "exclusion_reason": {"type": "string"},
    },
    "required": list(PAIN_SIGNAL_FIELDS),
    "additionalProperties": False,
}


def build_pain_prompt(candidate: dict[str, Any]) -> str:
    return f"""你是嚴格的商業問題證據稽核員。請只根據下方這一篇 {candidate.get('source', 'unknown')} 原文抽取 Pain Signal；不要搜尋或使用外部市場知識，也不要提出創業點子。

來源資料：
- 來源：{candidate.get('source', 'unknown')}
- 來源社群：{candidate.get('source_context', 'unknown')}
- 標題：{candidate.get('title', 'unknown')}
- 原文：{candidate.get('text', 'unknown')}
- 作者：{candidate.get('author', 'unknown')}
- 日期：{candidate.get('created_at', 'unknown')}
- URL：{candidate.get('url', 'unknown')}
- 留言數：{candidate.get('num_comments', 0)}
- Points：{candidate.get('points', 0)}
- 預篩字詞（只供定位，不是證據）：{candidate.get('candidate_evidence_terms', 'unknown')}
- 空資料占位：{str(bool(candidate.get('is_sentinel'))).lower()}

合格 Pain Signal 必須有「可直接引用的原文」支持以下至少一項：repetitive manual work、measurable time cost、monetary cost、operational loss、active solution seeking、workaround、對既有付費／免費方案的不滿、explicit payment/budget signal。

signal_type 只能是：
- COMMERCIAL_PAIN：明確金錢成本、付費方案不滿或商業損失。
- OPERATIONAL_PAIN：可觀察的重複工作、時間、人力或流程損失。
- SOLUTION_REQUEST：正在主動尋找工具、替代品、商品或服務。
- WORKAROUND：原文明確描述目前為繞過問題所採取的做法。
- PAYMENT_SIGNAL：明確已付費、預算、願付費或詢價。
- GENERAL_PROBLEM：只有一般問題或抱怨，沒有上述可執行證據。
- OPINION：意見、趨勢、預測、一般批評或哲學討論。

只有前五類可以讓 qualifies_pain_signal=true。GENERAL_PROBLEM 與 OPINION 必須為 false。

嚴格規則：
1. 「有人抱怨」不等於商業機會；本節只判斷是否有可研究的問題證據。
2. 不得推測市場規模、營收、競品、預算、付費意願、頻率或經濟損失。
3. 原文未明說的欄位一律填字串 "unknown"，不可用常識補造。
4. current_solution 只能寫原文提到的現行工具、服務或做法。
5. payment_signal 只能寫原文中的已付費、找付費工具、預算或願付費證據。
6. economic_cost 只能寫原文明示的時間、金錢、人力、營收或損失；不要自行換算。
7. qualification_evidence 必須逐字引用標題或原文中最能證明 qualification_basis 的短句，保留原始語言；不得翻譯、改寫或補字。沒有逐字證據就填 "unknown"。
8. qualification_basis 只列原文實際支持的 enum；若沒有任何一項，輸出空陣列。
9. evidence_summary 必須是忠實摘要，不得比原文更強。
10. evidence_strength 僅可為 STRONG / MEDIUM / WEAK / UNKNOWN：有量化成本、直接付費或具體 workaround 可為 STRONG；有清楚且可引用的求解／方案不滿可為 MEDIUM；含糊、假設性或只有情緒不得合格。
11. qualifies_pain_signal 只有在 signal_type 為前五類、qualification_evidence 是原文逐字引用、qualification_basis 非空、具體問題與受影響者都有原文根據時才可為 true。
12. 單純 opinion、trend discussion、general criticism、prediction、philosophical discussion、新聞、炫耀、點子徵集或占位資料一律 false。
13. 除 qualification_evidence 保留原始語言外，其餘摘要使用繁體中文；專有名詞與 URL 可保留原文。

請完整輸出 schema 要求的所有欄位。"""


def extract_pain_signals(
    candidates: list[dict[str, Any]],
    provider: LLMProvider,
    *,
    request_delay_seconds: float = 0,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Extract one structured result per candidate and merge immutable source metadata."""
    outputs: list[dict[str, Any]] = []
    total = len(candidates)
    for index, candidate in enumerate(candidates, 1):
        if progress:
            progress(f"Extract Pain Signal {index}/{total}: {candidate.get('title', 'unknown')}")
        try:
            extracted = provider.generate_json(
                prompt=build_pain_prompt(candidate),
                schema=PAIN_SIGNAL_SCHEMA,
                purpose="pain_extraction",
            )
        except ProviderError as exc:
            raise ProviderError(
                f"Pain Signal extraction failed for item {index} "
                f"({candidate.get('url', 'unknown')}): {exc}"
            ) from exc

        normalized = _normalize_extraction(extracted)
        outputs.append(
            {
                "source": _unknown(candidate.get("source")),
                "source_title": _unknown(candidate.get("title")),
                "source_url": _unknown(candidate.get("url")),
                "source_date": _unknown(candidate.get("created_at")),
                **normalized,
                "raw_data_count": int(candidate.get("raw_data_count", 0) or 0),
                "is_sentinel": candidate.get("is_sentinel") is True,
                "_source_text": (
                    f"{_unknown(candidate.get('title'))}\n"
                    f"{_unknown(candidate.get('text'))}"
                ),
            }
        )
        if request_delay_seconds > 0 and index < total:
            time.sleep(request_delay_seconds)
    return outputs


def _normalize_extraction(extracted: object) -> dict[str, Any]:
    source = extracted if isinstance(extracted, dict) else {}
    strength = _unknown(source.get("evidence_strength")).upper()
    if strength not in {"STRONG", "MEDIUM", "WEAK", "UNKNOWN"}:
        strength = "UNKNOWN"
    return {
        "target_user": _unknown(source.get("target_user")),
        "pain_statement": _unknown(source.get("pain_statement")),
        "current_solution": _unknown(source.get("current_solution")),
        "current_solution_problem": _unknown(source.get("current_solution_problem")),
        "workaround": _unknown(source.get("workaround")),
        "payment_signal": _unknown(source.get("payment_signal")),
        "economic_cost": _unknown(source.get("economic_cost")),
        "frequency_signal": _unknown(source.get("frequency_signal")),
        "evidence_summary": _unknown(source.get("evidence_summary")),
        "evidence_strength": strength,
        "possible_category": _unknown(source.get("possible_category")),
        "signal_type": _unknown(source.get("signal_type")).upper(),
        "qualification_evidence": _unknown(source.get("qualification_evidence")),
        "qualification_basis": _allowed_list(
            source.get("qualification_basis"), QUALIFICATION_BASES
        ),
        "qualifies_pain_signal": source.get("qualifies_pain_signal") is True,
        "exclusion_reason": _unknown(source.get("exclusion_reason")),
    }


def _allowed_list(value: object, allowed: tuple[str, ...]) -> list[str]:
    values = value if isinstance(value, list) else []
    allowed_set = set(allowed)
    output: list[str] = []
    for item in values:
        normalized = _unknown(item).upper()
        if normalized in allowed_set and normalized not in output:
            output.append(normalized)
    return output


def _unknown(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    if text.casefold() in {
        "", "unknown", "未知", "不明", "沒有", "没有", "無", "无",
        "n/a", "na", "none", "null",
    }:
        return "unknown"
    return text
