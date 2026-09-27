"""Jev (TypeSafe AI) — typed decisions instead of prose.

Claude and ChatGPT write. Jev decides. It is a "System One" model: you send
state plus typed questions and get back values with probabilities and a
confidence score, not sentences you then have to parse. TypeSafe report it as
far faster and cheaper than a frontier model on that kind of work.

That fits a real gap in this pipeline. Several decisions here are currently
either hardcoded or would cost a full LLM call to make well — "is this build
clean enough to skip the repair stage", "is this audit finding worth the
operator's attention", "which division should own this request". Those are
classifications, not essays.

Three question types, exactly as the API defines them:

  noul    yes/no      -> {"noul": 0.85}                     probability of yes
  choice  pick one    -> {"choice": k, "probabilities": {...}, "confidence": c}
  score   rate a scale-> {"score": 1.5, "legend": {...}, "probabilities": [...], "confidence": c}

Nothing is inferred when the model is not connected: callers get a clear
"unavailable" and decide for themselves, rather than a fabricated answer.

Stdlib only — the endpoint is a single authenticated POST.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CREDS_PATH = PROJECT_DIR / ".jev_credentials.json"

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
TIMEOUT = 30
MAX_QUESTIONS = 20
MAX_STATE_CHARS = 60000


class JevError(Exception):
    pass


# ---------------------------------------------------------------- credentials

def _read() -> dict[str, Any]:
    try:
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_credentials(api_key: str) -> None:
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("a TypeSafe API key is required")
    CREDS_PATH.write_text(json.dumps({"api_key": api_key}, indent=2), encoding="utf-8")


def disconnect() -> None:
    CREDS_PATH.unlink(missing_ok=True)


def _key() -> str:
    import os
    key = (_read().get("api_key") or "").strip() or os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise JevError("Jev is not connected — add a TypeSafe API key")
    return key


def connected() -> bool:
    try:
        _key()
        return True
    except JevError:
        return False


# ---------------------------------------------------------------- questions

def noul(instructions: str, criteria: dict[str, str] | None = None) -> dict[str, Any]:
    """A yes/no question. The answer is the probability of yes."""
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: str, options: dict[str, str]) -> dict[str, Any]:
    """Pick one option. `options` maps each key to what it means."""
    if not options or len(options) < 2:
        raise ValueError("a choice needs at least two options")
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict[str, Any]:
    """Rate along an ordered rubric, lowest level first."""
    if not levels or len(levels) < 2:
        raise ValueError("a score needs at least two levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# ---------------------------------------------------------------- the call

def decide(state: Any, questions: dict[str, dict[str, Any]], *,
           model: str = DEFAULT_MODEL, timeout: int = TIMEOUT) -> dict[str, Any]:
    """Ask Jev one or more typed questions about some state.

    Returns {"answers": {...}, "model": ..., "usage": {...}, "ms": n}. The
    answer shape depends on the question type; helpers below read them safely.
    """
    if not questions:
        raise ValueError("at least one question is required")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"too many questions: {len(questions)} (cap {MAX_QUESTIONS})")
    if not isinstance(state, str):
        state = json.dumps(state, ensure_ascii=False, default=str)
    if len(state) > MAX_STATE_CHARS:
        state = state[:MAX_STATE_CHARS]

    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
    })
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 401:
            raise JevError("TypeSafe rejected the API key (401)") from None
        if exc.code == 422:
            raise JevError(f"Jev rejected the request shape (422): {detail}") from None
        if exc.code in (429, 529):
            raise JevError(f"Jev is rate limited ({exc.code}) — retry shortly") from None
        raise JevError(f"Jev HTTP {exc.code}: {detail}") from None
    except Exception as exc:
        raise JevError(f"could not reach Jev: {type(exc).__name__}: {exc}") from None

    answers = payload.get("answers") or {}
    missing = [k for k in questions if k not in answers]
    if missing:
        raise JevError(f"Jev did not answer: {', '.join(missing)}")
    return {
        "answers": answers,
        "model": payload.get("model", model),
        "usage": payload.get("usage") or {},
        "ms": int((time.time() - started) * 1000),
        "provenance": "verified",   # the model actually answered; the value is its own
    }


# ---------------------------------------------------------------- reading answers

def yes(answer: dict[str, Any], threshold: float = 0.5) -> bool:
    """Read a noul answer as a boolean at an explicit threshold."""
    p = answer.get("noul")
    if p is None:
        raise JevError("not a noul answer")
    return float(p) >= threshold


def probability(answer: dict[str, Any]) -> float:
    p = answer.get("noul")
    if p is None:
        raise JevError("not a noul answer")
    return float(p)


def picked(answer: dict[str, Any]) -> tuple[str, float]:
    """Chosen option and the confidence behind it."""
    if "choice" not in answer:
        raise JevError("not a choice answer")
    return str(answer["choice"]), float(answer.get("confidence") or 0.0)


def level(answer: dict[str, Any]) -> tuple[float, str]:
    """Score value and the label of the nearest level."""
    if "score" not in answer:
        raise JevError("not a score answer")
    value = float(answer["score"])
    legend = answer.get("legend") or {}
    label = legend.get(str(int(round(value))), "")
    return value, str(label)


def status() -> dict[str, Any]:
    if not connected():
        return {"ok": False, "connected": False,
                "error": "Not connected. Add a TypeSafe API key to use Jev for typed decisions "
                         "(classification, routing, scoring) instead of a full model call."}
    try:
        res = decide("ping", {"ok": noul("Is this text non-empty?")}, timeout=15)
    except JevError as exc:
        return {"ok": False, "connected": True, "error": str(exc)}
    return {"ok": True, "connected": True, "model": res["model"],
            "latency_ms": res["ms"],
            "note": "Typed decisions: yes/no, choice and score, each with a confidence."}
