"""The agency's brain: every model the dashboard thinks with, behind one call.

Everything that reasons goes through here: pipeline stages, Jarvis, and the
"Ask the agency" chat. Each call walks the BRAIN CHAIN, an ordered list the
operator sets on the Models page:

  claude   Anthropic's API via the official SDK (key or `ant auth login`).
  chatgpt  OpenAI's API via the official SDK.
  ollama   A local Ollama server. The last line of defence: no key, no bill,
           no network. Only models that run on this machine count. Ollama
           "cloud" models execute on ollama.com and are never used as the
           local fallback, because that would send data off the machine
           while the UI claimed it stayed local.
  hermes   The agent gateway on :8643. Out of the default chain (a validation
           pass lost a run to its upstream's 402, and later its key was
           revoked); the operator can add it back.

Default order: Claude, then ChatGPT, then Ollama. A provider that fails
(out of credit, unreachable, refusing) hands the work to the next one, and
the result records which model actually answered and why the others did not.

Model choice is never invented: Claude defaults to Anthropic's current
model; ChatGPT and Ollama use whatever the operator picked from the live
list their account or machine returns.
"""

from __future__ import annotations

import json
import re
import os
import urllib.error
import urllib.request
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CREDS_PATH = PROJECT_DIR / ".provider_credentials.json"

# Anthropic's current default. Do not downgrade this for cost without the
# operator asking -- that is their call, not ours.
CLAUDE_DEFAULT_MODEL = "claude-opus-5"

# Long stages (a full HTML build runs tens of thousands of tokens) would hit
# HTTP timeouts on a non-streaming request, so Claude calls stream and the
# final message is assembled by the SDK.
CLAUDE_MAX_TOKENS = 32000
DEFAULT_TIMEOUT = 300

ORDER = ["claude", "chatgpt", "ollama", "hermes"]
LABELS = {"claude": "Claude (Anthropic)", "chatgpt": "ChatGPT (OpenAI)",
          "ollama": "Ollama (local)", "hermes": "Hermes gateway"}

# Claude and ChatGPT are the brain; Ollama is the local fallback. Hermes is
# opt-in (see the module docstring).
BRAIN_DEFAULT = ["claude", "chatgpt", "ollama"]
BRAIN = "brain"          # pass as provider= to use the operator's chain

# SEO, AEO and GEO work runs on the frontier models only. A keyword map, a
# schema plan or an answer-engine strategy from a small local model would look
# plausible and be wrong, and it lands in client deliverables -- so these
# stages never fall back to Ollama or the gateway. If neither cloud model
# answers, the stage fails loudly and can be resumed once one does.
CLOUD = "cloud"
CLOUD_PROVIDERS = ("claude", "chatgpt")

# Search work reaches the models three ways: pipeline stages (tagged in
# playbooks.SEARCH_STAGES), the SEO agent in Ask the agency, and SEO questions
# asked of Jarvis or any agent. The last two are recognised here.
SEARCH_AGENTS = {"rank"}
_SEARCH_WORDS = re.compile(
    r"\b(seo|aeo|geo|serps?|search engine|answer engine|generative engine|ai overviews?|"
    r"keywords?|rank(?:ing|ings|s)?|backlinks?|link building|schema(?: markup)?|structured data|"
    r"json-ld|meta (?:title|description)s?|sitemaps?|indexing|crawl(?:ing|ability)?|"
    r"core web vitals|search console|organic (?:traffic|search)|llms\.txt|citations? in ai)\b", re.I)


def policy_for(text: str = "", agent: str = "") -> str:
    """CLOUD for search work, BRAIN for everything else."""
    if (agent or "").lstrip("@").lower() in SEARCH_AGENTS:
        return CLOUD
    return CLOUD if _SEARCH_WORDS.search(text or "") else BRAIN

OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"
# Large enough for a stage prompt plus a page of output; small enough that an
# 8B model's cache stays on an 8 GB GPU. Capped at the model's own context.
OLLAMA_NUM_CTX = 16384


class ProviderError(Exception):
    pass


