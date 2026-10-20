"""Thin, dependency-free client for Gemini or OpenAI. Returns None when unavailable so
callers can always fall back to deterministic, rule-based text."""
import json
import logging
import time

import httpx

from ..config import settings

log = logging.getLogger("edupulse.llm")
RETRY_STATUS = {429, 500, 502, 503, 504}


def _post(url: str, headers: dict, body: dict, attempts: int = 2) -> httpx.Response:
    """POST with a short retry on rate limits / temporary overloads (common on free tiers)."""
    for i in range(attempts):
        r = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(40, connect=10))
        if r.status_code not in RETRY_STATUS or i == attempts - 1:
            r.raise_for_status()
            return r
        time.sleep(1.5)
    raise RuntimeError("unreachable")


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUS
    return isinstance(exc, httpx.TransportError)  # timeouts, dropped connections


def provider() -> str | None:
    p = settings.LLM_PROVIDER.lower().strip()
    if p == "gemini" and settings.GEMINI_API_KEY:
        return "gemini"
    if p == "openai" and settings.OPENAI_API_KEY:
        return "openai"
    return None


def generate(prompt: str, system: str = "", json_mode: bool = False, history: list[dict] | None = None) -> str | None:
    p = provider()
    if p is None:
        return None
    history = history or []
    try:
        if p == "gemini":
            contents = [
                {"role": "model" if h["role"] == "assistant" else "user", "parts": [{"text": h["content"]}]}
                for h in history
            ] + [{"role": "user", "parts": [{"text": prompt}]}]
            body: dict = {"contents": contents, "generationConfig": {"temperature": 0.4}}
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            if json_mode:
                body["generationConfig"]["responseMimeType"] = "application/json"
            models = [settings.GEMINI_MODEL] + ([settings.GEMINI_FALLBACK_MODEL] if settings.GEMINI_FALLBACK_MODEL else [])
            for i, model in enumerate(models):
                try:
                    r = _post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        {"x-goog-api-key": settings.GEMINI_API_KEY},
                        body,
                    )
                    return "".join(part.get("text", "") for part in r.json()["candidates"][0]["content"]["parts"])
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    if not _is_transient(exc) or i == len(models) - 1:
                        raise
                    log.warning("Gemini model %s unavailable (%s); trying %s", model, type(exc).__name__, models[i + 1])
        messages = ([{"role": "system", "content": system}] if system else []) + history + [{"role": "user", "content": prompt}]
        body = {"model": settings.OPENAI_MODEL, "messages": messages, "temperature": 0.4}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = _post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            body,
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        log.exception("LLM call failed; falling back to rule-based output")
        return None


def generate_json(prompt: str, system: str = "") -> dict | None:
    text = generate(prompt, system=system, json_mode=True)
    if not text:
        return None
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
