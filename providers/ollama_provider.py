"""Local Ollama HTTP provider with structured output validation and bounded retry."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.llm_provider import LLMProvider, ProviderError
from providers.schema_validation import SchemaValidationError, validate_json_schema


class OllamaProvider(LLMProvider):
    """Use Ollama's local /api/chat endpoint; no API key or paid service required."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float = 0,
        context_window: int = 8192,
        max_output_tokens: int = 4096,
        timeout_seconds: float = 300,
        max_retries: int = 2,
        keep_alive: str = "10m",
        think: bool = False,
        opener=urlopen,
        check_ready: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.temperature = float(temperature)
        self.context_window = int(context_window)
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.keep_alive = str(keep_alive)
        self.think = bool(think)
        self._opener = opener
        if not self.model:
            raise ProviderError("Ollama model is empty. Set llm.ollama.model in config.yaml.")
        if check_ready:
            self.check_ready()

    def check_ready(self) -> None:
        """Fail before HN work if the local service or configured model is unavailable."""
        request = Request(
            f"{self.base_url}/api/tags",
            headers={"Accept": "application/json", "User-Agent": "market-research-engine/1.0"},
        )
        try:
            with self._opener(request, timeout=5) as response:
                payload = json.load(response)
        except (URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise ProviderError(
                f"Ollama is not running at {self.base_url}. Start the Ollama Windows app "
                "or run 'ollama serve', then verify with "
                "'Invoke-WebRequest http://127.0.0.1:11434/api/tags'."
            ) from exc
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderError(f"Ollama /api/tags returned invalid JSON: {exc}") from exc

        models = payload.get("models") if isinstance(payload, dict) else None
        names = {
            str(model.get("name") or model.get("model") or "")
            for model in (models if isinstance(models, list) else [])
            if isinstance(model, dict)
        }
        aliases = names | {name.split(":", 1)[0] for name in names}
        if self.model not in aliases:
            raise ProviderError(
                f"Ollama is running, but model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        del purpose
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        retry_note = ""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            full_prompt = (
                f"{prompt}\n\nReturn only one JSON object matching this JSON Schema exactly:\n"
                f"{schema_text}{retry_note}"
            )
            try:
                response = self._chat(full_prompt, schema)
                parsed = json.loads(_strip_code_fence(response))
                if not isinstance(parsed, dict):
                    raise SchemaValidationError("$: expected object")
                validate_json_schema(parsed, schema)
                return parsed
            except (json.JSONDecodeError, SchemaValidationError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                retry_note = (
                    "\n\nThe previous response failed JSON/schema validation: "
                    f"{exc}. Return every required field. Never invent missing evidence; "
                    "use the literal string 'unknown' where the source has no evidence."
                )

        raise ProviderError(
            f"Ollama returned invalid structured output after {self.max_retries + 1} attempts: "
            f"{last_error}"
        )

    def _chat(self, prompt: str, schema: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an evidence extraction engine. Follow the supplied schema. "
                        "Output JSON only. Missing evidence must be 'unknown'; never fabricate."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": schema,
            "think": self.think,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_window,
                "num_predict": self.max_output_tokens,
            },
        }
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "market-research-engine/1.0",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                body = json.load(response)
        except HTTPError as exc:
            detail = _read_http_error(exc)
            if exc.code == 404 or "not found" in detail.lower():
                raise ProviderError(
                    f"Ollama model '{self.model}' is unavailable. Run: ollama pull {self.model}"
                ) from exc
            raise ProviderError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise ProviderError(
                f"Lost connection to Ollama at {self.base_url}. Start it with 'ollama serve' "
                "and run the command again."
            ) from exc
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderError(f"Ollama API returned invalid JSON: {exc}") from exc

        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama response did not contain message.content.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Ollama returned empty message.content.")
        return content.strip()


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _read_http_error(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        return str(payload.get("error") or body)[:1000]
    except (OSError, json.JSONDecodeError, AttributeError):
        return str(exc.reason)

