"""Jarvis — the agency's voice assistant, server side.

The browser handles speech (recognition and synthesis) and answers the common
commands itself, instantly and for free: navigation, status, attention, search.
Only what it cannot answer locally comes here, so this module has one job —
turn a spoken question into a short spoken answer grounded in the agency's
real state.

Two rules keep it honest:
  * The model sees a digest built from the same rows the dashboard shows, and
    is told to say "I don't know" rather than invent a number. A model with
    no data to read would otherwise guess, and a guessed ranking or revenue
    figure spoken aloud is worse than silence.
  * The model may ask the dashboard to *navigate* — nothing else. It cannot
    start a pipeline, approve a gate or spend money. Those stay one click
    away for a human. Action tokens are parsed against a fixed whitelist, so
    a hallucinated or injected token is dropped, not executed.
"""

from __future__ import annotations

import re
from typing import Any

# Pages Jarvis may open. Mirrors the route table in app/q/js/routes.js; a key
# missing here simply cannot be reached by voice, which is the safe failure.
ROUTES = {
    "": "Overview", "projects": "Websites", "growth": "Growth", "clients": "Clients",
    "monitor": "Activity", "builder": "Website builder", "approvals": "Approvals",
    "ai": "Ask the agency", "agency": "The Agency", "models": "Models",
    "integrations": "Integrations", "computer": "Computer use", "media": "Image & Video", "optimize": "Website audits",
    "seo": "OpenSEO", "backlinks": "Link outreach",
}


MAX_QUESTION = 1200
MAX_HISTORY = 6
TIMEOUT = 60

SYSTEM = """You are Jarvis, the voice of Gem Agency's operating dashboard. \
The operator runs a web design and SEO agency and is talking to you out loud.

How to answer:
- Your reply is spoken aloud. One to three short sentences. No markdown, \
no bullet points, no lists, no URLs, no emoji. Spell out symbols.
- Be crisp, capable and a little dry, like a trusted chief of staff. \
Address the operator as "sir" at most once per reply, and only sometimes.
- Answer only from the AGENCY STATE below. If the answer is not in it, say \
you don't have that data and name where it would come from. Never invent a \
number, a ranking, a client, a date or a result.
- You cannot start builds, approve anything, send messages or spend money. \
If asked, say which page does it and offer to open that page.

Opening a page: if showing a page would help, end your reply with exactly one \
token of the form [[open:KEY]] using a key from this list: {routes}. \
Use at most one token. Never put a token in the middle of a sentence."""


