"""Deterministic Pain Signal and Opportunity validation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pipeline.scoring import calculate_score


PAIN_FIELDS = (
    "source",
    "source_title",
    "source_url",
    "source_date",
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
)

ALLOWED_STRENGTH = {"STRONG", "MEDIUM", "WEAK", "UNKNOWN"}
CLUSTERABLE_SIGNAL_TYPES = {
    "COMMERCIAL_PAIN",
    "OPERATIONAL_PAIN",
    "SOLUTION_REQUEST",
    "WORKAROUND",
    "PAYMENT_SIGNAL",
}
ALLOWED_QUALIFICATION_BASES = {
    "REPETITIVE_MANUAL_WORK",
    "MEASURABLE_TIME_COST",
    "MONETARY_COST",
    "OPERATIONAL_LOSS",
    "ACTIVE_SOLUTION_SEEKING",
    "WORKAROUND_EVIDENCE",
    "EXISTING_SOLUTION_DISSATISFACTION",
    "EXPLICIT_PAYMENT_OR_BUDGET",
}


def prepare_pain_signals(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the completed workflow's Pain Signal evidence gate and stable IDs."""
    prepared: list[dict[str, Any]] = []
    for item in extractions:
        if item.get("is_sentinel") is True or item.get("qualifies_pain_signal") is not True:
            continue
        signal = {field: _unknown(item.get(field)) for field in PAIN_FIELDS}
        qualification_basis = _allowed_values(
            item.get("qualification_basis"), ALLOWED_QUALIFICATION_BASES
        )
        strength = signal["evidence_strength"].upper()
        signal["evidence_strength"] = strength if strength in ALLOWED_STRENGTH else "UNKNOWN"
        signal_type = signal["signal_type"].upper()
        signal["signal_type"] = signal_type
        if (
            signal["source"] == "unknown"
            or signal["source_url"] == "unknown"
            or signal["pain_statement"] == "unknown"
            or signal["target_user"] == "unknown"
            or signal["evidence_strength"] == "UNKNOWN"
            or signal_type not in CLUSTERABLE_SIGNAL_TYPES
            or not qualification_basis
            or not _quote_supported(
                signal["qualification_evidence"], item.get("_source_text")
            )
        ):
            continue
        signal["qualification_basis"] = qualification_basis
        prepared.append(signal)

    return [
        {"signal_id": f"PS-{index:03d}", **signal}
        for index, signal in enumerate(prepared, 1)
    ]


def validate_opportunities(
    raw_output: dict[str, Any],
    pain_signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject invented sources, recalculate scores, normalize risks, and gate singletons."""
    signal_by_id = {signal["signal_id"]: signal for signal in pain_signals}
    raw_clusters = raw_output.get("clusters") if isinstance(raw_output, dict) else []
    if not isinstance(raw_clusters, list):
        raw_clusters = []

    clusters: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    used_signal_ids: set[str] = set()
    for candidate in raw_clusters:
        if not isinstance(candidate, dict):
            continue
        name = _unknown(candidate.get("opportunity_name"))
        name_key = name.casefold()
        if name == "unknown" or name_key in seen_names:
            continue

        source_ids = _deduplicate(
            source_id
            for source_id in _as_list(candidate.get("source_signal_ids"))
            if source_id in signal_by_id and source_id not in used_signal_ids
        )
        if not source_ids:
            continue
        source_signals = [signal_by_id[source_id] for source_id in source_ids]
        score_result = calculate_score(candidate, source_signals)
        sources = sorted({_unknown(signal.get("source")) for signal in source_signals})
        known_sources = [source for source in sources if source != "unknown"]
        source_diversity = (
            "CROSS_SOURCE_VALIDATED"
            if {"hackernews", "reddit"}.issubset(set(known_sources))
            else "HN_ONLY"
            if known_sources == ["hackernews"]
            else "REDDIT_ONLY"
            if known_sources == ["reddit"]
            else "UNKNOWN"
        )

        singleton_has_required_evidence = len(source_signals) > 1 or any(
            signal["evidence_strength"] == "STRONG"
            and (
                signal["payment_signal"] != "unknown"
                or signal["economic_cost"] != "unknown"
                or signal["workaround"] != "unknown"
            )
            for signal in source_signals
        )

        confidence = _unknown(candidate.get("confidence")).upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            confidence = "LOW"
        if score_result["critical_unknown_count"] >= 3:
            confidence = "LOW"
        patterns = _as_list(candidate.get("repeated_patterns")) or ["unknown"]
        alternatives = _as_list(candidate.get("current_alternatives")) or ["unknown"]
        key_unknowns = _as_list(candidate.get("key_unknowns")) or ["unknown"]
        cluster = {
            "opportunity_name": name,
            "target_customer": _unknown(candidate.get("target_customer")),
            "core_problem": _unknown(candidate.get("core_problem")),
            "evidence_count": len(source_signals),
            "strongest_evidence": _unknown(candidate.get("strongest_evidence")),
            "repeated_patterns": patterns,
            "current_alternatives": alternatives,
            "alternative_weakness": _unknown(candidate.get("alternative_weakness")),
            "payment_evidence": _unknown(candidate.get("payment_evidence")),
            "payment_evidence_level": score_result["payment_evidence_level"],
            "suggested_solution_direction": _unknown(
                candidate.get("suggested_solution_direction")
            ),
            "opportunity_score": score_result["opportunity_score"],
            "score_breakdown": score_result["score_breakdown"],
            "confidence": confidence,
            "key_unknowns": key_unknowns,
            "why_it_matters": _unknown(candidate.get("why_it_matters")),
            "channel": _unknown(candidate.get("channel")),
            "durability": _unknown(candidate.get("durability")),
            "low_cost_entry": _unknown(candidate.get("low_cost_entry")),
            "validation_method": _unknown(candidate.get("validation_method")),
            "decision": score_result["decision"],
            "commercial_evidence_weak": score_result["commercial_evidence_weak"],
            "critical_unknowns": score_result["critical_unknowns"],
            "critical_unknown_count": score_result["critical_unknown_count"],
            "team_mismatch": score_result["team_mismatch"],
            "source_signal_ids": source_ids,
            "source_urls": _deduplicate(signal["source_url"] for signal in source_signals),
            "source_diversity": source_diversity,
            "publish_eligible": (
                singleton_has_required_evidence
                and score_result["opportunity_score"] >= 55
            ),
            "risks": score_result["risks"],
        }
        clusters.append(cluster)
        seen_names.add(name_key)
        used_signal_ids.update(source_ids)

    clusters.sort(key=lambda item: (-item["opportunity_score"], -item["evidence_count"]))
    return clusters


def top_opportunities(clusters: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return [cluster for cluster in clusters if cluster.get("publish_eligible")][:limit]


def _as_list(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [text for item in values if (text := _unknown(item)) != "unknown"]


def _deduplicate(values: object) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:  # type: ignore[union-attr]
        text = str(value)
        if text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _allowed_values(value: object, allowed: set[str]) -> list[str]:
    values = value if isinstance(value, list) else []
    output: list[str] = []
    for item in values:
        normalized = _unknown(item).upper()
        if normalized in allowed and normalized not in output:
            output.append(normalized)
    return output


def _quote_supported(quote: object, source_text: object) -> bool:
    normalized_quote = _normalize_quote(quote).strip("\"'“”‘’ ")
    normalized_source = _normalize_quote(source_text)
    return len(normalized_quote) >= 8 and normalized_quote in normalized_source


def _normalize_quote(value: object) -> str:
    text = unicodedata.normalize("NFKC", _unknown(value)).casefold()
    return re.sub(r"\s+", " ", text).strip()


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
