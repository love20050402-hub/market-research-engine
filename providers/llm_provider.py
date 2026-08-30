"""Replaceable structured-JSON LLM provider interface and optional adapters."""

from __future__ import annotations

import copy
import json
import os
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Base error for LLM provider failures."""


class MissingCredentialError(ProviderError):
    """Raised when a selected provider has no configured API credential."""


class ProviderConfigurationError(ProviderError):
    """Raised when a requested provider is unsupported or misconfigured."""


class LLMProvider(ABC):
    """Minimal provider abstraction used by extraction and clustering."""

    @abstractmethod
    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        """Return one JSON object conforming to the requested schema."""


class GeminiProvider(LLMProvider):
    """Gemini GenerateContent REST adapter with structured JSON output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_base: str,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
        timeout_seconds: float = 120,
        max_retries: int = 2,
    ) -> None:
        if not api_key.strip():
            raise MissingCredentialError(
                "GEMINI_API_KEY is missing. Copy .env.example to .env and add your Gemini API key."
            )
        self.api_key = api_key.strip()
        self.model = model.removeprefix("models/")
        self.api_base = api_base.rstrip("/")
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        del purpose  # Provider transport does not branch on pipeline purpose.
        model_path = quote(self.model, safe="-._")
        url = f"{self.api_base}/models/{model_path}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-goog-api-key": self.api_key,
                "User-Agent": "market-research-engine/1.0",
            },
        )

        response_payload = self._request_with_retry(request)
        text = _extract_gemini_text(response_payload)
        try:
            parsed = json.loads(_strip_code_fence(text))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gemini returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Gemini structured response was not a JSON object.")
        return parsed

    def _request_with_retry(self, request: Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ProviderError("Gemini response body was not a JSON object.")
                return payload
            except HTTPError as exc:
                detail = _read_http_error(exc)
                last_error = ProviderError(f"Gemini API HTTP {exc.code}: {detail}")
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.max_retries:
                    raise last_error from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = ProviderError(f"Unable to reach Gemini API: {exc}")
                if attempt >= self.max_retries:
                    raise last_error from exc
            time.sleep(2**attempt)
        raise ProviderError(f"Gemini request failed: {last_error}")


class MockLLMProvider(LLMProvider):
    """Deterministic provider for tests; never use its output as market evidence."""

    def __init__(
        self,
        responses: Mapping[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._responses: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for purpose, items in (responses or {}).items():
            self._responses[purpose].extend(copy.deepcopy(items))

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        del prompt, schema
        if self._responses[purpose]:
            return copy.deepcopy(self._responses[purpose].popleft())
        if purpose == "opportunity_clustering":
            return {"clusters": []}
        return {
            "target_user": "unknown",
            "pain_statement": "unknown",
            "current_solution": "unknown",
            "current_solution_problem": "unknown",
            "workaround": "unknown",
            "payment_signal": "unknown",
            "economic_cost": "unknown",
            "frequency_signal": "unknown",
            "evidence_summary": "unknown",
            "evidence_strength": "UNKNOWN",
            "possible_category": "unknown",
            "qualifies_pain_signal": False,
            "exclusion_reason": "mock provider has no configured fixture",
        }


def create_provider(
    config: dict[str, Any],
    *,
    provider_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMProvider:
    """Create a provider without coupling pipeline modules to one AI vendor."""
    env = environ if environ is not None else os.environ
    selected = (
        provider_name
        or env.get("MARKET_RESEARCH_PROVIDER")
        or str(config.get("provider", "ollama"))
    ).strip().lower()

    if selected == "mock":
        return MockLLMProvider()
    if selected == "ollama":
        ollama = config.get("ollama")
        if not isinstance(ollama, dict):
            raise ProviderConfigurationError("config.yaml is missing llm.ollama settings.")
        from providers.ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=str(ollama.get("base_url", "http://127.0.0.1:11434")),
            model=str(ollama.get("model", "qwen3:8b")),
            temperature=float(ollama.get("temperature", 0)),
            context_window=int(ollama.get("context_window", 8192)),
            max_output_tokens=int(ollama.get("max_output_tokens", 4096)),
            timeout_seconds=float(ollama.get("timeout_seconds", 300)),
            max_retries=int(ollama.get("max_retries", 2)),
            keep_alive=str(ollama.get("keep_alive", "10m")),
            think=bool(ollama.get("think", False)),
        )
    if selected != "gemini":
        raise ProviderConfigurationError(
            f"Unsupported LLM provider '{selected}'. V1 supports local 'ollama' and "
            "deterministic 'mock'; 'gemini' remains an optional legacy adapter."
        )

    gemini = config.get("gemini")
    if not isinstance(gemini, dict):
        raise ProviderConfigurationError("config.yaml is missing llm.gemini settings.")
    api_key = str(env.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        raise MissingCredentialError(
            "GEMINI_API_KEY is missing. Copy .env.example to .env, set GEMINI_API_KEY, "
            "then run 'python run.py' again."
        )
    return GeminiProvider(
        api_key=api_key,
        model=str(gemini.get("model", "gemini-3.5-flash-lite")),
        api_base=str(
            gemini.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        ),
        temperature=float(gemini.get("temperature", 0.1)),
        max_output_tokens=int(gemini.get("max_output_tokens", 8192)),
        timeout_seconds=float(gemini.get("timeout_seconds", 120)),
        max_retries=int(gemini.get("max_retries", 2)),
    )


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Gemini response did not contain candidate text.") from exc
    if not text.strip():
        raise ProviderError("Gemini response contained empty candidate text.")
    return text.strip()


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _read_http_error(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        message = payload.get("error", {}).get("message")
        return str(message or body)[:1000]
    except (OSError, json.JSONDecodeError, AttributeError):
        return str(exc.reason)
