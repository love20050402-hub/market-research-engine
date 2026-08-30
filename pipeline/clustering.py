"""Cross-signal Opportunity Clustering using the completed workflow rules."""

from __future__ import annotations

import json
from typing import Any

from providers.llm_provider import LLMProvider


CLUSTER_PROPERTIES: dict[str, Any] = {
    "opportunity_name": {"type": "string"},
    "target_customer": {"type": "string"},
    "core_problem": {"type": "string"},
    "source_signal_ids": {"type": "array", "items": {"type": "string"}},
    "strongest_evidence": {"type": "string"},
    "repeated_patterns": {"type": "array", "items": {"type": "string"}},
    "current_alternatives": {"type": "array", "items": {"type": "string"}},
    "alternative_weakness": {"type": "string"},
    "payment_evidence": {"type": "string"},
    "payment_evidence_level": {
        "type": "string",
        "enum": ["NONE", "WEAK", "DIRECT"],
    },
    "suggested_solution_direction": {"type": "string"},
    "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
    "key_unknowns": {"type": "array", "items": {"type": "string"}},
    "why_it_matters": {"type": "string"},
    "channel": {"type": "string"},
    "durability": {"type": "string"},
    "low_cost_entry": {"type": "string"},
    "validation_method": {"type": "string"},
}

CLUSTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": CLUSTER_PROPERTIES,
                "required": list(CLUSTER_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


def build_clustering_prompt(pain_signals: list[dict[str, Any]]) -> str:
    signals_json = json.dumps(pain_signals, ensure_ascii=False, indent=2)
    return f"""你是保守的商業問題聚類分析員。輸入只包含已通過可引用證據稽核、且 signal_type 為 COMMERCIAL_PAIN / OPERATIONAL_PAIN / SOLUTION_REQUEST / WORKAROUND / PAYMENT_SIGNAL 的 HN 與 Reddit Pain Signals。

Pain Signals JSON：
{signals_json}

任務：
1. 將描述同一受眾、同一核心工作／損失機制的訊號合併。只因同屬「生產力」「AI」「開發工具」等大類，不足以合併。
2. 不可把每篇文章機械式變成一個 opportunity。單一訊號只有在證據 STRONG，且含直接付費、明確 economic_cost 或具體高成本 workaround 時，才可形成暫定 cluster。
3. clusters 數量可以是 0；不要為了湊 10 個而降低標準。
4. 每個 cluster 的 source_signal_ids 只能引用輸入中的 signal_id。所有證據欄位必須可追溯到這些 signal。
5. 同一 signal_id 只能屬於一個 cluster。若 HN 與 Reddit 描述同一受眾與同一問題機制，應合併為同一 cluster；不要只因來源不同拆開。
6. 不得加入外部市場知識，不得幻想市場規模、營收、成熟競品、付費意願或問題頻率。沒有證據就寫 "unknown" 或空陣列。
7. opportunity_name、target_customer、core_problem 與所有摘要使用繁體中文。
8. suggested_solution_direction 只能是初步驗證方向，不可展開成完整 SaaS 規格。

payment_evidence_level 只可為 NONE / WEAK / DIRECT：
- NONE：沒有任何付費或經濟成本證據。
- WEAK：只有間接成本、未量化時間成本或可能的付費替代線索。
- DIRECT：原文直接提到已付費、找付費工具、預算、願付費，或具體量化經濟損失。

confidence 只可為 HIGH / MEDIUM / LOW：HIGH 通常需 3 個以上獨立訊號且含直接商業證據；MEDIUM 通常需 2 個以上一致訊號；其餘為 LOW。

不要輸出任何分數或 risk flags。分數與風險將由後續 deterministic Python 規則計算；你只負責語意抽取與 clustering。

請輸出 schema 要求的 clusters 陣列。沒有合格 cluster 時輸出 {{"clusters": []}}。"""


def cluster_pain_signals(
    pain_signals: list[dict[str, Any]],
    provider: LLMProvider,
) -> dict[str, Any]:
    """Run one cross-signal clustering call, never one opportunity per article by default."""
    if not pain_signals:
        return {"clusters": []}
    output = provider.generate_json(
        prompt=build_clustering_prompt(pain_signals),
        schema=CLUSTER_SCHEMA,
        purpose="opportunity_clustering",
    )
    clusters = output.get("clusters") if isinstance(output, dict) else None
    return {"clusters": clusters if isinstance(clusters, list) else []}