# A provider can be connected and still not answer: out of credit, rate
# limited, key revoked. Connection state cannot see that without spending a
# call, so every real call reports back here and the status shows it. Without
# this the dashboard said "Claude answering" while every request was failing
# over to ChatGPT on a "credit balance too low" 400.
FAILURE_TTL = 15 * 60
_HEALTH: dict[str, dict[str, Any]] = {}


def record(provider: str, error: Any = None) -> None:
    if error is None:
        _HEALTH.pop(provider, None)
    else:
        _HEALTH[provider] = {"error": str(error)[:240], "at": time.time()}


def api_message(exc: Any) -> str:
    """The human sentence inside an SDK error, not its dict repr.

    str(exc) on an API error is "Error code: 400 - {'type': 'error', ...}";
    the operator needs "Your credit balance is too low ...".
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("message"):
            return str(body["message"])
    return str(getattr(exc, "message", "") or exc)


def failing(provider: str) -> str:
    """The last error if this provider failed recently and has not answered since."""
    h = _HEALTH.get(provider)
    if h and time.time() - h["at"] < FAILURE_TTL:
        return h["error"]
    return ""


# ---------------------------------------------------------------- credentials

def _read() -> dict[str, Any]:
    try:
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    CREDS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_credentials(provider: str, api_key: str, model: str = "") -> None:
    provider = (provider or "").strip().lower()
    if provider not in ("claude", "chatgpt"):
        raise ValueError("provider must be 'claude' or 'chatgpt'")
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("an API key is required")
    data = _read()
    entry = {"api_key": api_key}
    if model.strip():
        entry["model"] = model.strip()
    elif provider == "claude":
        entry["model"] = CLAUDE_DEFAULT_MODEL
    data[provider] = entry
    _write(data)


def set_model(provider: str, model: str) -> None:
    if provider == "ollama":
        set_ollama(model=model)
        return
    data = _read()
    if provider not in data:
        raise ValueError(f"{provider} is not connected")
    data[provider]["model"] = (model or "").strip()
    _write(data)


def disconnect(provider: str) -> None:
    data = _read()
    data.pop(provider, None)
    _write(data)


# ---------------------------------------------------------------- the brain

def brain_order() -> list[str]:
    """The chain every thinking task walks, first to last."""
    saved = (_read().get("_brain") or {}).get("order")
    if isinstance(saved, list):
        order = [p for p in saved if p in ORDER]
        if order:
            return list(dict.fromkeys(order))
    return list(BRAIN_DEFAULT)


def set_brain_order(order: list[str]) -> list[str]:
    if not isinstance(order, list):
        raise ValueError("order must be a list of providers")
    clean = list(dict.fromkeys(str(p).strip().lower() for p in order))
    unknown = [p for p in clean if p not in ORDER]
    if unknown:
        raise ValueError(f"unknown provider(s): {', '.join(unknown)} (expected {', '.join(ORDER)})")
    if not clean:
        raise ValueError("the brain needs at least one provider")
    data = _read()
    data["_brain"] = {"order": clean}
    _write(data)
    return clean


def _healthy_first(chain: list[str]) -> list[str]:
    """A provider that just failed goes to the back of the line.

    It is still tried -- that is how a topped-up account recovers -- but not
    first, so a known-broken link stops adding a round trip to every request.
    """
    return [p for p in chain if not failing(p)] + [p for p in chain if failing(p)]


def chain_for(provider: str | None, fallback: bool) -> list[str]:
    """Which providers to try, in order, for one call.

    "brain" (or nothing) means the operator's chain. A named provider goes
    first; with fallback on, the rest of the chain follows it.
    """
    p = (provider or BRAIN).strip().lower()
    if p == BRAIN:
        return _healthy_first(brain_order())
    if p == CLOUD:
        # Claude and ChatGPT in the operator's order, both always present:
        # removing one from the brain does not exempt search work from the rule
        ranked = [x for x in brain_order() if x in CLOUD_PROVIDERS]
        return _healthy_first(ranked + [x for x in CLOUD_PROVIDERS if x not in ranked])
    if p not in ORDER:
        raise ProviderError(f"unknown provider '{provider}' (expected brain or one of {', '.join(ORDER)})")
    if not fallback:
        return [p]
    return [p] + [x for x in brain_order() if x != p]


def _key(provider: str) -> str | None:
    """Stored key first, then the conventional environment variable."""
    stored = (_read().get(provider) or {}).get("api_key")
    if stored:
        return stored
    env = {"claude": "ANTHROPIC_API_KEY", "chatgpt": "OPENAI_API_KEY"}.get(provider)
    return os.environ.get(env) if env else None


def _model(provider: str) -> str:
    stored = (_read().get(provider) or {}).get("model")
    if stored:
        return stored
    return CLAUDE_DEFAULT_MODEL if provider == "claude" else ""


# ---------------------------------------------------------------- ollama

def ollama_host() -> str:
    host = ((_read().get("ollama") or {}).get("host") or os.environ.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST).strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def _ollama_json(path: str, body: dict | None = None, timeout: float = 3.0) -> dict[str, Any]:
    req = urllib.request.Request(
        ollama_host() + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


_TAGS_CACHE: dict[str, Any] = {"at": 0.0, "host": "", "tags": None, "error": ""}


def ollama_tags(max_age: float = 10.0) -> tuple[list[dict[str, Any]] | None, str]:
    """(models, error). Cached briefly: the overview asks on every refresh."""
    host = ollama_host()
    c = _TAGS_CACHE
    if c["host"] == host and time.time() - c["at"] < max_age:
        return c["tags"], c["error"]
    try:
        tags, err = _ollama_json("/api/tags", timeout=2.0).get("models") or [], ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        tags, err = None, f"Ollama is not reachable at {host} ({getattr(exc, 'reason', exc)})"
    c.update(at=time.time(), host=host, tags=tags, error=err)
    return tags, err


def _is_cloud(tag: dict[str, Any]) -> bool:
    return (bool(tag.get("remote_host") or tag.get("remote_model"))
            or str(tag.get("name", "")).endswith(":cloud"))


def _is_chat(tag: dict[str, Any]) -> bool:
    caps = tag.get("capabilities")
    return not caps or "completion" in caps          # embedding-only models cannot chat


def ollama_local_models() -> tuple[list[dict[str, Any]], list[str]]:
    """(local chat models, names of hidden cloud models)."""
    tags, err = ollama_tags()
    if tags is None:
        raise ProviderError(err)
    local = [t for t in tags if not _is_cloud(t) and _is_chat(t)]
    cloud = [t["name"] for t in tags if _is_cloud(t)]
    return local, cloud


def set_ollama(host: str = "", model: str = "") -> dict[str, Any]:
    data = _read()
    entry = dict(data.get("ollama") or {})
    if host.strip():
        h = host.strip()
        if not h.startswith(("http://", "https://")):
            h = "http://" + h
        entry["host"] = h.rstrip("/")
    if model.strip():
        m = model.strip()
        _TAGS_CACHE["at"] = 0.0
        tags, err = ollama_tags(0)
        if tags is not None:
            tag = next((t for t in tags if t.get("name") == m), None)
            if tag is None:
                raise ValueError(f"'{m}' is not installed in Ollama (run: ollama pull {m})")
            if _is_cloud(tag):
                raise ValueError(f"'{m}' is an Ollama cloud model and runs on ollama.com, not this machine")
        entry["model"] = m
    data["ollama"] = entry
    _write(data)
    _TAGS_CACHE["at"] = 0.0
    return entry


def _ollama_ctx(model: str) -> int:
    for t in ollama_tags()[0] or []:
        if t.get("name") == model:
            n = (t.get("details") or {}).get("context_length")
            if isinstance(n, int) and n > 0:
                return min(n, OLLAMA_NUM_CTX)
    return OLLAMA_NUM_CTX


# ---------------------------------------------------------------- sign-in

# Anthropic's SDK resolves credentials in a chain: ANTHROPIC_API_KEY, then
# ANTHROPIC_AUTH_TOKEN, then an OAuth profile written by `ant auth login`, then
# workload identity. So a key is only one way in -- signing in with the CLI
# leaves a profile the SDK picks up on its own, and a bare Anthropic() works.
#
# OpenAI has no consumer equivalent. Its SDK reads OPENAI_API_KEY, and its only
# other path is workload identity federation for service accounts. A ChatGPT
# subscription is a separate product from the API and does not grant API access,
# so ChatGPT here needs a key.

def _anthropic_config_dir() -> Path | None:
    """Where the SDK keeps sign-in profiles, or None if it cannot be resolved.

    The SDK owns this answer, so ask it first -- the location is platform
    specific (%APPDATA%\Anthropic on Windows, ~/.config/anthropic elsewhere)
    and hardcoding the POSIX path here meant a successful `ant auth login` on
    Windows was never detected. The mirror below is only a fallback for if the
    SDK moves that private helper.

    Path.home() raises when no home variable is set (a bare service
    environment); a credential probe must never be what brings the dashboard
    down, so that returns None rather than propagating.
    """
    override = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if override:
        return Path(override)
    try:
        from anthropic.lib.credentials._constants import _config_dir  # type: ignore[attr-defined]
        return _config_dir()
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
            return base / "Anthropic"
        return Path.home() / ".config" / "anthropic"
    except (RuntimeError, OSError):
        return None


def signed_in(provider: str) -> bool:
    """True when the SDK can authenticate without us supplying a key."""
    if provider != "claude":
        return False
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config = _anthropic_config_dir()
    if config is None or not config.is_dir():
        return False
    # `ant auth login` writes credentials/<profile>.json alongside
    # configs/<profile>.json and an active_config pointer. A stored token is
    # what actually makes the SDK able to authenticate, so look for that.
    creds = config / "credentials"
    if creds.is_dir() and any(creds.glob("*.json")):
        return True
    return (config / "active_config").is_file()


def signin_hint() -> dict[str, Any]:
    """What the operator has to do, and whether the tool to do it is present."""
    return {
        "claude": {
            "supported": True,
            "command": "ant auth login",
            "cli_installed": bool(shutil.which("ant")),
            "config_dir": str(_anthropic_config_dir() or "unresolved"),
            "note": "Signing in with the Anthropic CLI writes an OAuth profile that the SDK "
                    "reads automatically — no key is pasted anywhere. Which account it bills "
                    "is whatever you sign in as; check your Anthropic Console.",
        },
        "chatgpt": {
            "supported": False,
            "note": "OpenAI has no sign-in equivalent for third-party apps. Its SDK accepts an "
                    "API key, or workload identity federation for service accounts. A ChatGPT "
                    "subscription is a separate product and does not include API access.",
        },
    }


# ---------------------------------------------------------------- SDK loading

def _sdk(provider: str):
    """Import the SDK on demand. Absence disables the provider, never crashes."""
    try:
        if provider == "claude":
            import anthropic
            return anthropic
        import openai
        return openai
    except ImportError as exc:
        raise ProviderError(
            f"the {provider} SDK is not installed ({exc}). Run: pip install -r requirements.txt"
        ) from None


# ---------------------------------------------------------------- completion

def _claude(prompt: str, system: str, timeout: int, model: str) -> dict[str, Any]:
    anthropic = _sdk("claude")
    key = _key("claude")
    if not key and not signed_in("claude"):
        raise ProviderError("Claude is not connected — add an API key, or run "
                            "'ant auth login' to sign in and let the SDK use that profile")
    # With no key of our own, construct bare so the SDK walks its credential
    # chain and picks up the signed-in profile.
    client = (anthropic.Anthropic(api_key=key, timeout=float(timeout)) if key
              else anthropic.Anthropic(timeout=float(timeout)))
    started = time.time()
    kwargs: dict[str, Any] = {
        "model": model or CLAUDE_DEFAULT_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        # Adaptive thinking is the current API; a fixed token budget is removed
        # on this model family and would be rejected.
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    try:
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise ProviderError(f"Claude API {exc.status_code}: {api_message(exc)}") from None
    except Exception as exc:
        raise ProviderError(f"Claude call failed: {type(exc).__name__}: {exc}") from None

    if getattr(message, "stop_reason", None) == "refusal":
        detail = getattr(getattr(message, "stop_details", None), "category", "") or "policy"
        raise ProviderError(f"Claude declined this request ({detail})")
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise ProviderError("Claude returned no text")
    usage = getattr(message, "usage", None)
    return {
        "text": text, "provider": "claude", "model": getattr(message, "model", model),
        "seconds": round(time.time() - started, 1),
        "tokens_in": int(getattr(usage, "input_tokens", 0) or 0),
        "tokens_out": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _chatgpt(prompt: str, system: str, timeout: int, model: str) -> dict[str, Any]:
    openai = _sdk("chatgpt")
    key = _key("chatgpt")
    if not key:
        raise ProviderError("ChatGPT is not connected — add an OpenAI API key")
    if not model:
        raise ProviderError("no ChatGPT model selected — pick one from your account's model list")
    client = openai.OpenAI(api_key=key, timeout=float(timeout))
    started = time.time()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        res = client.chat.completions.create(model=model, messages=messages)
    except openai.APIStatusError as exc:
        raise ProviderError(f"OpenAI API {exc.status_code}: {api_message(exc)}") from None
    except Exception as exc:
        raise ProviderError(f"ChatGPT call failed: {type(exc).__name__}: {exc}") from None

    choice = (res.choices or [None])[0]
    text = ((getattr(choice, "message", None).content if choice else "") or "").strip()
    if not text:
        raise ProviderError("ChatGPT returned no text")
    usage = getattr(res, "usage", None)
    return {
        "text": text, "provider": "chatgpt", "model": getattr(res, "model", model),
        "seconds": round(time.time() - started, 1),
        "tokens_in": int(getattr(usage, "prompt_tokens", 0) or 0),
        "tokens_out": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-style messages to Ollama's: string content, images as base64."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            images = [str(part["image_url"]["url"]).split(",", 1)[1] for part in content
                      if part.get("type") == "image_url"
                      and "," in str((part.get("image_url") or {}).get("url", ""))]
            msg: dict[str, Any] = {"role": m["role"], "content": text}
            if images:
                msg["images"] = images
            out.append(msg)
        else:
            out.append({"role": m["role"], "content": str(content or "")})
    return out


def _ollama_ready(model: str) -> None:
    if not model:
        raise ProviderError("no Ollama model selected; pick one on the Models page")
    tags, err = ollama_tags()
    if tags is None:
        raise ProviderError(err)
    tag = next((t for t in tags if t.get("name") == model), None)
    if tag is None:
        raise ProviderError(f"Ollama model '{model}' is not installed (run: ollama pull {model})")
    if _is_cloud(tag):
        raise ProviderError(f"'{model}' is an Ollama cloud model; the local fallback only runs models on this machine")


def _ollama(prompt: str, system: str, timeout: int, model: str, json_mode: bool = False) -> dict[str, Any]:
    _ollama_ready(model)
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False,
                            "options": {"num_ctx": _ollama_ctx(model)}, "keep_alive": "15m"}
    if json_mode:
        body["format"] = "json"
    started = time.time()
    try:
        res = _ollama_json("/api/chat", body, timeout=float(timeout))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise ProviderError(f"Ollama {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ProviderError(f"Ollama call failed: {getattr(exc, 'reason', exc)}") from None
    text = ((res.get("message") or {}).get("content") or "").strip()
    if not text:
        raise ProviderError("Ollama returned no text")
    return {"text": text, "provider": "ollama", "model": res.get("model", model),
            "seconds": round(time.time() - started, 1),
            "tokens_in": int(res.get("prompt_eval_count") or 0),
            "tokens_out": int(res.get("eval_count") or 0)}


def _hermes(prompt: str, system: str, timeout: int, agent: str) -> dict[str, Any]:
    import llm  # the existing gateway client; imported here to avoid a cycle
    res = llm.complete(prompt, agent=agent, system=system, timeout=timeout)
    res["provider"] = "hermes"
    res.setdefault("model", f"@{agent}")
    return res


def complete(prompt: str, *, provider: str = BRAIN, agent: str = "orchestrator",
             system: str = "", timeout: int = DEFAULT_TIMEOUT,
             fallback: bool = False, json_mode: bool = False) -> dict[str, Any]:
    """One completion from the brain.

    provider="brain" walks the operator's chain. A named provider is tried
    first, and with fallback=True the rest of the chain follows, so an
    upstream out of credit hands the work on instead of failing it. After the
    first, only connected providers are attempted; every failure is recorded
    so the result shows which model answered and why the others did not.
    """
    chain = chain_for(provider, fallback)
    policy = (provider or BRAIN).strip().lower()
    named = policy not in (BRAIN, CLOUD)
    attempts: list[dict[str, str]] = []
    skipped: list[str] = []
    for i, name in enumerate(chain):
        # a provider named explicitly is always attempted, so its own error is
        # what gets reported; the rest must be connected to be tried
        if not (named and i == 0) and not available(name):
            skipped.append(name)
            continue
        try:
            if name == "claude":
                out = _claude(prompt, system, timeout, _model("claude"))
            elif name == "chatgpt":
                out = _chatgpt(prompt, system, timeout, _model("chatgpt"))
            elif name == "ollama":
                out = _ollama(prompt, system, timeout, _model("ollama"), json_mode=json_mode)
            else:
                out = _hermes(prompt, system, timeout, agent)
            record(name)
            if attempts:
                out["fallback_from"] = attempts
            return out
        except Exception as exc:
            record(name, exc)
            attempts.append({"provider": name, "error": str(exc)[:200]})
    if policy == CLOUD:
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "neither is connected"
        raise ProviderError("SEO, AEO and GEO work runs only on Claude or ChatGPT, and neither answered "
                            f"({detail}). It never falls back to a local model. Resume once one is available.")
    if not attempts:
        raise ProviderError("no thinking engine is connected; connect Claude, ChatGPT or Ollama on the Models page"
                            + (f" (not connected: {', '.join(skipped)})" if skipped else ""))
    detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
    raise ProviderError(f"every provider failed: {detail}")


# ---------------------------------------------------------------- streaming

def _claude_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-style messages to Anthropic's (system apart, strict alternation)."""
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for part in content:
                if part.get("type") == "text":
                    blocks.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    url = str((part.get("image_url") or {}).get("url", ""))
                    mm = re.match(r"data:(image/[\w.+-]+);base64,(.+)", url, re.S)
                    if mm:
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": mm.group(1), "data": mm.group(2)}})
            content = blocks or [{"type": "text", "text": ""}]
        else:
            content = [{"type": "text", "text": str(content or "")}]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(content)      # the API needs alternating turns
        else:
            out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return system, out


