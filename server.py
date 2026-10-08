#!/usr/bin/env python3
"""Read-only Hermes Mission Control backend.

Python stdlib only. Hermes runtime databases are opened with SQLite read-only
URI mode and PRAGMA query_only=1. The local operator board uses read-write
SQLite in this project directory.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

import agency

HOST = os.environ.get("MISSION_CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("MISSION_CONTROL_PORT", "51764"))
PROJECT_DIR = Path(__file__).resolve().parent
INDEX_PATH = PROJECT_DIR / "app" / "q" / "index.html"
BRIDGE_DB = PROJECT_DIR / "bridge.db"
# Windows Hermes Desktop home. Override with HERMES_HOME if needed.
LOCAL_HERMES_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "hermes"
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or LOCAL_HERMES_HOME).expanduser()
HERMES_ENV_PATH = HERMES_HOME / ".env"
# Mission Control is often launched from different shells on Windows. Keep a
# hard fallback to the Hermes Desktop home so a stray HERMES_HOME or working
# directory cannot make bridge auth disappear.
DEFAULT_HERMES_ENV_PATH = LOCAL_HERMES_HOME / ".env"
UPLOAD_ROOT = Path(os.environ.get("MISSION_CONTROL_ATTACHMENTS_ROOT", str(PROJECT_DIR / "uploads"))).expanduser().resolve()
GATEWAY_CHAT_URL = os.environ.get("MISSION_CONTROL_GATEWAY_CHAT_URL", "http://127.0.0.1:8643/v1/chat/completions")
MAX_UPLOAD_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
MAX_INLINE_IMAGE_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_INLINE_IMAGE_BYTES", str(5 * 1024 * 1024)))
MAX_UPLOAD_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))
MAX_INLINE_IMAGE_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_INLINE_IMAGE_BYTES", str(5 * 1024 * 1024)))
MAX_TEXT_ATTACHMENT_CHARS = int(os.environ.get("MISSION_CONTROL_MAX_TEXT_ATTACHMENT_CHARS", "12000"))
TEXT_ATTACHMENT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
    ".yaml", ".yml", ".csv", ".html", ".htm", ".css", ".xml", ".log", ".ini",
    ".toml", ".sql", ".sh", ".bat",
}
TEXT_ATTACHMENT_MIME_EXACT = {
    "application/json", "application/xml", "application/javascript", "application/x-javascript",
    "application/x-yaml", "application/yaml",
}

BRIDGE_TARGETS = {
    "@orchestrator": {"agent": "orchestrator", "name": "Orchestrator", "code": "ORCH"},
    "@scout": {"agent": "scout", "name": "Scout", "code": "SCNT"},
    "@scribe": {"agent": "scribe", "name": "Scribe", "code": "SCRB"},
    "@reach": {"agent": "reach", "name": "Reach", "code": "RECH"},
    "@dev": {"agent": "dev", "name": "Dev", "code": "DEV"},
    "@lumen": {"agent": "lumen", "name": "Lumen", "code": "LMN"},
    "@forge": {"agent": "forge", "name": "Forge", "code": "FRG"},
    # Rank uses the main Hermes gateway. A previous dedicated Rank gateway on
    # localhost:8651 is optional and often not running; hardcoding it made
    # @rank / @all look broken with WinError 10061 even while the main gateway
    # was healthy.
    "@rank": {"agent": "rank", "name": "Rank", "code": "RANK"},
    "@stitch": {"agent": "stitch", "name": "Stitch", "code": "STCH"},
    "@all": {"agent": "all", "name": "All", "code": "ALL"},
}
BRIDGE_AGENT_TARGETS = [t for t in BRIDGE_TARGETS if t != "@all"]
EXPECTED_CLIENT_DISCONNECT_ERRNOS = {
    10053,  # WinError: connection aborted by local software/browser refresh
    10054,  # WinError: connection reset by peer
    10058,  # WinError: socket shutdown during send
    32,     # Broken pipe on POSIX
    104,    # Connection reset on POSIX
}


# ---------- Helpers ----------

def load_env_key(path: Path, key: str) -> str | None:
    """Load KEY=VALUE from .env, tolerating UTF-8 BOM and stray whitespace.

    Windows editors sometimes save .env with a BOM, which turns the first key
    into "\ufeffAPI_SERVER_KEY" for simple parsers. Strip it here so the bridge
    does not randomly lose auth after restarts.
    """
    try:
        for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.lstrip("\ufeff").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().lstrip("\ufeff") == key:
                return v.strip().strip('\"').strip("'") or None
    except (FileNotFoundError, OSError):
        return None
    return None


def hermes_env_paths() -> list[Path]:
    """Candidate Hermes .env locations, ordered from explicit to hard fallback."""
    candidates = [
        Path(os.environ["HERMES_ENV_PATH"]).expanduser() if os.environ.get("HERMES_ENV_PATH") else None,
        HERMES_ENV_PATH,
        DEFAULT_HERMES_ENV_PATH,
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
        Path.home() / ".hermes" / ".env",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            key = str(candidate.resolve()).lower()
        except OSError:
            key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def api_server_key() -> str | None:
    """Read the API key lazily and defensively from env or known Hermes homes."""
    for env_name in ("API_SERVER_KEY", "HERMES_API_SERVER_KEY"):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip().strip('\"').strip("'")
    for env_path in hermes_env_paths():
        for key_name in ("API_SERVER_KEY", "HERMES_API_SERVER_KEY"):
            value = load_env_key(env_path, key_name)
            if value:
                return value
    return None


def api_server_key_diagnostics() -> str:
    checked = ", ".join(str(p) for p in hermes_env_paths())
    return f"API_SERVER_KEY is not configured; checked: {checked}"


API_SERVER_KEY = api_server_key()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_expected_client_disconnect(exc: BaseException) -> bool:
    """Return True for normal browser/SSE disconnects that should not log tracebacks."""
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        errno = getattr(exc, "errno", None)
        return winerror in EXPECTED_CLIENT_DISCONNECT_ERRNOS or errno in EXPECTED_CLIENT_DISCONNECT_ERRNOS
    return False


def safe_write(handler: BaseHTTPRequestHandler, payload: bytes) -> bool:
    """Write to an HTTP client without letting browser disconnects kill request threads."""
    try:
        handler.wfile.write(payload)
        handler.wfile.flush()
        return True
    except OSError as exc:
        if is_expected_client_disconnect(exc):
            return False
        raise


def safe_call(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "data": fn(), "error": None}
    except Exception as exc:  # keep snapshot alive even if one data source breaks
        return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ro_sqlite(db_path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def format_duration(seconds: float | int | None) -> str | None:
    if seconds is None:
        return None
    try:
        seconds = int(max(0, seconds))
    except Exception:
        return None
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# ---------- Hermes read-only data functions ----------

def gateway_data() -> dict[str, Any]:
    path = HERMES_HOME / "gateway_state.json"
    data = read_json_file(path)
    updated_at = data.get("updated_at")
    start_time = data.get("start_time")
    uptime_seconds = None
    uptime = None
    # Hermes gateway_state.start_time may be a monotonic value, not wall-clock.
    # Only derive uptime if it looks like Unix wall-clock seconds.
    if isinstance(start_time, (int, float)) and start_time > 1_000_000_000:
        uptime_seconds = max(0, time.time() - float(start_time))
        uptime = format_duration(uptime_seconds)
    return {
        "state": data.get("gateway_state") or data.get("state"),
        "pid": data.get("pid"),
        "kind": data.get("kind"),
        "active_agents": data.get("active_agents", 0),
        "platforms": data.get("platforms", {}),
        "updated_at": updated_at,
        "start_time": start_time,
        "uptime_seconds": uptime_seconds,
        "uptime": uptime,
        "restart_requested": data.get("restart_requested"),
    }


def activity_data() -> dict[str, Any]:
    db = HERMES_HOME / "agent-logs.db"
    with ro_sqlite(db) as conn:
        last_entries = rows_to_dicts(conn.execute(
            """
            SELECT id, agent_name, task_description, model_used, status, created_at
            FROM agent_logs
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ).fetchall())
        totals = dict(conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
            FROM agent_logs
            """
        ).fetchone())
        per_agent = []
        agents = conn.execute("SELECT DISTINCT agent_name FROM agent_logs ORDER BY agent_name").fetchall()
        for r in agents:
            agent = r["agent_name"]
            stats = dict(conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                FROM agent_logs WHERE agent_name=?
                """, (agent,)
            ).fetchone())
            last = conn.execute(
                """
                SELECT task_description, created_at, model_used
                FROM agent_logs
                WHERE agent_name=?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """, (agent,)
            ).fetchone()
            total = stats.get("total") or 0
            per_agent.append({
                "agent_name": agent,
                "total": total,
                "responses": total,
                "completed": stats.get("completed") or 0,
                "failed": stats.get("failed") or 0,
                "last_task": last["task_description"] if last else None,
                "last_seen": last["created_at"] if last else None,
                "model": last["model_used"] if last else None,
            })
        since = (datetime.now(timezone.utc) - timedelta(days=6)).date().isoformat()
        daily_rows = rows_to_dicts(conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
            FROM agent_logs
            WHERE substr(created_at, 1, 10) >= ?
            GROUP BY day
            ORDER BY day ASC
            """, (since,)
        ).fetchall())
        daily_agent_rows = rows_to_dicts(conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day,
                   lower(agent_name) AS agent_name,
                   COUNT(*) AS total
            FROM agent_logs
            WHERE substr(created_at, 1, 10) >= ?
            GROUP BY day, lower(agent_name)
            ORDER BY day ASC, lower(agent_name) ASC
            """, (since,)
        ).fetchall())
    by_day = {r["day"]: r for r in daily_rows}
    agents_by_day: dict[str, dict[str, int]] = defaultdict(dict)
    for row in daily_agent_rows:
        agents_by_day[row["day"]][row["agent_name"]] = row.get("total", 0) or 0
    daily = []
    today = datetime.now(timezone.utc).date()
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        row = by_day.get(day, {})
        daily.append({
            "day": day,
            "total": row.get("total", 0),
            "completed": row.get("completed", 0),
            "failed": row.get("failed", 0),
            "agents": agents_by_day.get(day, {}),
        })
    totals = {k: (v or 0) for k, v in totals.items()}
    return {"last_entries": last_entries, "per_agent": per_agent, "totals": totals, "daily_7d": daily}


