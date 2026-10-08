"""Synchronous Hermes gateway client for the pipeline engine.

The dashboard's chat bridge streams SSE for the UI. Pipeline stages need the
opposite: a blocking call that returns one complete answer, optionally forced
into JSON. This module is that client. Same gateway, same API key source, no
new dependencies.

Nothing here invents data — it only relays what an agent returns, and records
how long it took and how many tokens it cost so runs can be audited.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 127.0.0.1, not localhost: the gateway listens on IPv4 only, and on Windows
# "localhost" tries ::1 first and waits ~2s for the refusal on every call.
GATEWAY_URL = "http://127.0.0.1:8643/v1/chat/completions"
HERMES_ENV = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "hermes" / ".env"
DEFAULT_TIMEOUT = 300

# Named agents map onto the gateway's model routing; the bridge uses the same
# names, so a stage can address "@scribe" and reach the same specialist the
# operator talks to by hand.
AGENTS = {
    "orchestrator", "scout", "scribe", "reach", "dev",
    "lumen", "forge", "rank",
}


# The gateway answers 200 OK even when the upstream provider refused the call,
# putting the provider's error prose in message.content. Left unchecked that
# text is treated as the agent's output and written into client deliverables --
# observed twice: a billing 402 stored verbatim as a "technical fix plan".
#
# These patterns are deliberately anchored to the start of the reply and use
# whole phrases the gateway emits. A loose keyword scan is not safe here: real
# SEO copy legitimately contains words like "quota" (as in "quotable passage").
_PROVIDER_ERROR_PREFIXES = (
    "billing or credits exhausted",
    "openrouter reported that",
    "rate limit exceeded",
    "provider error:",
    "upstream error:",
    "insufficient_quota",
    "no endpoints found",
    "model not found",
)


# A bare status line is the other envelope shape: "HTTP 401: User not found."
# arrived as a whole reply when the upstream key was revoked (2026-09-26).
# Anchored to the very start, so prose that mentions a status code is safe.
_STATUS_ENVELOPE = re.compile(r"^http [45]\d\d\b\s*[:\-]")


def _provider_error(text: str) -> str | None:
    """Return the provider's complaint if this reply is an error envelope."""
    head = (text or "").lstrip()[:400].lower()
    if _STATUS_ENVELOPE.match(head):
        return (text or "").strip().splitlines()[0][:300]
    for marker in _PROVIDER_ERROR_PREFIXES:
        if head.startswith(marker) or f"\n{marker}" in head:
            first_line = (text or "").strip().splitlines()[0]
            return first_line[:300]
    return None


class LLMError(Exception):
    pass


def _api_key() -> str:
    import os
    for env_name in ("API_SERVER_KEY", "HERMES_API_SERVER_KEY"):
        val = os.environ.get(env_name)
        if val:
            return val.strip()
    for path in (HERMES_ENV, Path.home() / "AppData/Local/hermes/.env"):
        try:
            for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = raw.lstrip("﻿").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip().lstrip("﻿") in ("API_SERVER_KEY", "HERMES_API_SERVER_KEY"):
                    key = v.strip().strip('"').strip("'")
                    if key:
                        return key
        except (FileNotFoundError, OSError):
            continue
    raise LLMError("API_SERVER_KEY not found — cannot reach the Hermes gateway")


def complete(prompt: str, *, agent: str = "orchestrator", system: str = "",
             timeout: int = DEFAULT_TIMEOUT, max_retries: int = 1) -> dict[str, Any]:
    """Run one completion. Returns {text, tokens_in, tokens_out, seconds, agent}."""
    key = _api_key()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": "hermes-agent",
        "stream": False,
        "messages": messages,
        "metadata": {"bridge_target": f"@{agent}", "source": "pipeline"},
    }
    data = json.dumps(body).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        started = time.time()
        req = urllib.request.Request(
            GATEWAY_URL, data=data, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            choice = (payload.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            usage = payload.get("usage") or {}
            if not text.strip():
                raise LLMError("gateway returned an empty completion")
            complaint = _provider_error(text)
            if complaint:
                # Fail the stage loudly. Storing this text as output would put
                # a billing notice into a client deliverable.
                raise LLMError(f"provider refused the call: {complaint}")
            return {
                "text": text,
                "agent": agent,
                "seconds": round(time.time() - started, 1),
                "tokens_in": int(usage.get("prompt_tokens") or 0),
                "tokens_out": int(usage.get("completion_tokens") or 0),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            last_err = LLMError(f"gateway HTTP {exc.code}: {detail}")
        except Exception as exc:  # network, timeout, decode, empty
            last_err = exc if isinstance(exc, LLMError) else LLMError(f"{type(exc).__name__}: {exc}")
        if attempt < max_retries:
            time.sleep(2)
    raise last_err or LLMError("gateway call failed")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull JSON out of a model reply that may be fenced or prefaced.

    Raises LLMError rather than guessing if nothing parses — a stage that
    cannot read its own output must fail loudly, not silently produce junk.
    """
    candidates: list[str] = []
    fenced = _FENCE.findall(text or "")
    candidates.extend(f.strip() for f in fenced)
    stripped = (text or "").strip()
    candidates.append(stripped)
    # first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            candidates.append(stripped[start:end + 1])
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise LLMError("agent reply contained no parsable JSON")


def complete_json(prompt: str, *, agent: str = "orchestrator", system: str = "",
                  timeout: int = DEFAULT_TIMEOUT, retries: int = 1) -> dict[str, Any]:
    """Completion that must yield JSON. Returns {data, raw, tokens…}."""
    guard = ("You reply with valid JSON only. No prose, no markdown fences, "
             "no commentary before or after the JSON.")
    sys_prompt = f"{system}\n\n{guard}".strip()
    last: Exception | None = None
    for attempt in range(retries + 1):
        res = complete(prompt, agent=agent, system=sys_prompt, timeout=timeout)
        try:
            res["data"] = extract_json(res["text"])
            return res
        except LLMError as exc:
            last = exc
            prompt = (f"{prompt}\n\nYour previous reply was not valid JSON. "
                      f"Reply again with JSON only.")
    raise last or LLMError("no JSON produced")


def health() -> dict[str, Any]:
    """Cheap liveness probe used by the UI before offering to run anything."""
    try:
        _api_key()
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        req = urllib.request.Request("http://127.0.0.1:8643/health",
                                     headers={"Authorization": f"Bearer {_api_key()}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        return {"ok": payload.get("status") == "ok", "gateway": payload}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