def _stream_claude(messages, timeout, effort=None):
    anthropic = _sdk("claude")
    key = _key("claude")
    if not key and not signed_in("claude"):
        raise ProviderError("Claude is not connected")
    client = (anthropic.Anthropic(api_key=key, timeout=float(timeout)) if key
              else anthropic.Anthropic(timeout=float(timeout)))
    system, msgs = _claude_messages(messages)
    kwargs: dict[str, Any] = {"model": _model("claude") or CLAUDE_DEFAULT_MODEL,
                              "max_tokens": CLAUDE_MAX_TOKENS,
                              "thinking": {"type": "adaptive"}, "messages": msgs}
    if system:
        kwargs["system"] = system
    if effort:
        # voice wants a fast answer; on Opus 5.5 effort is the speed control
        # (thinking itself cannot be switched off)
        kwargs["output_config"] = {"effort": effort}
    try:
        with client.messages.stream(**kwargs) as st:
            yield from st.text_stream
    except anthropic.APIStatusError as exc:
        raise ProviderError(f"Claude API {exc.status_code}: {api_message(exc)}") from None


def _stream_chatgpt(messages, timeout):
    openai = _sdk("chatgpt")
    key, model = _key("chatgpt"), _model("chatgpt")
    if not key or not model:
        raise ProviderError("ChatGPT is not connected")
    client = openai.OpenAI(api_key=key, timeout=float(timeout))
    try:
        for chunk in client.chat.completions.create(model=model, messages=messages, stream=True):
            choice = (chunk.choices or [None])[0]
            piece = getattr(getattr(choice, "delta", None), "content", None) if choice else None
            if piece:
                yield piece
    except openai.APIStatusError as exc:
        raise ProviderError(f"OpenAI API {exc.status_code}: {api_message(exc)}") from None