def sessions_data() -> dict[str, Any]:
    db = HERMES_HOME / "state.db"
    with ro_sqlite(db) as conn:
        session_count = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        message_count = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        token_totals = dict(conn.execute(
            """
            SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(api_call_count),0) AS api_call_count,
                   COALESCE(SUM(tool_call_count),0) AS tool_call_count
            FROM sessions
            """
        ).fetchone())
        recent = rows_to_dicts(conn.execute(
            """
            SELECT id, source, user_id, model, parent_session_id, started_at, ended_at,
                   end_reason, message_count, tool_call_count, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, reasoning_tokens, title,
                   api_call_count, estimated_cost_usd, actual_cost_usd, cost_status
            FROM sessions
            ORDER BY started_at DESC
            LIMIT 25
            """
        ).fetchall())
    return {
        "session_count": session_count,
        "message_count": message_count,
        "token_totals": token_totals,
        "recent_sessions": recent,
    }


def _read_proc_stat() -> tuple[int, int]:
    line = Path("/proc/stat").read_text().splitlines()[0]
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return idle, total


def _read_meminfo() -> dict[str, int]:
    out = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, val = line.split(":", 1)
        out[key] = int(val.strip().split()[0]) * 1024
    return out


def vps_health() -> dict[str, Any]:
    idle1, total1 = _read_proc_stat()
    time.sleep(0.1)
    idle2, total2 = _read_proc_stat()
    total_delta = total2 - total1
    idle_delta = idle2 - idle1
    cpu_percent = 0.0 if total_delta <= 0 else round((1.0 - idle_delta / total_delta) * 100, 1)
    mem = _read_meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", 0)
    mem_used = max(0, mem_total - mem_avail)
    st = os.statvfs("/")
    disk_total = st.f_blocks * st.f_frsize
    disk_free = st.f_bavail * st.f_frsize
    disk_used = disk_total - disk_free
    return {
        "cpu_percent": cpu_percent,
        "ram": {
            "total": mem_total,
            "available": mem_avail,
            "used": mem_used,
            "percent": round((mem_used / mem_total) * 100, 1) if mem_total else 0,
        },
        "disk": {
            "path": "/",
            "total": disk_total,
            "free": disk_free,
            "used": disk_used,
            "percent": round((disk_used / disk_total) * 100, 1) if disk_total else 0,
        },
    }


