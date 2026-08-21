"""Ollama LLM adapter with primary→fallback models and a JSON repair loop."""
from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

from ... import bridge
from ..config import settings
from .json_utils import extract_json, format_validation_errors

TraceSink = Callable[[dict[str, Any]], Awaitable[None]]
Validator = Callable[[dict], Any]

REPAIR_PROMPT = (
    "The JSON you returned failed schema validation with these errors:\n"
    "{errors}\n\n"
    "Return the FULL corrected JSON object only. Do not add prose, comments, "
    "or markdown fences. Keep all previously-correct content; only fix the "
    "listed problems and any missing required fields."
)


class LLMUnavailable(RuntimeError):
    """Ollama could not be reached on any configured model."""


class LLMRepairFailed(RuntimeError):
    """Model responded but could not produce schema-valid JSON within budget."""

    def __init__(self, label: str, partial: Optional[dict], raw: str):
        super().__init__(f"LLM repair failed for '{label}'")
        self.label = label
        self.partial = partial
        self.raw = raw


class LLMClient:
    def __init__(self) -> None:
        self.base = settings.ollama_base_url.rstrip("/")
        self._timeout = httpx.Timeout(settings.ollama_request_timeout, connect=5.0)

    @property
    def primary(self) -> str:
        return bridge.srs_model()

    @property
    def fallback(self) -> str:
        return settings.ollama_fallback_model or self.primary

    async def _call_model(
        self, model: str, messages: list[dict], json_mode: bool
    ) -> str:

        base, headers = bridge.route(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {

                "num_ctx": bridge.num_ctx(model),
                "temperature": 0.2,
            },
        }
        if json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{base.rstrip('/')}/api/chat",
                                     json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data.get("message", {}).get("content", "")

    async def _chat(
        self,
        messages: list[dict],
        json_mode: bool,
        label: str,
        trace_sink: Optional[TraceSink],
    ) -> str:
        errors: list[str] = []

        models: list[str] = []
        for m in (self.primary, self.fallback):
            if m and m not in models:
                models.append(m)
        for model in models:
            t0 = time.time()
            try:
                content = await self._call_model(model, messages, json_mode)
                if trace_sink:
                    await trace_sink(
                        {
                            "label": label,
                            "model": model,
                            "ms": int((time.time() - t0) * 1000),
                            "system": _first(messages, "system"),
                            "user": _last(messages, "user"),
                            "response": content[:6000],
                        }
                    )
                return content
            except Exception as exc:  # noqa: BLE001 - any failure tries fallback
                errors.append(f"{model}: {exc!s}")
                continue
        raise LLMUnavailable(
            f"Ollama unreachable for '{label}' at {self.base}: " + " | ".join(errors)
        )

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        images: Optional[list[str]] = None,
        validator: Optional[Validator] = None,
        max_attempts: Optional[int] = None,
        label: str = "completion",
        trace_sink: Optional[TraceSink] = None,
    ) -> dict:
        """Return a schema-valid JSON object, repairing up to N attempts."""
        max_attempts = max_attempts or settings.llm_max_repair_attempts
        messages: list[dict] = [
            {"role": "system", "content": system},
            _user_message(user, images),
        ]
        last_partial: Optional[dict] = None
        last_text = ""
        for attempt in range(1, max_attempts + 1):
            text = await self._chat(
                messages, json_mode=True, label=f"{label}#{attempt}", trace_sink=trace_sink
            )
            last_text = text
            try:
                data = extract_json(text)
            except ValueError as exc:
                messages.append({"role": "assistant", "content": text[:2000]})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({exc}). Reply with ONLY a "
                            "single valid JSON object — no prose, no fences."
                        ),
                    }
                )
                continue
            last_partial = data
            if validator is None:
                return data
            try:
                validator(data)
                return data
            except Exception as exc:  # noqa: BLE001 - validation error -> repair
                errs = exc.errors() if hasattr(exc, "errors") else str(exc)
                feedback = (
                    format_validation_errors(errs) if not isinstance(errs, str) else errs
                )
                messages.append(
                    {"role": "assistant", "content": json.dumps(data)[:3500]}
                )
                messages.append(
                    {"role": "user", "content": REPAIR_PROMPT.format(errors=feedback)}
                )
        raise LLMRepairFailed(label, last_partial, last_text)

    async def complete_text(
        self, *, system: str, user: str, images: Optional[list[str]] = None,
        label: str = "text", trace_sink: Optional[TraceSink] = None,
    ) -> str:
        return await self._chat(
            [
                {"role": "system", "content": system},
                _user_message(user, images),
            ],
            json_mode=False,
            label=label,
            trace_sink=trace_sink,
        )

    async def health(self) -> dict:
        """Return availability + installed models (best-effort)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(f"{self.base}/api/tags")
                resp.raise_for_status()
                tags = resp.json().get("models", [])
            names = [t.get("name") for t in tags]
            return {
                "available": True,
                "base_url": self.base,
                "models": names,
                "primary_ready": any(self.primary in (n or "") for n in names),
                "fallback_ready": any(self.fallback in (n or "") for n in names),
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "base_url": self.base, "error": str(exc)}


def _user_message(content: str, images: Optional[list[str]] = None) -> dict:
    """A user turn, with base64 images attached the way /api/chat expects them."""
    message: dict[str, Any] = {"role": "user", "content": content}
    usable = [i for i in (images or []) if i]
    if usable:
        message["images"] = usable
    return message


def _first(messages: list[dict], role: str) -> str:
    for m in messages:
        if m["role"] == role:
            return str(m["content"])[:2000]
    return ""


def _last(messages: list[dict], role: str) -> str:
    for m in reversed(messages):
        if m["role"] == role:
            return str(m["content"])[:2000]
    return ""


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
