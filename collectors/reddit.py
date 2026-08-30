"""Reddit submission search collector using compliant app-only OAuth."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from collectors.base import CollectorError, validate_normalized_record


class RedditCollector:
    """Fetch a small, deduplicated set of evidence-oriented Reddit submissions."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        opener: Callable[..., Any] = urlopen,
        environ: dict[str, str] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        environment = environ if environ is not None else os.environ
        self.token_endpoint = str(
            config.get("token_endpoint", "https://www.reddit.com/api/v1/access_token")
        )
        self.api_base_url = str(config.get("api_base_url", "https://oauth.reddit.com"))
        self.search_path = str(config.get("search_path", "/search"))
        self.queries = [
            str(query).strip() for query in config.get("queries", []) if str(query).strip()
        ]
        self.limit_per_query = max(1, min(100, int(config.get("limit_per_query", 10))))
        self.max_records = max(1, int(config.get("max_records", 75)))
        self.sort = str(config.get("sort", "relevance"))
        self.time_filter = str(config.get("time_filter", "year"))
        self.timeout_seconds = float(config.get("timeout_seconds", 30))
        self.request_delay_seconds = max(
            0.0, float(config.get("request_delay_seconds", 1.0))
        )
        self.skip_over_18 = config.get("skip_over_18", True) is not False
        self.client_id = environment.get(str(config.get("client_id_env", "REDDIT_CLIENT_ID")), "")
        self.client_secret = environment.get(
            str(config.get("client_secret_env", "REDDIT_CLIENT_SECRET")), ""
        )
        self.access_token = environment.get(
            str(config.get("access_token_env", "REDDIT_ACCESS_TOKEN")), ""
        )
        self.user_agent = environment.get(
            str(config.get("user_agent_env", "REDDIT_USER_AGENT")),
            str(config.get("user_agent", "windows:market-research-engine:v1.1")),
        )
        self._opener = opener
        self._sleep = sleeper

    @property
    def source_name(self) -> str:
        return "reddit"

    @property
    def request_description(self) -> str:
        return f"{self.api_base_url}{self.search_path} ({len(self.queries)} queries)"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.queries:
            raise CollectorError("Reddit collector has no configured search queries.")
        token = self.access_token or self._request_access_token()
        deduplicated: dict[str, dict[str, Any]] = {}
        for index, query in enumerate(self.queries):
            for item in self._search(query, token):
                object_id = _clean_text(item.get("name") or item.get("id"))
                if not object_id:
                    continue
                existing = deduplicated.get(object_id)
                if existing is None:
                    item["_search_keywords"] = [query]
                    deduplicated[object_id] = item
                else:
                    keywords = existing.setdefault("_search_keywords", [])
                    if isinstance(keywords, list) and query not in keywords:
                        keywords.append(query)
                if len(deduplicated) >= self.max_records:
                    return list(deduplicated.values())
            if self.request_delay_seconds > 0 and index < len(self.queries) - 1:
                self._sleep(self.request_delay_seconds)
        return list(deduplicated.values())

    def _request_access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise CollectorError(
                "Reddit OAuth is not configured. Create a Reddit app, then set "
                "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and a descriptive "
                "REDDIT_USER_AGENT in .env. Alternatively set a current "
                "REDDIT_ACCESS_TOKEN."
            )
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        body = urlencode({"grant_type": "client_credentials"}).encode("ascii")
        request = Request(
            self.token_endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        payload = self._request_json(request, purpose="OAuth token")
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise CollectorError("Reddit OAuth response did not contain an access_token.")
        return token

    def _search(self, query: str, token: str) -> list[dict[str, Any]]:
        params = urlencode(
            {
                "q": f'"{query}"',
                "restrict_sr": "false",
                "sort": self.sort,
                "t": self.time_filter,
                "type": "link",
                "limit": self.limit_per_query,
                "raw_json": 1,
            }
        )
        request = Request(
            f"{self.api_base_url}{self.search_path}?{params}",
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        payload = self._request_json(request, purpose=f"search query {query!r}")
        data = payload.get("data") if isinstance(payload, dict) else None
        children = data.get("children") if isinstance(data, dict) else None
        if not isinstance(children, list):
            raise CollectorError("Reddit search response did not contain data.children.")
        output: list[dict[str, Any]] = []
        for child in children:
            item = child.get("data") if isinstance(child, dict) else None
            if not isinstance(item, dict):
                continue
            if self.skip_over_18 and item.get("over_18") is True:
                continue
            if item.get("removed_by_category") or item.get("selftext") in {"[deleted]", "[removed]"}:
                continue
            output.append(dict(item))
        return output

    def _request_json(self, request: Request, *, purpose: str) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except OSError:
                pass
            hint = (
                " Check Reddit OAuth credentials and app access."
                if exc.code in {401, 403}
                else " Retry after the Reddit rate-limit window."
                if exc.code == 429
                else ""
            )
            raise CollectorError(
                f"Reddit {purpose} returned HTTP {exc.code}.{hint} {detail}".strip()
            ) from exc
        except URLError as exc:
            raise CollectorError(f"Unable to reach Reddit for {purpose}: {exc.reason}") from exc
        except (TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise CollectorError(f"Unable to read Reddit {purpose} response: {exc}") from exc
        if not isinstance(payload, dict):
            raise CollectorError(f"Reddit {purpose} response was not a JSON object.")
        return payload

    def normalize(self, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in items:
            fullname = _clean_text(item.get("name"))
            item_id = _clean_text(item.get("id"))
            permalink = _clean_text(item.get("permalink"))
            keywords = item.get("_search_keywords")
            search_keyword = "; ".join(str(value) for value in keywords) if isinstance(keywords, list) else "unknown"
            record = {
                "source": self.source_name,
                "source_id": f"reddit:{fullname or item_id}" if (fullname or item_id) else "unknown",
                "source_context": f"r/{_clean_text(item.get('subreddit')) or 'unknown'}",
                "title": _clean_text(item.get("title")),
                "text": _clean_text(item.get("selftext")),
                "author": _clean_text(item.get("author")) or "unknown",
                "created_at": _iso_utc(item.get("created_utc")),
                "url": f"https://www.reddit.com{permalink}" if permalink else "unknown",
                "search_keyword": search_keyword or "unknown",
                "num_comments": _safe_int(item.get("num_comments")),
                "points": _safe_int(item.get("score")),
                "object_id": fullname or item_id or "unknown",
            }
            validate_normalized_record(record)
            records.append(record)
        return records

    def fetch_and_normalize(self) -> list[dict[str, Any]]:
        return self.normalize(self.fetch())


def _clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso_utc(value: object) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return "unknown"
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