DOW = {'0':'Sunday','1':'Monday','2':'Tuesday','3':'Wednesday','4':'Thursday','5':'Friday','6':'Saturday','7':'Sunday'}


def field_desc(field: str, unit: str) -> str:
    if field == "*":
        return f"every {unit}"
    if field.startswith("*/"):
        return f"every {field[2:]} {unit}s"
    return field


def cron_to_english(minute: str, hour: str, dom: str, month: str, dow: str) -> str:
    time_part = ""
    if minute != "*" and hour != "*":
        try:
            time_part = f"at {int(hour):02d}:{int(minute):02d}"
        except Exception:
            time_part = f"at hour {hour} minute {minute}"
    else:
        time_part = f"{field_desc(minute, 'minute')}, {field_desc(hour, 'hour')}"
    if dom != "*":
        return f"{time_part} on day {dom} of each month"
    if dow != "*":
        return f"{time_part} on {DOW.get(dow, dow)}"
    return f"{time_part} daily"


def parse_cron_file(path: Path, system_file: bool) -> list[dict[str, Any]]:
    jobs = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception as exc:
        return [{"source": str(path), "label": "system", "error": f"{type(exc).__name__}: {exc}"}]
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        sched = parts[:5]
        if system_file and len(parts) >= 7:
            user = parts[5]
            command = " ".join(parts[6:])
        else:
            user = None
            command = " ".join(parts[5:])
        label = "hermes" if "hermes" in command.lower() or "agent-logs" in command.lower() else "system"
        jobs.append({
            "source": str(path),
            "line": i,
            "schedule": " ".join(sched),
            "english": cron_to_english(*sched),
            "user": user,
            "command": command,
            "label": label,
        })
    return jobs


def cron_jobs() -> dict[str, Any]:
    jobs = []
    paths = [(Path("/var/spool/cron/crontabs/root"), False), (Path("/etc/crontab"), True)]
    cron_d = Path("/etc/cron.d")
    if cron_d.exists():
        for p in sorted(cron_d.iterdir()):
            if p.is_file():
                paths.append((p, True))
    for p, is_system in paths:
        jobs.extend(parse_cron_file(p, is_system))
    return {"jobs": jobs, "count": len([j for j in jobs if "command" in j])}