def _stream_ollama(messages, timeout):
    model = _model("ollama")
    _ollama_ready(model)
    body = {"model": model, "messages": _to_ollama_messages(messages), "stream": True,
            "options": {"num_ctx": _ollama_ctx(model)}, "keep_alive": "15m"}
    req = urllib.request.Request(ollama_host() + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise ProviderError(f"Ollama: {d['error']}")
                piece = (d.get("message") or {}).get("content")
                if piece:
                    yield piece
                if d.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"Ollama {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ProviderError(f"Ollama call failed: {getattr(exc, 'reason', exc)}") from None


def stream(messages: list[dict[str, Any]], *, provider: str = BRAIN, timeout: int = DEFAULT_TIMEOUT,
           hermes: Any = None, effort: str | None = None):
    """Stream a chat through the brain chain.

    Yields ("meta", {...}) once, naming the provider that is answering, then
    ("token", text) pieces. Fallback happens only before the first token:
    once an answer is on screen, a failure is reported rather than a second
    answer spliced onto the first. `hermes` is a callable(messages) returning
    a token iterator for the gateway, which the server owns.
    """
    attempts: list[dict[str, str]] = []
    policy = (provider or BRAIN).strip().lower()
    for name in chain_for(provider, True):
        if name == "hermes" and hermes is None:
            continue
        if not available(name):
            continue
        gen = (_stream_claude(messages, timeout, effort) if name == "claude"
               else _stream_chatgpt(messages, timeout) if name == "chatgpt"
               else _stream_ollama(messages, timeout) if name == "ollama"
               else hermes(messages))
        started = False
        try:
            for piece in gen:
                if not started:
                    started = True
                    yield "meta", {"provider": name, "label": LABELS[name],
                                   "model": _model(name) or ("routed per agent" if name == "hermes" else ""),
                                   "fell_back_from": list(attempts)}
                yield "token", piece
            if started:
                record(name)
                return
            record(name, "returned no text")
            attempts.append({"provider": name, "error": "returned no text"})
        except Exception as exc:
            record(name, exc)
            if started:
                raise ProviderError(f"{LABELS[name]} stopped mid-answer: {exc}") from None
            attempts.append({"provider": name, "error": str(exc)[:200]})
    if policy == CLOUD:
        raise ProviderError("SEO, AEO and GEO work runs only on Claude or ChatGPT, and neither answered ("
                            + ("; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "neither is connected")
                            + "). It never falls back to a local model.")
    if not attempts:
        raise ProviderError("no thinking engine is connected; connect Claude, ChatGPT or Ollama on the Models page")
    raise ProviderError("every provider failed: " + "; ".join(f"{a['provider']}: {a['error']}" for a in attempts))


# ---------------------------------------------------------------- status

def available(provider: str) -> bool:
    """Connected and usable, without spending an API call to find out."""
    if provider == "hermes":
        try:
            import llm
            return bool(llm.health().get("ok"))
        except Exception:
            return False
    if provider == "ollama":
        try:
            _ollama_ready(_model("ollama"))
            return True
        except ProviderError:
            return False
    if not _key(provider) and not signed_in(provider):
        return False
    if provider == "chatgpt" and not _model("chatgpt"):
        return False
    try:
        _sdk(provider)
    except ProviderError:
        return False
    return True


# Ids that share a chat prefix but cannot hold a conversation: image, speech,
# realtime audio, search previews and completion-only (instruct) models.
_NOT_CHAT = re.compile(r"image|audio|realtime|tts|transcribe|whisper|dall-e|embedding|"
                       r"moderation|search|instruct|computer-use|codex", re.I)


def chat_models(ids: list[str]) -> list[str]:
    """Chat-capable OpenAI ids, newest family first, dated snapshots after aliases.

    The first entry is what the picker preselects, so a sort that put
    gpt-3.5-turbo on top would quietly steer people to the oldest model.
    """
    chat = [i for i in ids if i.startswith(("gpt", "o1", "o3", "o4", "o5", "chatgpt"))
            and not _NOT_CHAT.search(i)]

    def key(i: str):
        m = re.match(r"gpt-(\d+(?:\.\d+)?)", i)
        # the o-series counts separately (o1, o3, o4); those are GPT-4-era
        # reasoning models, so rank them with that generation, not by digit
        version = float(m.group(1)) if m else 4.0 if re.match(r"o\d", i) else 0.0
        dated = bool(re.search(r"-\d{4}-\d{2}-\d{2}$|-\d{4}$", i))
        return (-version, dated, len(i), i)
    return sorted(chat, key=key) or sorted(ids)


def models(provider: str) -> list[str]:
    """The models this account can actually use. Never a hardcoded guess."""
    if provider == "ollama":
        local, _cloud = ollama_local_models()
        return [t["name"] for t in local]
    key = _key(provider)
    if not key and not signed_in(provider):
        raise ProviderError(f"{provider} is not connected")
    sdk = _sdk(provider)
    try:
        if provider == "claude":
            client = (sdk.Anthropic(api_key=key, timeout=30.0) if key
                      else sdk.Anthropic(timeout=30.0))
            return [m.id for m in client.models.list(limit=40)]
        client = sdk.OpenAI(api_key=key, timeout=30.0)
        ids = [m.id for m in client.models.list()]
        # Chat-capable ids only; the list also carries embeddings, audio, etc.
        return chat_models(ids)
    except Exception as exc:
        raise ProviderError(f"could not list {provider} models: {type(exc).__name__}: {exc}") from None


def status() -> dict[str, Any]:
    """What the UI shows: which providers are live, and on what model."""
    out: dict[str, Any] = {}
    for name in ORDER:
        if name == "ollama":
            out[name] = _ollama_status()
            continue
        if name == "hermes":
            try:
                import llm
                h = llm.health()
                out[name] = {"label": LABELS[name], "connected": bool(h.get("ok")),
                             "model": "routed per agent",
                             "error": "" if h.get("ok") else str(h.get("error", ""))}
            except Exception as exc:
                out[name] = {"label": LABELS[name], "connected": False, "model": "", "error": str(exc)}
            continue
        has_key = bool(_key(name)) or signed_in(name)
        sdk_ok, sdk_err = True, ""
        try:
            _sdk(name)
        except ProviderError as exc:
            sdk_ok, sdk_err = False, str(exc)
        model = _model(name)
        problem = ""
        if not has_key:
            problem = ("no API key — paste one, or sign in with 'ant auth login'"
                       if name == "claude" else "no API key")
        elif not sdk_ok:
            problem = sdk_err
        elif name == "chatgpt" and not model:
            problem = "no model selected"
        out[name] = {
            "label": LABELS[name],
            "connected": has_key and sdk_ok and bool(model or name == "claude"),
            "model": model,
            "key_source": ("stored" if (_read().get(name) or {}).get("api_key")
                           else "signed in" if signed_in(name)
                           else "environment" if _key(name) else ""),
            "error": problem,
        }
    out["_any"] = any(v.get("connected") for k, v in out.items() if not k.startswith("_"))
    for name in ORDER:
        if name in out:
            out[name]["failing"] = failing(name)
    order = brain_order()
    live = [p for p in order if (out.get(p) or {}).get("connected")]
    healthy = [p for p in live if not out[p]["failing"]]
    # "active" is who is really answering: the first connected provider that
    # has not just failed. If every one has, the first is still tried first.
    out["_brain"] = {"order": order, "live": live, "failing": [p for p in live if out[p]["failing"]],
                     "active": (healthy or live or [""])[0], "default": list(BRAIN_DEFAULT)}
    return out


def _ollama_status() -> dict[str, Any]:
    host, model = ollama_host(), _model("ollama")
    base: dict[str, Any] = {"label": LABELS["ollama"], "host": host, "model": model, "key_source": "",
                            "models": [], "cloud_hidden": []}
    tags, err = ollama_tags()
    if tags is None:
        return {**base, "connected": False, "reachable": False, "error": err}
    base["models"] = [{"name": t["name"], "size": (t.get("details") or {}).get("parameter_size", ""),
                       "context": (t.get("details") or {}).get("context_length")}
                      for t in tags if not _is_cloud(t) and _is_chat(t)]
    base["cloud_hidden"] = [t["name"] for t in tags if _is_cloud(t)]
    problem = ""
    try:
        _ollama_ready(model)
    except ProviderError as exc:
        problem = str(exc)
    return {**base, "connected": not problem, "reachable": True, "error": problem}