def _clip(s: Any, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def digest() -> str:
    """The agency's state as compact text for the model to read.

    Everything here is a stored row or a count of stored rows; no estimate
    enters. Sections that fail to load say so instead of disappearing, so the
    model can tell "none" apart from "unknown".
    """
    lines: list[str] = []
    try:
        import agency
        ov = agency.overview()
    except Exception as exc:  # a broken read must not become "you have nothing"
        return f"AGENCY STATE: unavailable ({_clip(exc, 160)})."

    c = ov.get("counts") or {}
    lines.append(
        f"Counts: {c.get('clients', 0)} clients, {c.get('projects', 0)} websites/projects, "
        f"{c.get('approvals_pending', 0)} approvals pending "
        f"({c.get('approvals_blocking', 0)} blocking), {c.get('tasks_open', 0)} open tasks, "
        f"{c.get('keywords', 0)} tracked keywords."
    )
    mrr = ov.get("mrr_cents")
    if mrr:
        lines.append(f"Monthly recurring revenue: ${mrr / 100:,.0f} "
                     f"({ov.get('mrr_provenance') or 'unverified'} — typed in, not billed).")

    projects = [p for p in ov.get("projects") or [] if not str(p.get("name", "")).startswith("ZZ ")]
    if projects:
        lines.append("Websites:")
        for p in projects[:12]:
            stage = f"stage {p.get('stage')}/{p.get('stage_total')}" if p.get("stage_total") else ""
            lines.append(f"- {_clip(p.get('name'), 60)}: {p.get('status', '?')}, "
                         f"{p.get('health', '?')}{', ' + stage if stage else ''}"
                         f"{', due ' + str(p['due_date']) if p.get('due_date') else ''}")

    clients = ov.get("clients") or []
    if clients:
        lines.append("Clients: " + "; ".join(
            f"{_clip(x.get('name'), 40)} ({x.get('status', '?')})" for x in clients[:10]))

    for a in (ov.get("approvals") or [])[:6]:
        lines.append(f"Approval waiting: {_clip(a.get('title'), 90)}"
                     f"{' (blocking)' if a.get('blocking') else ''}")
    for a in (ov.get("attention") or [])[:6]:
        lines.append(f"Attention [{a.get('severity', '?')}]: {_clip(a.get('title'), 110)}")

    integ = ov.get("integrations") or {}
    if integ:
        lines.append("Integrations: " + ", ".join(
            f"{k}={(v or {}).get('state', '?')}" for k, v in integ.items()))

    busy = [a for a in ov.get("agents") or []
            if a.get("status") in ("running", "planning", "waiting", "needs_approval")]
    lines.append(f"Agents working now: {len(busy)}"
                 + (" — " + ", ".join(_clip(a.get('name'), 24) for a in busy[:6]) if busy else ""))

    try:
        import runner
        runs = runner.runs_list(8)
    except Exception as exc:
        runs = None
        lines.append(f"Pipeline runs: unavailable ({_clip(exc, 100)}).")
    if runs is not None:
        if not runs:
            lines.append("Pipeline runs: none yet.")
        for r in runs[:8]:
            lines.append(f"Run on {_clip(r.get('project_name') or 'a project', 50)}: "
                         f"{r.get('playbook', '?')} {r.get('state', '?')}"
                         f"{' — ' + _clip(r.get('error'), 140) if r.get('error') else ''}")

    for e in (ov.get("activity") or [])[:5]:
        lines.append(f"Recent: {e.get('action', '?')} by {e.get('actor', '?')} at {str(e.get('ts', ''))[:16]}")

    return "AGENCY STATE (as of " + str(ov.get("generated_at", ""))[:16] + " UTC):\n" + "\n".join(lines)


_TOKEN = re.compile(r"\[\[\s*open\s*:\s*([a-z_]*)\s*\]\]", re.I)


def parse_actions(text: str) -> tuple[str, list[dict[str, str]]]:
    """Split a model reply into speakable text and whitelisted actions.

    Every token is removed from the text whether or not it is allowed — a
    rejected token must not be read aloud as "open colon payments" either.
    """
    actions: list[dict[str, str]] = []
    for m in _TOKEN.finditer(text or ""):
        key = m.group(1).lower()
        if key in ROUTES and not actions:
            actions.append({"type": "open", "route": key, "label": ROUTES[key]})
    clean = _TOKEN.sub("", text or "")
    clean = re.sub(r"[*_`#>]+", "", clean)         # stray markdown reads badly aloud
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean, actions


def ask(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("say or type a question first")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"that is too long for a voice command (over {MAX_QUESTION} characters)")

    convo = []
    for turn in (history or [])[-MAX_HISTORY:]:
        who = "Operator" if turn.get("role") == "user" else "Jarvis"
        convo.append(f"{who}: {_clip(turn.get('text'), 400)}")

    prompt = digest() + "\n\n"
    if convo:
        prompt += "CONVERSATION SO FAR:\n" + "\n".join(convo) + "\n\n"
    prompt += f"Operator: {question}\nJarvis:"

    import providers
    # the brain chain: Claude, then ChatGPT, then local Ollama (Models page order)
    system = SYSTEM.format(routes=", ".join(k or '""' for k in ROUTES))
    # an SEO / AEO / GEO question is search work: Claude or ChatGPT only
    res = providers.complete(prompt, provider=providers.policy_for(question), agent="orchestrator",
                             system=system, timeout=TIMEOUT, fallback=True)
    reply, actions = parse_actions(res.get("text", ""))
    if not reply:
        reply = "I have nothing useful to add to that."
    return {
        "reply": reply,
        "actions": actions,
        "provider": res.get("provider", ""),
        "model": res.get("model", ""),
        "seconds": res.get("seconds", 0),
        "fell_back": bool(res.get("fallback_from")),
    }


# ================================================================ conversation
#
# Voice mode, like a spoken ChatGPT: any sentence goes to the model, which
# answers in natural speech and can act on the dashboard. The reply streams,
# so the browser speaks the first sentence while the rest is still coming.
#
# Acting: after its spoken reply the model may add ONE final line
#     ACTIONS: [{"do": "...", ...}]
# drawn from the vocabulary below. The line is never spoken or shown; each
# action is checked against the vocabulary and the agency's real records,
# and anything else is dropped. Nothing in the vocabulary spends money or
# changes a record: builds, agent jobs, browser tasks and media are drafted
# into their page for the operator to confirm, exactly as before.

import json as _json

MAX_ACTIONS = 3
CHAT_HISTORY = 12
ACCENTS = ("teal", "violet", "sky", "orchid", "mono")
AGENT_TARGETS = ("@orchestrator", "@scout", "@scribe", "@reach", "@dev", "@lumen", "@antigravity",
                 "@chatgpt", "@forge", "@rank", "@stitch", "@all")
MEDIA_MODES = ("text-to-image", "image-to-image", "text-to-video", "image-to-video")

CHAT_SYSTEM = """You are Jarvis, the voice assistant built into Gem Agency's dashboard. The \
operator runs a web design and SEO agency and is talking to you out loud, hands-free, like a \
conversation with a trusted, sharp chief of staff.

Talk naturally:
- Your words are spoken aloud. Plain sentences only: no markdown, lists, headings, emoji, \
URLs or code. Say numbers the way a person would.
- Be brief by default, one to three sentences, like a real conversation. Go longer only when \
asked to explain, draft or brainstorm, and even then keep it listenable.
- You can discuss anything: strategy, copy ideas, SEO, general knowledge, small talk. For \
facts about THIS agency (its clients, websites, runs, approvals, numbers), use only the \
AGENCY STATE below; if it is not there, say you don't have it. Never invent agency data.
- If a request is ambiguous, ask one short question back.
- The operator may interrupt you; that is normal.

Doing things: to act on the dashboard, end your reply with exactly one extra line that starts \
with ACTIONS: followed by a JSON array (at most 3 items). Never mention this line. Available:
  {"do":"open","page":"<page key>"}                       page keys: PAGES
  {"do":"open_record","kind":"project"|"client","id":"<id from RECORDS>"}
  {"do":"search","query":"<text>"}
  {"do":"draft_website","brief":"<full brief>"}            loads the website builder, operator presses Build
  {"do":"draft_agent_task","agent":"<@agent>","text":"<task>"}   agents: AGENTS
  {"do":"draft_browser_task","task":"<what to do>","url":"<optional https start page>"}
  {"do":"draft_media","mode":"text-to-image"|"text-to-video"|"image-to-image"|"image-to-video","prompt":"<description>"}
  {"do":"set_accent","accent":"teal"|"violet"|"sky"|"orchid"|"mono"}
  {"do":"set_density","value":"comfortable"|"compact"}
  {"do":"set_motion","value":"full"|"calm"|"off"}
  {"do":"sidebar","collapsed":true|false}
  {"do":"scroll","to":"up"|"down"|"top"|"bottom"}
  {"do":"back"}   {"do":"refresh"}   {"do":"end_conversation"}
You cannot start builds, approve, send, buy or delete anything; drafts wait for the operator. \
Say what you did in plain words ("I've opened approvals", "I've drafted that brief, press Build \
when you're happy"). Use end_conversation when the operator says goodbye or is done.

The operator is currently on: CURRENT_PAGE"""


def _overview_or_none():
    try:
        import agency
        return agency.overview()
    except Exception:
        return None


def _records(ov=None) -> str:
    """Ids the model may pass to open_record, with names to match speech to."""
    ov = ov if ov is not None else _overview_or_none()
    if ov is None:
        return "RECORDS: unavailable."
    rows = [f"project {p['id']} = {_clip(p.get('name'), 60)}" for p in (ov.get("projects") or [])
            if p.get("status") != "archived"][:30]
    rows += [f"client {c['id']} = {_clip(c.get('name'), 60)}" for c in (ov.get("clients") or [])][:20]
    return "RECORDS:\n" + ("\n".join(rows) if rows else "none")


def _record_ids(ov=None) -> set:
    ov = ov if ov is not None else _overview_or_none()
    if ov is None:
        return set()
    return ({("project", p["id"]) for p in ov.get("projects") or []}
            | {("client", c["id"]) for c in ov.get("clients") or []})


def clean_actions(raw: Any, record_ids: set | None = None) -> list[dict[str, Any]]:
    """Keep only well-formed actions from the vocabulary; drop everything else."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    ids = record_ids if record_ids is not None else _record_ids()

    def text(v, n):
        return " ".join(str(v or "").split())[:n]

    for a in raw:
        if not isinstance(a, dict) or len(out) >= MAX_ACTIONS:
            continue
        do = str(a.get("do", "")).strip().lower()
        act: dict[str, Any] | None = None
        if do == "open" and str(a.get("page", "")) in ROUTES:
            act = {"do": do, "page": str(a["page"])}
        elif do == "open_record" and (str(a.get("kind")), str(a.get("id"))) in ids:
            act = {"do": do, "kind": str(a["kind"]), "id": str(a["id"])}
        elif do == "search" and text(a.get("query"), 120):
            act = {"do": do, "query": text(a["query"], 120)}
        elif do == "draft_website" and len(text(a.get("brief"), 2000)) >= 8:
            act = {"do": do, "brief": text(a["brief"], 2000)}
        elif (do == "draft_agent_task" and str(a.get("agent", "")).lower() in AGENT_TARGETS
              and text(a.get("text"), 1500)):
            act = {"do": do, "agent": str(a["agent"]).lower(), "text": text(a["text"], 1500)}
        elif do == "draft_browser_task" and len(text(a.get("task"), 1500)) >= 5:
            url = str(a.get("url") or "").strip()
            act = {"do": do, "task": text(a["task"], 1500),
                   "url": url if re.match(r"^https?://\S+$", url) else ""}
        elif do == "draft_media" and a.get("mode") in MEDIA_MODES and text(a.get("prompt"), 2000):
            act = {"do": do, "mode": a["mode"], "prompt": text(a["prompt"], 2000)}
        elif do == "set_accent" and a.get("accent") in ACCENTS:
            act = {"do": do, "accent": a["accent"]}
        elif do == "set_density" and a.get("value") in ("comfortable", "compact"):
            act = {"do": do, "value": a["value"]}
        elif do == "set_motion" and a.get("value") in ("full", "calm", "off"):
            act = {"do": do, "value": a["value"]}
        elif do == "sidebar" and isinstance(a.get("collapsed"), bool):
            act = {"do": do, "collapsed": a["collapsed"]}
        elif do == "scroll" and a.get("to") in ("up", "down", "top", "bottom"):
            act = {"do": do, "to": a["to"]}
        elif do in ("back", "refresh", "end_conversation"):
            act = {"do": do}
        if act:
            out.append(act)
    return out


_MARK = "ACTIONS:"


def _split_actions(text: str) -> tuple[str, list]:
    """Spoken text before the ACTIONS line, and the parsed array after it."""
    i = text.find(_MARK)
    if i < 0:
        return text, []
    spoken, tail = text[:i], text[i + len(_MARK):]
    m = re.search(r"\[.*\]", tail, re.S)
    try:
        return spoken, _json.loads(m.group(0)) if m else []
    except ValueError:
        return spoken, []


def chat_events(text: str, history: list[dict[str, str]] | None = None, page: str = ""):
    """One conversational turn as a stream of (kind, data) events:
    meta, token (speakable text only), actions, done. Provider errors raise."""
    import providers
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("say something first")
    if len(text) > MAX_QUESTION * 2:
        raise ValueError("that is too long to answer by voice")
    page_key = re.sub(r"[^a-z0-9_/-]", "", str(page or "").lower().lstrip("#"))[:60]
    # detail pages (#project/<id>, #client/<id>) belong to their section
    section = {"project": "projects", "client": "clients", "agents": "agency"}.get(page_key.split("/")[0],
                                                                                   page_key.split("/")[0])
    here = ROUTES.get(section, "Overview") + (f" ({page_key})" if "/" in page_key else "")
    ov = _overview_or_none()
    system = (CHAT_SYSTEM.replace("PAGES", ", ".join(k or '""' for k in ROUTES))
              .replace("AGENTS", ", ".join(AGENT_TARGETS)).replace("CURRENT_PAGE", here)
              + "\n\n" + digest() + "\n\n" + _records(ov))
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-CHAT_HISTORY:]:
        role = "user" if turn.get("role") == "user" else "assistant"
        t = _clip(turn.get("text"), 1200)
        if t:
            messages.append({"role": role, "content": t})
    messages.append({"role": "user", "content": text})

    full, sent = "", 0
    for kind, data in providers.stream(messages, provider=providers.policy_for(text), timeout=90, effort="low"):
        if kind == "meta":
            yield "meta", data
            continue
        full += data
        # stream everything before the ACTIONS line; hold back a tail that
        # could be the start of the marker split across tokens
        cut = full.find(_MARK)
        safe = cut if cut >= 0 else max(sent, len(full) - len(_MARK))
        if safe > sent:
            yield "token", full[sent:safe]
            sent = safe
    spoken, raw_actions = _split_actions(full)
    if len(spoken) > sent:
        yield "token", spoken[sent:]
    acts = clean_actions(raw_actions, _record_ids(ov))
    if acts:
        yield "actions", acts
    yield "done", {"text": re.sub(r"[*_`#>]+", "", spoken).strip()}