def normalized_crons(cron_payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for job in cron_payload.get("jobs", []) if isinstance(cron_payload, dict) else []:
        if not job.get("command"):
            continue
        owner = job.get("owner") or job.get("label") or "system"
        if owner not in {"hermes", "system"}:
            owner = "system"
        out.append({
            "source": job.get("source"),
            "schedule": job.get("schedule"),
            "command": job.get("command"),
            "owner": owner,
            "description": job.get("description") or job.get("english"),
        })
    return out



def _timeline_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    parsed = parse_iso(str(value))
    if parsed:
        return parsed.timestamp()
    try:
        return float(value)
    except Exception:
        return 0.0


def _short_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def flight_recorder_data(limit: int = 80) -> dict[str, Any]:
    """Build an inspectable cockpit timeline from Mission Control data sources."""
    limit = max(1, min(250, int(limit or 80)))
    events: list[dict[str, Any]] = []

    activity_section = safe_call("activity", activity_data)
    for row in (_payload(activity_section, {}) or {}).get("last_entries", []) or []:
        agent = str(row.get("agent_name") or "agent").lower()
        status = str(row.get("status") or "logged")
        events.append({
            "id": f"activity-{row.get('id', len(events))}",
            "ts": row.get("created_at"),
            "kind": "agent_log",
            "agent": agent,
            "status": status,
            "title": f"{agent.title()} {status}",
            "detail": _short_text(row.get("task_description") or "Agent activity logged."),
            "artifact": row.get("model_used") or "agent-logs.db",
            "source": "agent-logs.db",
        })

    for row in bridge_history(limit):
        target = str(row.get("target") or "@bridge")
        response = _short_text(row.get("response"), 260)
        events.append({
            "id": f"bridge-{row.get('id')}",
            "ts": row.get("ts"),
            "kind": "command",
            "agent": target.lstrip("@") or "bridge",
            "status": "completed" if response else "sent",
            "title": f"Command Bridge → {target}",
            "detail": _short_text(row.get("message") or "Command sent."),
            "artifact": response,
            "source": "bridge.db",
        })

    sessions_section = safe_call("sessions", sessions_data)
    for row in (_payload(sessions_section, {}) or {}).get("recent_sessions", []) or []:
        title = row.get("title") or row.get("source") or "Hermes session"
        events.append({
            "id": f"session-{row.get('id')}",
            "ts": row.get("started_at"),
            "kind": "session",
            "agent": str(row.get("source") or "session"),
            "status": str(row.get("end_reason") or "active"),
            "title": _short_text(title, 120),
            "detail": f"{row.get('message_count') or 0} messages · {row.get('tool_call_count') or 0} tool calls",
            "artifact": row.get("model") or row.get("id"),
            "source": "state.db",
        })

    events.sort(key=lambda e: _timeline_ts(e.get("ts")), reverse=True)
    events = events[:limit]
    by_kind: dict[str, int] = defaultdict(int)
    blockers = 0
    approvals = 0
    for event in events:
        by_kind[str(event.get("kind") or "unknown")] += 1
        text = f"{event.get('status','')} {event.get('title','')} {event.get('detail','')}".lower()
        if any(word in text for word in ("failed", "blocked", "error", "stuck", "timeout")):
            blockers += 1
        if any(word in text for word in ("approval", "review", "approve", "decision")):
            approvals += 1
    return {
        "ok": True,
        "generated_at": now_iso(),
        "summary": {
            "events": len(events),
            "blockers": blockers,
            "approvals": approvals,
            "sources": len([k for k, v in by_kind.items() if v]),
            "by_kind": dict(by_kind),
        },
        "events": events,
    }


# ---------- Command Bridge ----------

def bridge_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(BRIDGE_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_bridge() -> None:
    with bridge_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bridge_history (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            target TEXT NOT NULL,
            message TEXT NOT NULL,
            response TEXT DEFAULT ''
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bridge_favorites (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            name TEXT NOT NULL,
            target TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bridge_history_ts ON bridge_history(ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bridge_favorites_ts ON bridge_favorites(ts DESC)")


def validate_bridge_target(target: str | None) -> str:
    target = str(target or "").strip().lower()
    if target not in BRIDGE_TARGETS:
        raise ValueError("invalid bridge target")
    return target


def bridge_history(limit: int = 50) -> list[dict[str, Any]]:
    init_bridge()
    limit = max(1, min(200, int(limit or 50)))
    with bridge_conn() as conn:
        return rows_to_dicts(conn.execute("SELECT id, ts, target, message, response FROM bridge_history ORDER BY ts DESC LIMIT ?", (limit,)).fetchall())


def save_bridge_history(target: str, message: str, response: str) -> str:
    init_bridge()
    item_id = str(uuid.uuid4())
    with bridge_conn() as conn:
        conn.execute("INSERT INTO bridge_history (id,ts,target,message,response) VALUES (?,?,?,?,?)", (item_id, now_iso(), target, message, response))
    return item_id


def ensure_upload_root() -> Path:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return UPLOAD_ROOT


def sanitize_upload_name(name: str) -> str:
    cleaned = Path(str(name or "upload")).name.strip().replace("\x00", "")
    cleaned = "".join(ch if ch.isalnum() or ch in {".", "-", "_", " "} else "_" for ch in cleaned)
    cleaned = " ".join(cleaned.split())[:180].strip(" .")
    return cleaned or "upload"


def guess_attachment_kind(mime_type: str, name: str) -> str:
    mime = str(mime_type or "").lower()
    suffix = Path(name).suffix.lower()
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if mime.startswith("text/") or mime in TEXT_ATTACHMENT_MIME_EXACT or suffix in TEXT_ATTACHMENT_SUFFIXES:
        return "text"
    return "file"


def attachment_public_url(stored_name: str) -> str:
    return f"/api/uploads/{stored_name}"


def attachment_path_from_meta(meta: dict[str, Any]) -> Path:
    stored_name = Path(str(meta.get("stored_name") or meta.get("id") or "")).name
    if not stored_name:
        raise ValueError("stored_name is required")
    path = (ensure_upload_root() / stored_name).resolve()
    try:
        path.relative_to(ensure_upload_root().resolve())
    except ValueError as exc:
        raise ValueError("attachment path escapes upload root") from exc
    return path


def decode_attachment_payload(item: dict[str, Any], filename: str) -> tuple[bytes, str]:
    mime_type = str(item.get("mime_type") or item.get("type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    data_url = item.get("data_url")
    if data_url:
        prefix, _, encoded = str(data_url).partition(",")
        if not prefix.startswith("data:") or not encoded:
            raise ValueError(f"{filename} has an invalid data URL")
        header = prefix[5:]
        if ";base64" not in header:
            raise ValueError(f"{filename} must use base64 data URLs")
        mime_type = header.split(";", 1)[0] or mime_type
        return base64.b64decode(encoded, validate=True), mime_type
    data_base64 = item.get("data_base64")
    if data_base64:
        return base64.b64decode(str(data_base64), validate=True), mime_type
    raise ValueError(f"{filename} is missing file data")


def build_attachment_meta(filename: str, mime_type: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{filename} exceeds the {MAX_UPLOAD_BYTES}-byte upload limit")
    stored_name = f"{uuid.uuid4().hex}_{filename}"
    path = ensure_upload_root() / stored_name
    path.write_bytes(data)
    meta = {
        "id": stored_name,
        "name": filename,
        "stored_name": stored_name,
        "mime_type": mime_type,
        "size": len(data),
        "kind": guess_attachment_kind(mime_type, filename),
        "url": attachment_public_url(stored_name),
        "path": str(path),
        "preview": "",
    }
    if is_text_attachment(meta):
        meta["preview"] = read_text_attachment(meta)
    return meta


def normalize_attachment_payload(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    stored_name = Path(str(item.get("stored_name") or item.get("id") or "")).name
    if stored_name:
        name = sanitize_upload_name(str(item.get("name") or stored_name or "upload"))
        mime_type = str(item.get("mime_type") or item.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
        try:
            path = attachment_path_from_meta({"stored_name": stored_name})
            size = path.stat().st_size
        except Exception:
            return None
        meta = {
            "id": str(item.get("id") or stored_name),
            "name": name,
            "stored_name": stored_name,
            "mime_type": mime_type,
            "size": size,
            "kind": guess_attachment_kind(mime_type, name),
            "url": attachment_public_url(stored_name),
            "path": str(path),
            "preview": "",
        }
        if is_text_attachment(meta):
            meta["preview"] = read_text_attachment(meta)
        return meta
    filename = sanitize_upload_name(str(item.get("name") or "upload"))
    try:
        data, mime_type = decode_attachment_payload(item, filename)
    except Exception:
        return None
    return build_attachment_meta(filename, mime_type, data)


def normalize_attachments_payload(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items[:8]:
        meta = normalize_attachment_payload(item)
        if not meta:
            continue
        key = str(meta.get("stored_name") or meta.get("id") or meta.get("name"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(meta)
    return normalized


def is_text_attachment(meta: dict[str, Any]) -> bool:
    mime = str(meta.get("mime_type") or "").lower()
    name = str(meta.get("name") or "")
    return str(meta.get("kind") or "") == "text" or mime.startswith("text/") or mime in TEXT_ATTACHMENT_MIME_EXACT or Path(name).suffix.lower() in TEXT_ATTACHMENT_SUFFIXES


def is_image_attachment(meta: dict[str, Any]) -> bool:
    mime = str(meta.get("mime_type") or "").lower()
    name = str(meta.get("name") or "")
    return str(meta.get("kind") or "") == "image" or mime.startswith("image/") or Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def read_text_attachment(meta: dict[str, Any], max_chars: int = MAX_TEXT_ATTACHMENT_CHARS) -> str:
    path = attachment_path_from_meta(meta)
    raw = path.read_bytes()[: max_chars * 4]
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = text.replace(chr(13) + chr(10), "\n").replace(chr(13), "\n")
    text = text.replace("\x00", " ")
    return text[:max_chars].strip()


def attachment_data_url(meta: dict[str, Any]) -> str | None:
    path = attachment_path_from_meta(meta)
    size = path.stat().st_size
    if size > MAX_INLINE_IMAGE_BYTES:
        return None
    mime_type = str(meta.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return f"data:{mime_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def attachment_prompt_block(meta: dict[str, Any]) -> str:
    name = str(meta.get("name") or meta.get("stored_name") or "attachment")
    mime_type = str(meta.get("mime_type") or "application/octet-stream")
    size = int(meta.get("size") or 0)
    path = str(meta.get("path") or attachment_path_from_meta(meta))
    header = f"Attachment: {name} ({mime_type}, {size} bytes)\nSaved path: {path}"
    if is_text_attachment(meta):
        excerpt = str(meta.get("preview") or read_text_attachment(meta))
        if excerpt:
            return header + "\nUse read_file on the saved path if you need more context.\nText excerpt:\n```\n" + excerpt + "\n```"
        return header + "\nUse read_file on the saved path if you need more context. The text file appears empty."
    if is_image_attachment(meta):
        note = "Use vision_analyze on the saved path for detailed inspection."
        if size <= MAX_INLINE_IMAGE_BYTES:
            note += " An inline image copy is also attached below for ChatGPT-style multimodal analysis."
        else:
            note += f" The image exceeds the inline vision limit of {MAX_INLINE_IMAGE_BYTES} bytes, so only the saved path is available."
        return header + "\n" + note
    return header + "\nBinary file attached. Refer to the saved path and filename in your response."


def build_user_message_content(message: str, attachments: list[dict[str, Any]] | None = None) -> Any:
    attachments = attachments or []
    text = str(message or "").strip()
    attachment_blocks = [attachment_prompt_block(meta) for meta in attachments]
    if attachment_blocks:
        intro = text or "Please inspect the attached files and help with them."
        text = intro + "\n\nAttached files:\n\n" + "\n\n".join(attachment_blocks)
    if not attachments:
        return text
    content: list[dict[str, Any]] = [{"type": "text", "text": text or "Please inspect the attached files and help with them."}]
    for meta in attachments:
        if is_image_attachment(meta):
            data_url = attachment_data_url(meta)
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
    return content


def log_bridge(target: str, status: str = "completed") -> None:
    try:
        agency.bridge_run_finished(target, status)
    except Exception:
        pass  # run bookkeeping must never break the bridge stream
    script = HERMES_HOME / "agents" / "_shared" / "log-task-local.sh"
    if not script.exists():
        return
    try:
        subprocess.run(["bash", str(script), "bridge", f"Command Bridge sent to {target}"[:140], status], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except Exception:
        pass


def sse_payload(agent: str, kind: str, data: Any) -> bytes:
    return ("data: " + json.dumps({"agent": agent, "type": kind, "data": data}, ensure_ascii=False) + "\n\n").encode("utf-8")


def sse_status(agent: str, status: str, task: str, detail: str = "") -> bytes:
    return sse_payload(agent, "status", {"status": status, "task": task[:500], "detail": detail[:240], "ts": now_iso()})


def is_status_check(message: str) -> bool:
    text = " ".join(str(message or "").lower().split())
    if not text:
        return False

    # The dashboard prompt often includes target mentions before the actual
    # request, e.g. "@orchestrator @Orchestrator, check if all the agents are
    # running?". Treat these as local health checks instead of routing them to
    # the LLM gateway, so status probes work even when auth/gateway state is
    # being diagnosed.
    status_phrases = (
        "status",
        "status check",
        "check status",
        "health",
        "ping",
        "are you online",
        "online",
        "are running",
        "agents are running",
        "agent is running",
        "check if all",
        "check all agents",
        "all agents running",
        "are all agents running",
    )
    return any(phrase in text for phrase in status_phrases)


def local_status_text(target: str) -> str:
    meta = BRIDGE_TARGETS.get(target) or BRIDGE_TARGETS.get("@orchestrator", {})
    name = meta.get("name") or meta.get("agent") or target.lstrip("@")
    role = {
        "@orchestrator": "coordination and routing",
        "@scout": "research and context gathering",
        "@scribe": "writing, notes, and summaries",
        "@reach": "marketing and outreach",
        "@dev": "development and integrations",
        "@lumen": "UI/UX and visual polish",
        "@forge": "build and hardening",
        "@rank": "ranking, SEO, and prioritization",
        "@stitch": "UI generation, visual prototyping, and Stitch design-to-build handoff",
    }.get(target, "agent work")
    gateway = gateway_data()
    state = gateway.get("state") or "unknown"
    return f"{name} online. Status: ready. Role: {role}. Gateway: {state}."


def iter_fast_status(target: str):
    if target == "@all":
        for one_target in BRIDGE_AGENT_TARGETS:
            agent = BRIDGE_TARGETS[one_target]["agent"]
            yield {"agent": agent, "type": "token", "data": local_status_text(one_target)}
            yield {"agent": agent, "type": "done", "data": ""}
        return
    agent = BRIDGE_TARGETS[target]["agent"]
    yield {"agent": agent, "type": "token", "data": local_status_text(target)}
    yield {"agent": agent, "type": "done", "data": ""}


def _clean_bridge_history(history: Any, target: str, max_items: int = 12) -> list[dict[str, Any]]:
    """Convert browser bridge transcript into OpenAI-style context messages.

    The dashboard bridge opens a fresh HTTP request for each send, so follow-up
    replies must include recent transcript context explicitly. Keep this small
    and target-scoped to avoid dragging unrelated agents into the prompt.
    """
    if not isinstance(history, list):
        return []
    wanted_agent = BRIDGE_TARGETS.get(target, {}).get("agent")
    cleaned: list[dict[str, Any]] = []
    for item in history[-max_items:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        text = str(item.get("text") or "").strip()
        attachments = normalize_attachments_payload(item.get("attachments"))
        if role == "user":
            if text or attachments:
                cleaned.append({"role": "user", "content": build_user_message_content(text, attachments)})
            continue
        if role == "agent":
            if not text:
                continue
            agent = str(item.get("agent") or "").lower()
            if wanted_agent and agent and agent != wanted_agent:
                continue
            cleaned.append({"role": "assistant", "content": text[:1600]})
    while cleaned and cleaned[-1]["role"] == "user":
        cleaned.pop()
    return cleaned


def bridge_messages(agent_name: str, message: str, conversation_id: str | None = None, history: Any = None, target: str = "", attachments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    system = f"You are {agent_name} in the eleven-channel AgentOS/Mission Control crew. Answer as that agent or channel, keep role boundaries, and be concise unless asked for a deliverable. Crew: Orchestrator, Scout, Scribe, Reach, Dev, Lumen, Antigravity, ChatGPT, Forge, Rank, Stitch."
    if conversation_id:
        system += f" Conversation id: {conversation_id}."
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(_clean_bridge_history(history, target))
    messages.append({"role": "user", "content": build_user_message_content(message, normalize_attachments_payload(attachments))})
    return messages


def _gateway_tokens(target: str, messages: list[dict[str, Any]], conversation_id: str | None = None):
    """Token stream from the Hermes gateway, for when it is in the brain chain.

    The gateway answers 200 even when its upstream refused, streaming the
    provider's complaint ("HTTP 401: User not found.") as if it were the
    reply. The opening is held back and checked, so an envelope becomes a
    failure the chain can fall back from rather than an answer on screen.
    """
    import llm
    meta = BRIDGE_TARGETS[target]
    key = api_server_key()
    if not key:
        raise RuntimeError(api_server_key_diagnostics() or "API_SERVER_KEY not found")
    url = meta.get("api_url") or GATEWAY_CHAT_URL
    body = {"model": meta.get("model") or "hermes-agent", "stream": True, "messages": messages,
            "metadata": {"bridge_target": target, "conversation_id": conversation_id or ""}}
    req = Request(url, data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    held, released = "", False
    with urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":") or line.startswith("event:"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            piece = (choice.get("delta") or choice.get("message") or {}).get("content")
            if piece:
                if released:
                    yield piece
                else:
                    held += piece
                    if len(held) >= 120:
                        complaint = llm._provider_error(held)
                        if complaint:
                            raise RuntimeError(f"gateway upstream refused: {complaint}")
                        released = True
                        yield held
            if choice.get("finish_reason"):
                break
    if not released and held:
        complaint = llm._provider_error(held)
        if complaint:
            raise RuntimeError(f"gateway upstream refused: {complaint}")
        yield held


def iter_brain_stream(target: str, message: str, conversation_id: str | None = None, history: Any = None,
                      attachments: list[dict[str, Any]] | None = None):
    """An agent's reply, thought by the brain chain (Claude, ChatGPT, local Ollama).

    The agent keeps its identity through the system prompt; which model
    answered is announced in a "meta" event so the page can say so.
    """
    import providers
    meta = BRIDGE_TARGETS[target]
    agent = meta["agent"]
    messages = bridge_messages(meta["name"], message, conversation_id, history, target, attachments)
    # the SEO agent, and SEO questions to any agent, think only with Claude or ChatGPT
    policy = providers.policy_for(message, agent)
    try:
        for kind, data in providers.stream(
                messages, provider=policy,
                hermes=lambda msgs: _gateway_tokens(target, msgs, conversation_id)):
            yield {"agent": agent, "type": kind, "data": data}
        yield {"agent": agent, "type": "done", "data": ""}
    except Exception as exc:
        yield {"agent": agent, "type": "done", "data": str(exc)}


def _payload(section: dict[str, Any], default: Any = None) -> Any:
    if isinstance(section, dict) and section.get("ok"):
        return section.get("data") if section.get("data") is not None else default
    return default


def snapshot() -> dict[str, Any]:
    gateway_section = safe_call("gateway", gateway_data)
    activity_section = safe_call("activity", activity_data)
    sessions_section = safe_call("sessions", sessions_data)
    vps_section = safe_call("vps_health", vps_health)
    cron_section = safe_call("cron_jobs", cron_jobs)

    activity = _payload(activity_section, {}) or {}
    sessions = _payload(sessions_section, {}) or {}
    vps = _payload(vps_section, {}) or {}
    gateway = _payload(gateway_section, {}) or {}
    cron = _payload(cron_section, {}) or {}

    ram = vps.get("ram") or {}
    disk = vps.get("disk") or {}
    agents = activity.get("per_agent") or []
    stats = activity.get("totals") or {"total": 0, "completed": 0, "failed": 0}
    totals = sessions.get("token_totals") or {}

    db_size = 0
    for db_name in ("state.db", "agent-logs.db", "cronjobs.db"):
        db_path = HERMES_HOME / db_name
        try:
            db_size += db_path.stat().st_size
        except FileNotFoundError:
            pass

    return {
        "generated_at": now_iso(),
        "hermes_home": str(HERMES_HOME),
        "gateway": {**gateway, "uptime_seconds": gateway.get("uptime_seconds") or 0},
        "activity": activity.get("last_entries") or [],
        "agents": agents,
        "activity_by_day": activity.get("daily_7d") or [],
        "stats": stats,
        "sessions": {
            "count": sessions.get("session_count", 0),
            "recent": sessions.get("recent_sessions", []),
            "totals": {
                "messages": sessions.get("message_count", 0),
                "input_tokens": totals.get("input_tokens", 0),
                "output_tokens": totals.get("output_tokens", 0),
                "cache_read_tokens": totals.get("cache_read_tokens", 0),
                "cache_write_tokens": totals.get("cache_write_tokens", 0),
                "reasoning_tokens": totals.get("reasoning_tokens", 0),
            },
        },
        "vps": {
            "cpu_pct": vps.get("cpu_percent", 0),
            "mem_pct": ram.get("percent", 0),
            "mem_used_mb": round((ram.get("used", 0) or 0) / 1024 / 1024),
            "mem_total_mb": round((ram.get("total", 0) or 0) / 1024 / 1024),
            "disk_pct": disk.get("percent", 0),
            "disk_used_gb": round((disk.get("used", 0) or 0) / 1024 / 1024 / 1024, 1),
            "disk_total_gb": round((disk.get("total", 0) or 0) / 1024 / 1024 / 1024, 1),
            "db_size_mb": round(db_size / 1024 / 1024, 2),
        },
        "cron_jobs": cron_section,
        "crons": normalized_crons(cron),
        "raw": {
            "gateway": gateway_section,
            "activity": activity_section,
            "sessions": sessions_section,
            "vps_health": vps_section,
        },
    }


# ---------- HTTP ----------

class RobustThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded server tuned for a local dashboard that browsers may refresh often."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address) -> None:
        exc_type, exc, _tb = sys.exc_info()
        if exc is not None and is_expected_client_disconnect(exc):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesMissionControl/3.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, obj: Any, status: int = 200) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        safe_write(self, payload)

    def send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        safe_write(self, payload)

    def send_head_only(self, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8"))
        return {k: v[0] if len(v)==1 else v for k,v in parse_qs(raw.decode("utf-8")).items()}

    def send_bridge_stream(self, target: str, message: str, conversation_id: str | None = None, history: Any = None, attachments: list[dict[str, Any]] | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        if not is_status_check(message):
            try:
                agency.bridge_run_started(target, message)
            except Exception:
                pass
        responses: dict[str, list[str]] = defaultdict(list)
        try:
            if is_status_check(message):
                for event in iter_fast_status(target):
                    agent = str(event.get("agent") or "bridge")
                    kind = str(event.get("type") or "token")
                    data = event.get("data", "")
                    if kind == "token":
                        responses[agent].append(str(data))
                        if not safe_write(self, sse_status(agent, "running", message, "local health check")):
                            log_bridge(target, "failed")
                            return
                    if not safe_write(self, sse_payload(agent, kind, data)):
                        log_bridge(target, "failed")
                        return
                    if kind == "done":
                        if not safe_write(self, sse_status(agent, "completed", message, "local health check complete")):
                            log_bridge(target, "failed")
                            return
                merged = "\n\n".join(f"[{agent}] {''.join(parts)}" for agent, parts in sorted(responses.items()))
                save_bridge_history(target, message, merged)
                log_bridge(target, "completed")
                return
            if target == "@all":
                q: queue.Queue[dict[str, Any]] = queue.Queue()
                remaining = len(BRIDGE_AGENT_TARGETS)
                def worker(one_target: str) -> None:
                    agent_name = BRIDGE_TARGETS[one_target]["agent"]
                    q.put({"agent": agent_name, "type": "status", "data": {"status": "running", "task": message, "detail": "request started", "ts": now_iso()}})
                    for event in iter_brain_stream(one_target, message, conversation_id, history, attachments):
                        q.put(event)
                    q.put({"agent": agent_name, "type": "status", "data": {"status": "completed", "task": message, "detail": "stream complete", "ts": now_iso()}})
                for one_target in BRIDGE_AGENT_TARGETS:
                    threading.Thread(target=worker, args=(one_target,), daemon=True).start()
                while remaining > 0:
                    event = q.get()
                    agent = str(event.get("agent") or "bridge")
                    kind = str(event.get("type") or "token")
                    data = event.get("data", "")
                    if kind == "token":
                        responses[agent].append(str(data))
                    elif kind == "done":
                        remaining -= 1
                    if not safe_write(self, sse_payload(agent, kind, data)):
                        log_bridge(target, "failed")
                        return
                merged = "\n\n".join(f"[{agent}] {''.join(parts)}" for agent, parts in sorted(responses.items()))
                save_bridge_history(target, message, merged)
                log_bridge(target, "completed")
                return
            agent = BRIDGE_TARGETS[target]["agent"]
            if not safe_write(self, sse_status(agent, "running", message, "request started")):
                log_bridge(target, "failed")
                return
            saw_done = False
            for event in iter_brain_stream(target, message, conversation_id, history, attachments):
                if event.get("type") == "token":
                    responses[agent].append(str(event.get("data", "")))
                if event.get("type") == "done":
                    saw_done = True
                if not safe_write(self, sse_payload(str(event.get("agent") or agent), str(event.get("type") or "token"), event.get("data", ""))):
                    log_bridge(target, "failed")
                    return
            if not saw_done:
                if not safe_write(self, sse_payload(agent, "done", "")):
                    log_bridge(target, "failed")
                    return
            if not safe_write(self, sse_status(agent, "completed", message, "stream complete")):
                log_bridge(target, "failed")
                return
            save_bridge_history(target, message, "".join(responses[agent]))
            log_bridge(target, "completed")
            return
        except OSError as exc:
            if not is_expected_client_disconnect(exc):
                raise
            log_bridge(target, "failed")
            return
        except Exception as exc:
            safe_write(self, sse_payload("bridge", "done", f"{type(exc).__name__}: {exc}"))
            log_bridge(target, "failed")

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        html_paths = {"/", "/index.html"}
        json_paths = {
            "/health", "/status", "/snapshot", "/agents", "/sessions",
            "/api/health", "/api/status", "/api/snapshot", "/api/agents", "/api/sessions",
            "/api/bridge/history", "/api/flight-recorder",
        }
        if parsed.path in html_paths or (not parsed.path.startswith("/api/") and parsed.path not in json_paths and parsed.path != "/events"):
            self.send_head_only(200, "text/html; charset=utf-8")
            return
        if parsed.path in json_paths:
            self.send_head_only(200, "application/json; charset=utf-8")
            return
        if parsed.path == "/events":
            self.send_head_only(200, "text/event-stream")
            return
        if parsed.path.startswith("/api/uploads/"):
            self.send_head_only(200, mimetypes.guess_type(parsed.path)[0] or "application/octet-stream")
            return
        self.send_head_only(404, "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if agency.handle_get(self, parsed):
            return
        if parsed.path in {"/health", "/api/health"}:
            snap = snapshot()
            self.send_json({
                "ok": True,
                "service": "mission-control",
                "generated_at": snap.get("generated_at"),
                "gateway_state": (snap.get("gateway") or {}).get("state"),
                "active_agents": (snap.get("gateway") or {}).get("active_agents", 0),
            })
            return
        if parsed.path in {"/status", "/api/status"}:
            snap = snapshot()
            self.send_json({
                "ok": True,
                "generated_at": snap.get("generated_at"),
                "gateway": snap.get("gateway"),
                "stats": snap.get("stats"),
                "vps": snap.get("vps"),
            })
            return
        if parsed.path in {"/snapshot", "/api/snapshot"}:
            self.send_json(snapshot())
            return
        if parsed.path in {"/agents", "/api/agents"}:
            snap = snapshot()
            self.send_json({"ok": True, "agents": snap.get("agents", []), "stats": snap.get("stats", {})})
            return
        if parsed.path in {"/sessions", "/api/sessions"}:
            snap = snapshot()
            self.send_json({"ok": True, "sessions": snap.get("sessions", {})})
            return
        if parsed.path == "/api/flight-recorder":
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or [80])[0])
                self.send_json(flight_recorder_data(limit))
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            return
        # Serve the dashboard shell from / and common browser/SPAs paths.
        # Some users/bookmarks open /index.html; client-side routes should also
        # fall back to the dashboard instead of the stdlib 404 page.
        if parsed.path == "/" or parsed.path == "/index.html" or not parsed.path.startswith("/api/") and parsed.path != "/events":
            try:
                data = INDEX_PATH.read_bytes()
            except FileNotFoundError:
                self.send_error(404, "index.html not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            safe_write(self, data)
            return
        if parsed.path.startswith("/api/uploads/"):
            try:
                stored_name = Path(parsed.path.rsplit("/", 1)[-1]).name
                path = attachment_path_from_meta({"stored_name": stored_name})
                if not path.exists() or not path.is_file():
                    raise FileNotFoundError("upload not found")
                data = path.read_bytes()
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                safe_write(self, data)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 404)
            return
        if parsed.path == "/api/bridge/history":
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or [50])[0])
                self.send_json({"ok": True, "history": bridge_history(limit)})
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            return
        if parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(snapshot(), ensure_ascii=False)
                    if not safe_write(self, f"event: snapshot\ndata: {payload}\n\n".encode("utf-8")):
                        return
                    time.sleep(5)
            except OSError as exc:
                if not is_expected_client_disconnect(exc):
                    raise
                return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            data = self.read_body_json()
            if agency.handle_post(self, parsed, qs, data):
                return
            if parsed.path == "/api/bridge/send":
                target = validate_bridge_target(data.get("target"))
                message = str(data.get("message", "")).strip()
                attachments = normalize_attachments_payload(data.get("attachments"))
                if not message and not attachments:
                    raise ValueError("message or attachment is required")
                conversation_id = data.get("conversation_id")
                if conversation_id is not None:
                    conversation_id = str(conversation_id)[:120]
                history = data.get("history")
                self.send_bridge_stream(target, message, conversation_id, history, attachments)
                return
            self.send_error(404)
        except OSError as exc:
            if not is_expected_client_disconnect(exc):
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)


def _referenced_assets(html: str) -> list[str]:
    """Local asset paths the shell references (ignores absolute URLs)."""
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    return sorted({r for r in refs if not r.startswith("//")})


def _javascript_error(path: Path) -> str | None:
    """Syntax-check one JS module with `node --check`.

    Node is optional: if it is not installed we cannot validate and say so
    rather than pretending the file is healthy.
    """
    # `node --check FILE` silently passes files containing `import`: Node
    # switches to module detection and stops reporting the syntax error, which
    # is precisely the "looks fine, does not run" failure this guard exists to
    # catch. Feeding the source on stdin with --input-type=module forces real
    # module parsing and reports errors correctly.
    # Bytes, not text: these modules contain characters (→, ✓) the Windows
    # console codepage cannot encode, and piping them as str raises before
    # node ever sees the source.
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=path.read_bytes(),
            cwd=str(PROJECT_DIR), capture_output=True, timeout=15)
    except FileNotFoundError:
        return None  # node absent — skip validation, not a UI fault
    except Exception as exc:
        return f"could not run node --check: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")
        return detail.strip() or "unknown syntax error"
    return None


def ensure_index_html_healthy() -> None:
    """Preflight the Quantum UI before the dashboard binds its port.

    Called by the desktop launcher as `server.ensure_index_html_healthy()`.
    The name is kept for that contract; what it checks is the current
    architecture — app/q/index.html plus the ES modules it loads — rather
    than the single inline-script index.html this project used to ship.

    Raises RuntimeError on a genuinely broken UI so the launcher stops
    instead of serving a page that loads but cannot run.
    """
    if not INDEX_PATH.exists():
        raise RuntimeError(f"Mission Control UI missing: {INDEX_PATH}")
    html = INDEX_PATH.read_text(encoding="utf-8", errors="replace")
    if not html.strip():
        raise RuntimeError(f"Mission Control UI is empty: {INDEX_PATH}")

    missing = []
    for ref in _referenced_assets(html):
        asset = (PROJECT_DIR / ref.lstrip("/")).resolve()
        try:
            asset.relative_to(PROJECT_DIR)
        except ValueError:
            continue  # outside the project: not ours to validate
        if not asset.is_file():
            missing.append(ref)
    if missing:
        raise RuntimeError(
            "Mission Control UI references files that do not exist: " + ", ".join(missing))

    js_files = sorted((PROJECT_DIR / "app" / "q" / "js").rglob("*.js"))
    broken: list[str] = []
    checked = 0
    for js in js_files:
        error = _javascript_error(js)
        if error is None:
            checked += 1
            continue
        broken.append(f"{js.relative_to(PROJECT_DIR)}: {error.splitlines()[0]}")
    if broken:
        raise RuntimeError("Mission Control UI JavaScript failed validation:\n  " + "\n  ".join(broken))

    if checked:
        print(f"Mission Control UI preflight: OK ({checked} modules validated)")
    else:
        print("Mission Control UI preflight: files present "
              "(node not installed, JavaScript not syntax-checked)")


def _warm_provider_sdks() -> None:
    for mod in ("providers", "anthropic", "openai"):
        try:
            __import__(mod)
        except Exception:
            pass  # an SDK that is not installed is reported by the Models page, not here


def main() -> int:
    ensure_index_html_healthy()
    init_bridge()
    import providers
    providers.remember_billing()          # an out-of-credit provider stays skipped across restarts
    try:
        import autopilot                 # scheduled campaigns and monthly client reports
        autopilot.start()
    except Exception as exc:
        print(f"autopilot did not start: {exc}")
    httpd = RobustThreadingHTTPServer((HOST, PORT), Handler)
    # The provider SDKs take ~2s to import. The first overview needs them (the
    # Models state), so import them now, off the request path, instead of
    # making the first page load after every restart wait for it.
    threading.Thread(target=_warm_provider_sdks, name="warm-sdks", daemon=True).start()
    print(f"Hermes Mission Control backend listening on http://{HOST}:{PORT}")
    print(f"HERMES_HOME={HERMES_HOME}")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

