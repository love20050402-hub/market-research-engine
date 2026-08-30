"""Deterministic 100-point Opportunity Score and risk rules."""

from __future__ import annotations

import re
from typing import Any


COMPONENT_MAX: dict[str, int] = {
    "pain_severity": 20,
    "payment_evidence": 20,
    "durability": 15,
    "solution_gap": 15,
    "channel_accessibility": 10,
    "low_cost_entry": 10,
    "validation_speed": 5,
    "small_team_fit": 5,
}


def calculate_score(
    candidate: dict[str, Any],
    source_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate all eight components from validated evidence; no LLM scores are accepted."""
    risks = calculate_risks(candidate, source_signals)
    evidence_count = len(source_signals)
    strengths = [_unknown(signal.get("evidence_strength")).upper() for signal in source_signals]
    has_economic_cost = any(_known(signal.get("economic_cost")) for signal in source_signals)
    has_frequency = any(_known(signal.get("frequency_signal")) for signal in source_signals)
    has_workaround = any(_known(signal.get("workaround")) for signal in source_signals)
    solution_problem_count = sum(
        _known(signal.get("current_solution_problem")) for signal in source_signals
    )
    workaround_count = sum(_known(signal.get("workaround")) for signal in source_signals)
    payment_signal_count = sum(_known(signal.get("payment_signal")) for signal in source_signals)
    economic_cost_count = sum(_known(signal.get("economic_cost")) for signal in source_signals)

    strength_base = max(
        ({"STRONG": 10, "MEDIUM": 7, "WEAK": 3}.get(value, 0) for value in strengths),
        default=0,
    )
    pain_severity = strength_base
    pain_severity += 4 if has_economic_cost else 0
    pain_severity += 3 if has_frequency else 0
    pain_severity += 2 if has_workaround else 0
    pain_severity += min(2, max(0, evidence_count - 1))

    # The model extracts semantics, but cannot choose the commercial-evidence tier.
    # DIRECT requires an explicit, non-speculative payment statement or monetary loss
    # in a validated source signal. Generic time/effort costs are WEAK evidence only.
    direct_payment_count = sum(
        _is_direct_payment_evidence(signal.get("payment_signal"))
        for signal in source_signals
    )
    direct_monetary_cost_count = sum(
        _is_direct_monetary_cost(signal.get("economic_cost"))
        for signal in source_signals
    )
    payment_summary_denies_evidence = _denies_payment_evidence(
        candidate.get("payment_evidence")
    )
    direct_evidence_count = direct_payment_count + direct_monetary_cost_count
    weak_evidence_count = sum(
        _is_weak_commercial_evidence(signal.get("payment_signal"))
        or _is_weak_commercial_evidence(signal.get("economic_cost"))
        for signal in source_signals
    )
    payment_level = (
        "NONE"
        if payment_summary_denies_evidence
        else "DIRECT"
        if direct_evidence_count > 0
        else "WEAK"
        if weak_evidence_count > 0
        else "NONE"
    )
    if payment_level == "NONE":
        payment_evidence = 0
    elif payment_level == "WEAK":
        payment_evidence = 5
    else:
        payment_evidence = min(
            20,
            10
            + min(6, direct_evidence_count * 3)
            + (2 if direct_monetary_cost_count > 0 else 0),
        )

    if not _known(candidate.get("durability")):
        durability = 0
    else:
        durability = 7
        durability += 3 if evidence_count >= 2 else 0
        durability += 3 if has_frequency else 0
        durability += 2 if risks["hype_dependency"] == "LOW" else 0
        if risks["hype_dependency"] == "HIGH":
            durability = min(durability, 5)

    has_current_solution = bool(_known_list(candidate.get("current_alternatives")))
    if not _known(candidate.get("alternative_weakness")):
        solution_gap = 0
    else:
        solution_gap = 4
        solution_gap += min(4, solution_problem_count * 2)
        solution_gap += min(4, workaround_count * 2)
        solution_gap += 2 if evidence_count >= 2 else 0
        solution_gap += 1 if payment_signal_count > 0 and solution_problem_count > 0 else 0
    if not has_current_solution:
        solution_gap = min(solution_gap, 5)
    elif solution_problem_count == 0 and workaround_count == 0:
        solution_gap = min(solution_gap, 5)

    if not _known(candidate.get("channel")):
        channel_accessibility = 0
    else:
        channel_accessibility = 5
        channel_accessibility += 2 if _known(candidate.get("target_customer")) else 0
        channel_accessibility += 2 if _contains_any(
            candidate.get("channel"),
            ("hn", "社群", "community", "search", "搜尋", "email", "名單", "marketplace"),
        ) else 0
        channel_accessibility += 1 if evidence_count >= 2 else 0

    if not _known(candidate.get("low_cost_entry")):
        low_cost_entry = 0
    elif risks["capital_risk"] == "HIGH":
        low_cost_entry = 2
    else:
        low_cost_entry = 5
        low_cost_entry += 2 if _known(candidate.get("suggested_solution_direction")) else 0
        low_cost_entry += 2 if _contains_any(
            candidate.get("low_cost_entry"),
            ("低成本", "不需庫存", "不需要庫存", "small", "manual", "人工驗證"),
        ) else 0
        low_cost_entry += 1 if risks["technical_complexity"] == "LOW" else 0

    if not _known(candidate.get("validation_method")):
        validation_speed = 0
    else:
        validation_speed = 2
        validation_speed += 2 if _contains_any(
            candidate.get("validation_method"),
            ("7–14", "7-14", "訪談", "interview", "測試", "test", "兩週", "14 天"),
        ) else 0
        validation_speed += 1 if _known(candidate.get("channel")) else 0

    if risks["capital_risk"] == "HIGH" or risks["technical_complexity"] == "HIGH":
        small_team_fit = 2
    elif _known(candidate.get("suggested_solution_direction")):
        small_team_fit = 5 if risks["technical_complexity"] == "LOW" else 4
    else:
        small_team_fit = 2

    score = {
        "pain_severity": _clamp(pain_severity, 20),
        "payment_evidence": _clamp(payment_evidence, 20),
        "durability": _clamp(durability, 15),
        "solution_gap": _clamp(solution_gap, 15),
        "channel_accessibility": _clamp(channel_accessibility, 10),
        "low_cost_entry": _clamp(low_cost_entry, 10),
        "validation_speed": _clamp(validation_speed, 5),
        "small_team_fit": _clamp(small_team_fit, 5),
    }
    total = sum(score.values())
    critical_unknowns = _critical_unknowns(
        candidate,
        direct_payment_count=direct_payment_count,
        has_economic_cost=has_economic_cost,
        has_frequency=has_frequency,
        has_current_solution=has_current_solution,
        payment_summary_denies_evidence=payment_summary_denies_evidence,
    )
    critical_unknown_count = sum(
        value == "UNKNOWN" for value in critical_unknowns.values()
    )
    commercial_evidence_weak = payment_evidence <= 5
    team_mismatch = score["low_cost_entry"] <= 3 and score["small_team_fit"] <= 2
    confidence = str(candidate.get("confidence", "LOW")).strip().upper()
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        confidence = "LOW"
    decision = _final_decision(
        total=total,
        score=score,
        evidence_count=evidence_count,
        critical_unknown_count=critical_unknown_count,
        commercial_evidence_weak=commercial_evidence_weak,
        team_mismatch=team_mismatch,
        confidence=confidence,
    )
    return {
        "score_breakdown": score,
        "opportunity_score": total,
        "decision": decision,
        "payment_evidence_level": payment_level,
        "commercial_evidence_weak": commercial_evidence_weak,
        "critical_unknowns": critical_unknowns,
        "critical_unknown_count": critical_unknown_count,
        "team_mismatch": team_mismatch,
        "risks": risks,
    }


def _final_decision(
    *,
    total: int,
    score: dict[str, int],
    evidence_count: int,
    critical_unknown_count: int,
    commercial_evidence_weak: bool,
    team_mismatch: bool,
    confidence: str,
) -> str:
    """Apply evidence and team-fit gates after the analytical 100-point score."""
    if total < 55:
        return "REJECT"
    if team_mismatch:
        if (
            confidence == "LOW"
            or commercial_evidence_weak
            or critical_unknown_count >= 3
        ):
            return "MARKET INTERESTING / TEAM MISMATCH"
        return "MARKET GOOD / TEAM MISMATCH"
    if commercial_evidence_weak:
        return "WATCH"
    if critical_unknown_count >= 3:
        return "VALIDATE" if total >= 70 else "WATCH"
    if (
        total >= 80
        and evidence_count >= 2
        and score["payment_evidence"] >= 10
        and score["solution_gap"] > 5
        and score["low_cost_entry"] >= 6
        and score["small_team_fit"] >= 4
    ):
        return "DO NOW"
    if total >= 70:
        return "VALIDATE"
    return "WATCH"


def calculate_risks(
    candidate: dict[str, Any],
    source_signals: list[dict[str, Any]],
) -> dict[str, str]:
    """Assign all six risk flags using transparent keyword/evidence rules."""
    values: list[object] = [
        candidate.get("opportunity_name"),
        candidate.get("target_customer"),
        candidate.get("core_problem"),
        candidate.get("suggested_solution_direction"),
        candidate.get("channel"),
    ]
    for signal in source_signals:
        values.extend(
            [
                signal.get("source_title"),
                signal.get("pain_statement"),
                signal.get("current_solution"),
            ]
        )
    text = " ".join(_unknown(value) for value in values).casefold()

    if _contains_any(text, ("hype", "爆紅", "迷因", "meme", "nft")):
        hype = "HIGH"
    elif _contains_any(text, (" ai ", "llm", "chatgpt", "claude", "vibe", "crypto")):
        hype = "MEDIUM"
    else:
        hype = "LOW"

    if _contains_any(
        text,
        ("chatgpt", "claude", "linkedin", "google", "amazon", "youtube", "tiktok", "x.com"),
    ):
        platform = "HIGH"
    elif _contains_any(text, ("platform", "api", "plugin", "extension", "browser")):
        platform = "MEDIUM"
    else:
        platform = "LOW"

    if _contains_any(
        text,
        ("medical", "醫療", "legal", "法律", "investment", "投資", "drug", "藥物"),
    ):
        regulation = "HIGH"
    elif _contains_any(
        text,
        ("privacy", "個資", "personal data", "financial", "財務", "accounting", "會計"),
    ):
        regulation = "MEDIUM"
    else:
        regulation = "LOW"

    if _contains_any(
        text,
        (
            "manufacturing", "製造", "inventory", "庫存", "hardware", "硬體",
            "robot", "機器人", "factory", "工廠",
        ),
    ):
        capital = "HIGH"
    elif _known(candidate.get("low_cost_entry")):
        capital = "LOW"
    else:
        capital = "UNKNOWN"

    if _contains_any(
        text,
        (
            "kernel", "infrastructure", "基礎設施", "cybersecurity", "資安",
            "model training", "訓練模型", "hardware", "硬體",
        ),
    ):
        technical = "HIGH"
    elif _contains_any(
        text,
        ("api", "integration", "整合", "automation", "自動化", "plugin", "extension", "ai", "llm"),
    ):
        technical = "MEDIUM"
    elif _known(candidate.get("suggested_solution_direction")):
        technical = "LOW"
    else:
        technical = "UNKNOWN"

    if not _known(candidate.get("target_customer")):
        acquisition = "HIGH"
    elif not _known(candidate.get("channel")):
        acquisition = "UNKNOWN"
    elif _contains_any(candidate.get("channel"), ("公司名單", "directory", "email", "marketplace")):
        acquisition = "LOW"
    else:
        acquisition = "MEDIUM"

    return {
        "hype_dependency": hype,
        "platform_dependency": platform,
        "regulation_risk": regulation,
        "capital_risk": capital,
        "technical_complexity": technical,
        "customer_acquisition_difficulty": acquisition,
    }


def _known(value: object) -> bool:
    return _unknown(value).casefold() != "unknown"


def _known_list(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [text for item in values if (text := _unknown(item)) != "unknown"]


def _denies_payment_evidence(value: object) -> bool:
    text = _unknown(value).casefold()
    if text == "unknown":
        return False
    return _contains_any(
        text,
        (
            "未提及", "沒有具體", "沒有直接", "無具體", "無直接",
            "沒有金錢", "無金錢", "沒有付費", "無付費",
            "not mentioned", "no direct evidence", "no payment evidence",
            "no monetary evidence", "no specific monetary",
        ),
    )


def _critical_unknowns(
    candidate: dict[str, Any],
    *,
    direct_payment_count: int,
    has_economic_cost: bool,
    has_frequency: bool,
    has_current_solution: bool,
    payment_summary_denies_evidence: bool,
) -> dict[str, str]:
    willingness_known = (
        direct_payment_count > 0
        and not payment_summary_denies_evidence
        and not _key_unknown_mentions(
            candidate,
            ("付費", "願意花錢", "預算", "willingness", "willing to pay", "budget"),
        )
    )
    economic_cost_known = has_economic_cost and not _key_unknown_mentions(
        candidate,
        ("經濟成本", "金錢成本", "具體成本", "economic cost", "monetary cost"),
    )
    frequency_known = has_frequency and not _key_unknown_mentions(
        candidate,
        ("問題頻率", "頻率", "多久一次", "frequency", "how often"),
    )
    current_solution_known = has_current_solution and not _key_unknown_mentions(
        candidate,
        ("現有解法", "目前解法", "現在怎麼解決", "current solution"),
    )
    return {
        "willingness_to_pay": "KNOWN" if willingness_known else "UNKNOWN",
        "economic_cost": "KNOWN" if economic_cost_known else "UNKNOWN",
        "problem_frequency": "KNOWN" if frequency_known else "UNKNOWN",
        "current_solution": "KNOWN" if current_solution_known else "UNKNOWN",
    }


def _key_unknown_mentions(candidate: dict[str, Any], needles: tuple[str, ...]) -> bool:
    values = candidate.get("key_unknowns")
    items = values if isinstance(values, list) else [values]
    text = " ".join(_unknown(item) for item in items)
    return _contains_any(text, needles)


def _is_direct_payment_evidence(value: object) -> bool:
    text = _unknown(value).casefold()
    if text == "unknown" or _is_speculative(text):
        return False
    return _contains_financial_term(
        text,
        (
            "付費", "支付", "已付", "付錢", "預算", "願意付", "願付",
            "paid", "paying", "pay for", "budget", "willing to pay",
            "subscription", "訂閱", "bill", "帳單",
        ),
    )


def _is_direct_monetary_cost(value: object) -> bool:
    text = _unknown(value).casefold()
    if text == "unknown" or _is_speculative(text):
        return False
    return _contains_financial_term(
        text,
        (
            "$", "usd", "dollar", "€", "eur", "£", "gbp", "¥", "jpy",
            "費用", "花費金錢", "支付", "成本增加", "金錢損失", "營收損失",
            "revenue loss", "lost revenue", "financial loss", "monetary",
            "fee", "cost", "price", "bill", "subscription", "訂閱",
        ),
    )


def _is_weak_commercial_evidence(value: object) -> bool:
    text = _unknown(value).casefold()
    if text == "unknown":
        return False
    return _contains_financial_term(
        text,
        (
            "$", "usd", "dollar", "€", "eur", "£", "gbp", "¥", "jpy",
            "付費", "支付", "付錢", "預算", "願意付", "願付", "費用", "成本",
            "支出", "金錢", "營收", "paid", "pay", "budget", "fee", "cost",
            "price", "bill", "subscription", "訂閱", "revenue", "monetary",
        ),
    )


def _is_speculative(text: str) -> bool:
    return _contains_any(
        text,
        (
            "暗示", "推測", "可能", "潛在", "擔心", "恐怕", "假設",
            "implies", "suggests", "potential", "possibly", "might", "may ",
            "could ", "assume",
        ),
    )


def _contains_financial_term(text: str, terms: tuple[str, ...]) -> bool:
    """Match English financial words on boundaries; CJK/currency terms by substring."""
    for term in terms:
        folded = term.casefold()
        if folded.isascii() and any(character.isalpha() for character in folded):
            if re.search(rf"(?<![a-z]){re.escape(folded)}(?![a-z])", text):
                return True
        elif folded in text:
            return True
    return False


def _contains_any(value: object, needles: tuple[str, ...]) -> bool:
    text = _unknown(value).casefold()
    return any(needle.casefold() in text for needle in needles)


def _clamp(value: int, maximum: int) -> int:
    return max(0, min(maximum, int(value)))


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
