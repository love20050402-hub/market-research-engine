"""Hacker News collector using the public Algolia API."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from collectors.base import CollectorError, validate_normalized_record


def clean_text(value: object) -> str:
    """Normalize HTML-bearing HN text without inventing missing content."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class HackerNewsCollector:
    """Fetch and normalize the same Ask HN query used by the n8n workflow."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.endpoint = str(config["endpoint"])
        self.query = str(config.get("query", "wish"))
        self.tags = str(config.get("tags", "ask_hn"))
        self.hits_per_page = int(config.get("hits_per_page", 20))
        self.timeout_seconds = float(config.get("timeout_seconds", 30))
        self.user_agent = str(config.get("user_agent", "market-research-engine/1.0"))
        self._opener = opener

    @property
    def source_name(self) -> str:
        return "hackernews"

    @property
    def request_description(self) -> str:
        return self.request_url

    @property
    def request_url(self) -> str:
        params = urlencode(
            {
                "query": self.query,
                "tags": self.tags,
                "hitsPerPage": self.hits_per_page,
            }
        )
        return f"{self.endpoint}?{params}"

    def fetch(self) -> list[dict[str, Any]]:
        request = Request(
            self.request_url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise CollectorError(f"HN API returned HTTP {exc.code}.") from exc
        except URLError as exc:
            raise CollectorError(f"Unable to reach HN API: {exc.reason}") from exc
        except (TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise CollectorError(f"Unable to read HN API response: {exc}") from exc

        hits = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(hits, list):
            raise CollectorError("HN API response did not contain a valid 'hits' list.")
        return [hit for hit in hits if isinstance(hit, dict)]

    def normalize(self, hits: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for hit in hits:
            object_id = clean_text(hit.get("objectID"))
            title = clean_text(hit.get("title") or hit.get("story_title"))
            text = clean_text(hit.get("story_text") or hit.get("comment_text"))
            hn_url = (
                f"https://news.ycombinator.com/item?id={object_id}"
                if object_id
                else "unknown"
            )
            record = {
                    "source": self.source_name,
                    "source_id": f"hn:{object_id}" if object_id else "unknown",
                    "source_context": self.tags,
                    "title": title,
                    "text": text,
                    "author": clean_text(hit.get("author")) or "unknown",
                    "created_at": clean_text(hit.get("created_at")) or "unknown",
                    "url": hn_url,
                    "search_keyword": self.query,
                    "num_comments": _safe_int(hit.get("num_comments")),
                    "points": _safe_int(hit.get("points")),
                    "object_id": object_id or "unknown",
                }
            validate_normalized_record(record)
            records.append(record)
        return records

    def fetch_and_normalize(self) -> list[dict[str, Any]]:
        return self.normalize(self.fetch())


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
