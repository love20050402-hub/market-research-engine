"""Shared collector interface and normalized-record contract."""

from __future__ import annotations

from typing import Any, Protocol


NORMALIZED_RECORD_FIELDS = (
    "source",
    "source_id",
    "source_context",
    "title",
    "text",
    "author",
    "created_at",
    "url",
    "search_keyword",
    "num_comments",
    "points",
    "object_id",
)


class CollectorError(RuntimeError):
    """Raised when a configured public-data collector cannot complete."""


class Collector(Protocol):
    """Minimal interface shared by Hacker News and Reddit collectors."""

    @property
    def source_name(self) -> str: ...

    @property
    def request_description(self) -> str: ...

    def fetch_and_normalize(self) -> list[dict[str, Any]]: ...


def validate_normalized_record(record: dict[str, Any]) -> None:
    """Fail fast if a collector drifts from the common downstream schema."""
    actual = set(record)
    expected = set(NORMALIZED_RECORD_FIELDS)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise CollectorError(
            f"Normalized record schema mismatch; missing={missing}, extra={extra}."
        )
