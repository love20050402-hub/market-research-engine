"""Low-recall-loss candidate prefilter ported from the completed workflow."""

from __future__ import annotations

from typing import Any


PHRASE_GROUPS: dict[str, tuple[str, ...]] = {
    "payment": (
        "pay", "paid", "budget", "pricing", "price", "cost", "expensive",
        "subscription", "contractor", "consultant", "hire", "revenue", "loss",
        "lost money",
    ),
    "pain": (
        "frustrated", "frustrating", "difficult", "hard to", "cannot", "can't",
        "struggling", "painful", "annoying", "tedious", "problem", "blocked",
        "waste time", "wasting time", "time-consuming", "time consuming",
        "i hate", "takes me hours", "there has to be a better way",
    ),
    "workaround": (
        "manual", "manually", "workaround", "spreadsheet", "copy paste",
        "copy-paste", "script", "by hand", "hours", "every day", "every week",
        "daily", "weekly",
    ),
    "seeking": (
        "looking for", "alternative", "recommend", "what do you use", "is there a",
        "does anyone know", "wish there was", "wish there were", "how do you",
        "how can i", "how do you deal with", "is there a tool",
        "looking for an alternative", "does anyone offer", "currently using",
        "willing to pay", "how much would you pay",
    ),
}


def candidate_prefilter(
    records: list[dict[str, Any]],
    *,
    max_candidates: int = 20,
) -> list[dict[str, Any]]:
    """Prioritize evidence-bearing records without treating priority as opportunity score."""
    raw_data_count = len(records)
    candidates: list[dict[str, Any]] = []

    for record in records:
        title = str(record.get("title") or "").strip()
        text = str(record.get("text") or "").strip()
        combined = f"{title} {text}".strip()
        if len(title) < 8 or len(combined) < 50:
            continue

        lower = combined.lower()
        matches = {
            group: [phrase for phrase in phrases if phrase in lower]
            for group, phrases in PHRASE_GROUPS.items()
        }
        priority = 0
        priority += min(len(matches["payment"]), 2) * 3
        priority += min(len(matches["pain"]), 3) * 2
        priority += min(len(matches["workaround"]), 3) * 2
        priority += min(len(matches["seeking"]), 2)
        priority += 1 if _safe_int(record.get("num_comments")) >= 5 else 0

        evidence_terms = "; ".join(
            f"{group}: {', '.join(values[:4])}"
            for group, values in matches.items()
            if values
        ) or "unknown"

        candidates.append(
            {
                **record,
                "title": title,
                "text": text,
                "num_comments": _safe_int(record.get("num_comments")),
                "points": _safe_int(record.get("points")),
                "raw_data_count": raw_data_count,
                "candidate_priority_score": priority,
                "candidate_evidence_terms": evidence_terms,
                "is_sentinel": False,
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["candidate_priority_score"]),
            -int(item["num_comments"]),
            -int(item["points"]),
        )
    )

    if not candidates:
        source = str(records[0].get("source") or "unknown") if records else "unknown"
        source_context = (
            str(records[0].get("source_context") or "unknown") if records else "unknown"
        )
        return [
            {
                "source": source,
                "source_id": "unknown",
                "source_context": source_context,
                "title": "unknown",
                "text": "No source item had enough text for evidence extraction.",
                "author": "unknown",
                "created_at": "unknown",
                "url": "unknown",
                "search_keyword": "wish",
                "num_comments": 0,
                "points": 0,
                "object_id": "unknown",
                "raw_data_count": raw_data_count,
                "candidate_priority_score": 0,
                "candidate_evidence_terms": "unknown",
                "is_sentinel": True,
                "candidate_rank": 1,
            }
        ]

    selected = candidates[: max(0, int(max_candidates))]
    return [{**item, "candidate_rank": index} for index, item in enumerate(selected, 1)]


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
