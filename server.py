#!/usr/bin/env python3
"""Read-only Hermes Mission Control backend.

Python stdlib only. Hermes runtime databases are opened with SQLite read-only
URI mode and PRAGMA query_only=1. The local operator board uses read-write
SQLite in this project directory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import queue
import secrets
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
import multipart

HOST = os.environ.get("MISSION_CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("MISSION_CONTROL_PORT", "51764"))
PROJECT_DIR = Path(__file__).resolve().parent
INDEX_PATH = PROJECT_DIR / "app" / "q" / "index.html"
BOARD_DB = PROJECT_DIR / "board.db"
BRIDGE_DB = PROJECT_DIR / "bridge.db"
WORKFLOW_DB = PROJECT_DIR / "workflows.db"
MISSION_DB = PROJECT_DIR / "missions.db"
MISSION_MEMORY_DB = PROJECT_DIR / "mission_memory.db"
# Windows Hermes Desktop home. Override with HERMES_HOME if needed.
LOCAL_HERMES_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "hermes"
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or LOCAL_HERMES_HOME).expanduser()
HERMES_ENV_PATH = HERMES_HOME / ".env"
# Mission Control is often launched from different shells on Windows. Keep a
# hard fallback to the Hermes Desktop home so a stray HERMES_HOME or working
# directory cannot make bridge auth disappear.
DEFAULT_HERMES_ENV_PATH = LOCAL_HERMES_HOME / ".env"
CONTENT_ROOT = Path(os.environ.get("MISSION_CONTROL_CONTENT_ROOT", str(HERMES_HOME / "content"))).expanduser().resolve()
UPLOAD_ROOT = Path(os.environ.get("MISSION_CONTROL_ATTACHMENTS_ROOT", str(PROJECT_DIR / "uploads"))).expanduser().resolve()
GATEWAY_CHAT_URL = os.environ.get("MISSION_CONTROL_GATEWAY_CHAT_URL", "http://127.0.0.1:8643/v1/chat/completions")
UPLOADS_DIR = PROJECT_DIR / "uploads"
MAX_UPLOAD_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
MAX_INLINE_IMAGE_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_INLINE_IMAGE_BYTES", str(5 * 1024 * 1024)))
MAX_TEXT_PREVIEW_CHARS = int(os.environ.get("MISSION_CONTROL_MAX_TEXT_PREVIEW_CHARS", "12000"))
TEXT_UPLOAD_SUFFIXES = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv", ".log", ".html", ".htm", ".css", ".xml", ".sql", ".sh", ".bash", ".bat", ".ps1"}
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
    "@antigravity": {"agent": "antigravity", "name": "Antigravity", "code": "ANTI"},
    "@chatgpt": {"agent": "chatgpt", "name": "ChatGPT", "code": "GPT"},
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


def ensure_uploads_dir() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in {'-', '_', '.'} else '-' for ch in str(name or 'upload').strip())
    cleaned = cleaned.strip('.-') or 'upload'
    return cleaned[:120]


def attachment_meta_path(attachment_id: str) -> Path:
    return UPLOADS_DIR / f"{attachment_id}.json"


def attachment_file_path(meta: dict[str, Any]) -> Path:
    stored_name = meta.get('stored_name')
    if not stored_name:
        raise FileNotFoundError('stored file missing')
    return UPLOADS_DIR / str(stored_name)


def attachment_public_url(meta: dict[str, Any]) -> str:
    return f"/api/uploads/{meta.get('id')}/{quote(str(meta.get('name') or 'attachment'))}"


def attachment_kind(name: str, mime_type: str) -> str:
    mime = str(mime_type or '').lower()
    suffix = Path(name or '').suffix.lower()
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith('text/') or suffix in TEXT_UPLOAD_SUFFIXES:
        return 'text'
    return 'binary'


def read_text_preview(blob: bytes) -> str:
    return blob.decode('utf-8', errors='replace').replace('\x00', ' ').strip()[:MAX_TEXT_PREVIEW_CHARS]


def save_uploaded_file(field: multipart.Part) -> dict[str, Any]:
    ensure_uploads_dir()
    raw_name = getattr(field, 'filename', None) or 'upload'
    name = sanitize_filename(raw_name)
    mime_type = (getattr(field, 'type', None) or mimetypes.guess_type(name)[0] or 'application/octet-stream').lower()
    blob = field.file.read(MAX_UPLOAD_BYTES + 1)
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ValueError(f'file exceeds {MAX_UPLOAD_BYTES} byte limit')
    attachment_id = str(uuid.uuid4())
    stored_name = f"{attachment_id}__{name}"
    meta = {'id': attachment_id, 'name': name, 'stored_name': stored_name, 'mime': mime_type, 'size': len(blob), 'kind': attachment_kind(name, mime_type), 'created_at': now_iso()}
    preview_text = ''
    if meta['kind'] == 'text':
        preview_text = read_text_preview(blob)
        meta['preview_text'] = preview_text
    file_path = UPLOADS_DIR / stored_name
    file_path.write_bytes(blob)
    attachment_meta_path(attachment_id).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return {'id': attachment_id, 'name': name, 'mime': mime_type, 'size': len(blob), 'kind': meta['kind'], 'preview_text': preview_text, 'url': attachment_public_url(meta)}


def load_attachment_meta(attachment_id: str) -> dict[str, Any]:
    if not re.fullmatch(r'[0-9a-fA-F-]{8,64}', str(attachment_id or '')):
        raise ValueError('invalid attachment id')
    path = attachment_meta_path(str(attachment_id))
    if not path.exists():
        raise FileNotFoundError('attachment not found')
    meta = json.loads(path.read_text(encoding='utf-8'))
    attachment_file_path(meta)
    return meta


def resolve_attachment_infos(attachment_ids: Any) -> list[dict[str, Any]]:
    out = []
    for raw in list(attachment_ids or [])[:8]:
        try:
            meta = load_attachment_meta(str(raw))
        except Exception:
            continue
        out.append({'id': meta.get('id'), 'name': meta.get('name'), 'mime': meta.get('mime'), 'size': meta.get('size'), 'kind': meta.get('kind'), 'preview_text': meta.get('preview_text', ''), 'url': attachment_public_url(meta)})
    return out


def attachment_data_url(meta: dict[str, Any]) -> str | None:
    blob_path = attachment_file_path(meta)
    size = int(meta.get('size') or 0)
    if size > MAX_INLINE_IMAGE_BYTES:
        return None
    encoded = base64.b64encode(blob_path.read_bytes()).decode('ascii')
    return f"data:{meta.get('mime') or 'application/octet-stream'};base64,{encoded}"


def user_message_content(message: str, attachment_ids: Any = None) -> Any:
    text = str(message or '').strip()
    attachments = resolve_attachment_infos(attachment_ids)
    if not attachments:
        return text
    lines = []
    if text:
        lines.append(text)
    else:
        lines.append('Please analyze the uploaded attachment(s) in this chat session and answer like ChatGPT with concrete help.')
    lines.append('Uploaded attachment context:')
    parts: list[dict[str, Any]] = []
    for meta in attachments:
        label = f"- {meta.get('name')} ({meta.get('kind')}, {meta.get('mime')}, {meta.get('size')} bytes)"
        preview = str(meta.get('preview_text') or '').strip()
        if preview:
            label += f"\n{preview[:MAX_TEXT_PREVIEW_CHARS]}"
        elif meta.get('kind') != 'image':
            label += '\nBinary file uploaded. Work from metadata unless the user provides a text-export or asks for a specific conversion.'
        lines.append(label)
    parts.append({'type': 'text', 'text': '\n\n'.join(lines)})
    for meta in attachments:
        if meta.get('kind') != 'image':
            continue
        try:
            full_meta = load_attachment_meta(str(meta.get('id')))
            data_url = attachment_data_url(full_meta)
        except Exception:
            data_url = None
        if data_url:
            parts.append({'type': 'image_url', 'image_url': {'url': data_url}})
        else:
            parts[0]['text'] += f"\n\nImage note: {meta.get('name')} is too large to inline for vision, so reason from the metadata unless the user re-uploads a smaller image."
    return parts


def normalize_history_message(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    role = str(item.get('role') or '').strip().lower()
    if role not in {'user', 'assistant', 'system'}:
        return None
    content = item.get('content', '')
    if role == 'user':
        return {'role': 'user', 'content': user_message_content(str(content or ''), item.get('attachment_ids'))}
    text = str(content or '').strip()
    if not text:
        return None
    return {'role': role, 'content': text}


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


_SAFE_UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TEXT_UPLOAD_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".htm", ".css", ".scss", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".toml",
    ".log", ".sql", ".sh", ".bat", ".ps1", ".env", ".gitignore",
}



def bridge_user_message_content(message: str, attachments: list[dict[str, Any]] | None = None) -> str | list[dict[str, Any]]:
    text = str(message or "").strip()
    attachments = attachments or []
    if not attachments:
        return text
    sections: list[str] = [text] if text else []
    notes: list[str] = []
    for item in attachments:
        note = [
            f"[Attached file: {item.get('filename')}]",
            f"MIME: {item.get('mime_type')}",
            f"Size: {item.get('size')} bytes",
            f"Local path: {item.get('path')}",
            f"Local URL: {item.get('url')}",
        ]
        preview = str(item.get("preview") or "").strip()
        if preview:
            note.append("Extracted text preview:\n" + preview)
        elif item.get("is_image"):
            note.append("This is an image attachment. Use your vision/image tools if you need pixel-level analysis.")
        else:
            note.append("This appears to be a binary or unsupported-text file. Use your available file tools on the local path if needed.")
        notes.append("\n".join(note))
    sections.append("The user attached the following files for this turn:\n\n" + "\n\n".join(notes))
    text_block = {"type": "text", "text": "\n\n".join(s for s in sections if s).strip()}
    parts: list[dict[str, Any]] = [text_block]
    for item in attachments:
        data_url = item.get("data_url")
        if item.get("is_image") and isinstance(data_url, str) and data_url.startswith("data:image/"):
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else text_block["text"]


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


MONTHS = {'*': 'every month'}
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


# ---------- Personal operator board ----------

def board_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(BOARD_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_board() -> None:
    with board_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """)
        count = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        if count == 0:
            seed = [
                ("Review overnight Hermes activity logs", "pending", "high", "Check failed responses and unusual agent silence."),
                ("Validate Discord agent channel routing", "pending", "high", "Send one test prompt per agent channel."),
                ("Draft weekly AI operations summary", "pending", "medium", "Summarize uptime, activity, and open work."),
                ("Clean up stale local setup artifacts", "in_progress", "medium", "Archive bootstrap files before deleting anything."),
                ("Tune Mission Control dashboard layout", "in_progress", "medium", "Prioritize gateway, activity, and sessions above the fold."),
                ("Confirm monthly log retention cron", "completed", "high", "Verify first-of-month 03:00 schedule and manual test output."),
                ("Document AgentOS role boundaries", "completed", "medium", "Keep Scout/Scribe/Reach/Dev responsibilities clear."),
                ("Create shared activity logging policy", "completed", "high", "Ensure every agent logs before response."),
            ]
            ts = now_iso()
            for title, status, priority, notes in seed:
                conn.execute(
                    "INSERT INTO tasks (id,title,status,priority,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), title, status, priority, notes, ts, ts),
                )


def board_list() -> list[dict[str, Any]]:
    init_board()
    with board_conn() as conn:
        return rows_to_dicts(conn.execute(
            "SELECT id,title,status,priority,notes,created_at,updated_at FROM tasks ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC"
        ).fetchall())


def board_create(data: dict[str, Any]) -> dict[str, Any]:
    init_board()
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("title is required")
    status = str(data.get("status", "pending"))
    priority = str(data.get("priority", "medium"))
    notes = str(data.get("notes", ""))
    ts = now_iso()
    task = {"id": str(uuid.uuid4()), "title": title, "status": status, "priority": priority, "notes": notes, "created_at": ts, "updated_at": ts}
    with board_conn() as conn:
        conn.execute("INSERT INTO tasks (id,title,status,priority,notes,created_at,updated_at) VALUES (:id,:title,:status,:priority,:notes,:created_at,:updated_at)", task)
    return task


def board_update(task_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_board()
    allowed = {"title", "status", "priority", "notes"}
    fields = {k: str(v) for k, v in data.items() if k in allowed}
    if not task_id:
        raise ValueError("id is required")
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join([f"{k}=?" for k in fields])
    vals = list(fields.values()) + [task_id]
    with board_conn() as conn:
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", vals)
        if cur.rowcount == 0:
            raise KeyError("task not found")
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row)


def board_delete(task_id: str) -> dict[str, Any]:
    init_board()
    if not task_id:
        raise ValueError("id is required")
    with board_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return {"deleted": cur.rowcount, "id": task_id}



# ---------- Mission state cockpit ----------
def mission_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(MISSION_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _mission_json(value: Any, limit: int = 12) -> str:
    if isinstance(value, str):
        items = [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    elif isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        items = []
    return json.dumps(items[:limit], ensure_ascii=False)


def _mission_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("open_questions", "decisions", "artifacts", "blockers"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except Exception:
            out[key] = []
    try:
        out["confidence"] = int(out.get("confidence") or 0)
    except Exception:
        out["confidence"] = 0
    return out


def init_missions() -> None:
    with mission_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            phase TEXT DEFAULT 'Discovery',
            active_agent TEXT DEFAULT '@orchestrator',
            status TEXT DEFAULT 'active',
            open_questions TEXT DEFAULT '[]',
            decisions TEXT DEFAULT '[]',
            artifacts TEXT DEFAULT '[]',
            blockers TEXT DEFAULT '[]',
            next_action TEXT DEFAULT '',
            confidence INTEGER DEFAULT 70,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mission_events (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            agent TEXT DEFAULT '@orchestrator',
            kind TEXT DEFAULT 'update',
            text TEXT NOT NULL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_missions_updated ON missions(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mission_events_ts ON mission_events(ts DESC)")
        count = conn.execute("SELECT COUNT(*) AS n FROM missions").fetchone()["n"]
        if count == 0:
            ts = now_iso()
            mission_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO missions (id,objective,phase,active_agent,status,open_questions,decisions,artifacts,blockers,next_action,confidence,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    "Build AgentOS Mission Control into a visible operating cockpit",
                    "Build",
                    "@forge",
                    "active",
                    _mission_json(["Which mission fields should agents update automatically first?"]),
                    _mission_json(["Ship the mission/state layer before adding heavier review gates."]),
                    _mission_json(["Mission Control dashboard", "Local SQLite mission ledger"]),
                    _mission_json([]),
                    "Expose mission state in the dashboard and verify the API/UI path.",
                    78,
                    ts,
                    ts,
                ),
            )
            conn.execute("INSERT INTO mission_events (id,mission_id,ts,agent,kind,text) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), mission_id, ts, "@forge", "created", "Mission cockpit seeded from Forge world-class AgentOS recommendation."))


def mission_list(limit: int = 25) -> list[dict[str, Any]]:
    init_missions()
    limit = max(1, min(100, int(limit or 25)))
    with mission_conn() as conn:
        rows = conn.execute("SELECT * FROM missions ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'blocked' THEN 1 WHEN 'review' THEN 2 ELSE 3 END, updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_mission_from_row(r) for r in rows]


def mission_events(mission_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    init_missions()
    limit = max(1, min(200, int(limit or 40)))
    with mission_conn() as conn:
        if mission_id:
            return rows_to_dicts(conn.execute("SELECT * FROM mission_events WHERE mission_id=? ORDER BY ts DESC LIMIT ?", (mission_id, limit)).fetchall())
        return rows_to_dicts(conn.execute("SELECT * FROM mission_events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall())


def mission_payload(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    objective = str(data.get("objective") if data.get("objective") is not None else existing.get("objective", "")).strip()
    if not objective:
        raise ValueError("objective is required")
    active_agent = validate_bridge_target(data.get("active_agent") if data.get("active_agent") is not None else existing.get("active_agent", "@orchestrator"))
    status = str(data.get("status") if data.get("status") is not None else existing.get("status", "active")).strip().lower()[:40] or "active"
    try:
        confidence = max(0, min(100, int(data.get("confidence") if data.get("confidence") is not None else existing.get("confidence", 70))))
    except Exception:
        confidence = 70
    return {
        "objective": objective[:220],
        "phase": str(data.get("phase") if data.get("phase") is not None else existing.get("phase", "Discovery")).strip()[:80] or "Discovery",
        "active_agent": active_agent,
        "status": status,
        "open_questions": _mission_json(data.get("open_questions") if data.get("open_questions") is not None else existing.get("open_questions", [])),
        "decisions": _mission_json(data.get("decisions") if data.get("decisions") is not None else existing.get("decisions", [])),
        "artifacts": _mission_json(data.get("artifacts") if data.get("artifacts") is not None else existing.get("artifacts", [])),
        "blockers": _mission_json(data.get("blockers") if data.get("blockers") is not None else existing.get("blockers", [])),
        "next_action": str(data.get("next_action") if data.get("next_action") is not None else existing.get("next_action", "")).strip()[:500],
        "confidence": confidence,
    }


def mission_create(data: dict[str, Any]) -> dict[str, Any]:
    init_missions()
    item = mission_payload(data)
    ts = now_iso()
    row = {"id": str(uuid.uuid4()), **item, "created_at": ts, "updated_at": ts}
    with mission_conn() as conn:
        conn.execute("""
        INSERT INTO missions (id,objective,phase,active_agent,status,open_questions,decisions,artifacts,blockers,next_action,confidence,created_at,updated_at)
        VALUES (:id,:objective,:phase,:active_agent,:status,:open_questions,:decisions,:artifacts,:blockers,:next_action,:confidence,:created_at,:updated_at)
        """, row)
        conn.execute("INSERT INTO mission_events (id,mission_id,ts,agent,kind,text) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), row["id"], ts, row["active_agent"], "created", row["objective"]))
    return _mission_from_row(row)


def mission_update(mission_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_missions()
    if not mission_id:
        raise ValueError("id is required")
    with mission_conn() as conn:
        existing = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not existing:
            raise KeyError("mission not found")
        existing_dict = _mission_from_row(existing)
        item = mission_payload(data, existing_dict)
        row = {"id": mission_id, **item, "updated_at": now_iso()}
        conn.execute("""
        UPDATE missions SET objective=:objective,phase=:phase,active_agent=:active_agent,status=:status,open_questions=:open_questions,decisions=:decisions,artifacts=:artifacts,blockers=:blockers,next_action=:next_action,confidence=:confidence,updated_at=:updated_at WHERE id=:id
        """, row)
        conn.execute("INSERT INTO mission_events (id,mission_id,ts,agent,kind,text) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), mission_id, row["updated_at"], row["active_agent"], "updated", f"{row['phase']} · {row['next_action'] or row['status']}"))
        saved = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    return _mission_from_row(saved)


def mission_fork(mission_id: str | None = None, instruction: str = "") -> dict[str, Any]:
    init_missions()
    source = None
    with mission_conn() as conn:
        if mission_id:
            source = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if source is None:
            source = conn.execute("SELECT * FROM missions ORDER BY updated_at DESC LIMIT 1").fetchone()
        if source is None:
            raise KeyError("mission not found")
        src = _mission_from_row(source)
        ts = now_iso()
        new_id = str(uuid.uuid4())
        objective = (instruction.strip() or f"Fork: {src.get('objective', 'Mission')}")[:220]
        row = {
            "id": new_id,
            "objective": objective,
            "phase": "Forked",
            "active_agent": src.get("active_agent") or "@orchestrator",
            "status": "active",
            "open_questions": _mission_json(src.get("open_questions") or []),
            "decisions": _mission_json((src.get("decisions") or []) + [f"Forked from mission {src.get('id')}"]),
            "artifacts": _mission_json(src.get("artifacts") or []),
            "blockers": _mission_json([]),
            "next_action": "Replay or revise the mission from this checkpoint.",
            "confidence": max(30, min(100, int(src.get("confidence") or 70))),
            "created_at": ts,
            "updated_at": ts,
        }
        conn.execute("""
        INSERT INTO missions (id,objective,phase,active_agent,status,open_questions,decisions,artifacts,blockers,next_action,confidence,created_at,updated_at)
        VALUES (:id,:objective,:phase,:active_agent,:status,:open_questions,:decisions,:artifacts,:blockers,:next_action,:confidence,:created_at,:updated_at)
        """, row)
        conn.execute("INSERT INTO mission_events (id,mission_id,ts,agent,kind,text) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), new_id, ts, row["active_agent"], "forked", f"Forked from {src.get('id')}"))
    return _mission_from_row(row)


def mission_add_event(data: dict[str, Any]) -> dict[str, Any]:
    init_missions()
    mission_id = str(data.get("mission_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not mission_id:
        active = mission_list(1)
        mission_id = active[0]["id"] if active else ""
    if not mission_id or not text:
        raise ValueError("mission_id and text are required")
    agent = validate_bridge_target(data.get("agent") or data.get("active_agent") or "@orchestrator")
    kind = str(data.get("kind") or "update").strip()[:40] or "update"
    ts = now_iso()
    row = {"id": str(uuid.uuid4()), "mission_id": mission_id, "ts": ts, "agent": agent, "kind": kind, "text": text[:1000]}
    with mission_conn() as conn:
        conn.execute("INSERT INTO mission_events (id,mission_id,ts,agent,kind,text) VALUES (:id,:mission_id,:ts,:agent,:kind,:text)", row)
        conn.execute("UPDATE missions SET updated_at=? WHERE id=?", (ts, mission_id))
    return row


def mission_summary() -> dict[str, Any]:
    missions = mission_list(10)
    active = missions[0] if missions else None
    events = mission_events(active.get("id") if active else None, 12) if active else []
    if not active:
        return {"active": None, "missions": [], "events": [], "objective": "No active mission yet.", "phase": "Ready", "owner": "Orchestrator", "target": "@orchestrator", "updated_at": now_iso(), "decisions": [], "artifacts": [], "phases": []}
    owner = BRIDGE_TARGETS.get(active.get("active_agent"), {}).get("name", str(active.get("active_agent", "@orchestrator")).lstrip("@").title())
    decisions = [{"label": active.get("active_agent"), "summary": x, "ts": active.get("updated_at")} for x in (active.get("decisions") or [])]
    artifacts = [{"title": x, "agent": owner, "type": "mission", "modified_at": active.get("updated_at")} for x in (active.get("artifacts") or [])]
    blockers = active.get("blockers") or []
    phases = [
        {"id": "goal", "label": "Goal", "owner": owner, "status": "active", "summary": active.get("objective", ""), "evidence": active.get("created_at")},
        {"id": "plan", "label": "Plan", "owner": "Orchestrator", "status": "ready" if active.get("open_questions") else "waiting", "summary": "; ".join(active.get("open_questions") or ["No open questions captured."]), "evidence": active.get("updated_at")},
        {"id": "build", "label": "Build", "owner": owner, "status": active.get("status") or "active", "summary": active.get("next_action") or "No next action captured.", "evidence": f"Confidence {active.get('confidence', 0)}%"},
        {"id": "verify", "label": "Verify", "owner": "Forge / Rank", "status": "blocked" if blockers else "ready", "summary": "; ".join(blockers or ["No blockers recorded."]), "evidence": f"{len(events)} ledger events"},
        {"id": "ship", "label": "Ship", "owner": "Scribe / Orchestrator", "status": "ready" if artifacts else "waiting", "summary": f"{len(artifacts)} artifact(s) recorded.", "evidence": active.get("updated_at")},
    ]
    normalized_missions = []
    for m in missions:
        normalized = dict(m)
        normalized.setdefault("title", m.get("objective", "Mission")[:80])
        normalized.setdefault("owner", BRIDGE_TARGETS.get(m.get("active_agent"), {}).get("name", str(m.get("active_agent", "@orchestrator")).lstrip("@").title()))
        normalized.setdefault("evidence", m.get("artifacts") or [])
        normalized_missions.append(normalized)
    normalized_events = [{**e, "summary": e.get("text", ""), "status": e.get("kind", "logged")} for e in events]
    return {"active": active, "missions": normalized_missions, "events": normalized_events, "objective": active.get("objective"), "phase": active.get("phase"), "owner": owner, "target": active.get("active_agent"), "updated_at": active.get("updated_at"), "decisions": decisions, "artifacts": artifacts, "phases": phases}


# ---------- Shared Mission Memory ----------
MISSION_MEMORY_LIST_FIELDS = ("constraints", "open_questions", "decisions", "artifacts", "blocked_items")


def mission_memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(MISSION_MEMORY_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _memory_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.replace("\r\n", "\n").replace("\r", "\n").replace(",", "\n").split("\n")
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    items: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            continue
        item = raw.strip()
        if item:
            items.append(item[:500])
    return items[:40]

def _mission_memory_row(row: sqlite3.Row, timeline: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = dict(row)
    for field in MISSION_MEMORY_LIST_FIELDS:
        try:
            item[field] = json.loads(item.get(field) or "[]")
        except Exception:
            item[field] = []
    item["timeline"] = timeline or []
    return item


def init_mission_memory() -> None:
    conn = mission_memory_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            goal TEXT NOT NULL,
            constraints TEXT DEFAULT '[]',
            current_hypothesis TEXT DEFAULT '',
            open_questions TEXT DEFAULT '[]',
            decisions TEXT DEFAULT '[]',
            artifacts TEXT DEFAULT '[]',
            blocked_items TEXT DEFAULT '[]',
            next_best_action TEXT DEFAULT '',
            owner_agent TEXT DEFAULT 'orchestrator'
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mission_events (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            agent TEXT NOT NULL,
            text TEXT NOT NULL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mission_memory_events_ts ON mission_events(ts DESC)")
        count = conn.execute("SELECT COUNT(*) AS n FROM missions").fetchone()["n"]
        if count == 0:
            ts = now_iso()
            mission_id = str(uuid.uuid4())
            seed = {
                "id": mission_id,
                "created_at": ts,
                "updated_at": ts,
                "status": "active",
                "goal": "Build Mission Memory: a shared operational brain for AgentOS work.",
                "constraints": json.dumps(["Local-first", "Python stdlib only", "Preserve existing Mission Control APIs"], ensure_ascii=False),
                "current_hypothesis": "Structured mission state improves multi-agent handoffs and resumption.",
                "open_questions": json.dumps(["Which agent owns the next best action?"], ensure_ascii=False),
                "decisions": json.dumps(["Use one active SQLite mission row plus an event timeline."], ensure_ascii=False),
                "artifacts": json.dumps(["server.py", "index.html", "mission_memory.db"], ensure_ascii=False),
                "blocked_items": json.dumps([], ensure_ascii=False),
                "next_best_action": "Review and update this mission state after each significant crew decision.",
                "owner_agent": "orchestrator",
            }
            conn.execute("""
            INSERT INTO missions (id,created_at,updated_at,status,goal,constraints,current_hypothesis,open_questions,decisions,artifacts,blocked_items,next_best_action,owner_agent)
            VALUES (:id,:created_at,:updated_at,:status,:goal,:constraints,:current_hypothesis,:open_questions,:decisions,:artifacts,:blocked_items,:next_best_action,:owner_agent)
            """, seed)
            conn.execute("INSERT INTO mission_events (id,mission_id,ts,kind,agent,text) VALUES (?,?,?,?,?,?)", (
                str(uuid.uuid4()), mission_id, ts, "decision", "orchestrator", "Mission Memory initialized as the shared AgentOS state layer."
            ))
        conn.commit()
    finally:
        conn.close()


def mission_memory_current() -> dict[str, Any]:
    init_mission_memory()
    conn = mission_memory_conn()
    try:
        row = conn.execute("SELECT * FROM missions WHERE status='active' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("no active mission memory row")
        timeline = rows_to_dicts(conn.execute("SELECT id,ts,kind,agent,text FROM mission_events WHERE mission_id=? ORDER BY ts DESC LIMIT 40", (row["id"],)).fetchall())
        return _mission_memory_row(row, timeline)
    finally:
        conn.close()


def mission_memory_update(data: dict[str, Any]) -> dict[str, Any]:
    current = mission_memory_current()
    updates: dict[str, Any] = {}
    for field in ("goal", "current_hypothesis", "next_best_action"):
        if field in data:
            updates[field] = str(data.get(field) or "").strip()[:2000]
    if "owner_agent" in data:
        owner = str(data.get("owner_agent") or "orchestrator").strip().lower().lstrip("@")
        if owner == "all" or f"@{owner}" not in BRIDGE_TARGETS:
            owner = "orchestrator"
        updates["owner_agent"] = owner
    for field in MISSION_MEMORY_LIST_FIELDS:
        if field in data:
            updates[field] = json.dumps(_memory_list(data.get(field)), ensure_ascii=False)
    if not updates:
        return current
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [current["id"]]
    conn = mission_memory_conn()
    try:
        conn.execute(f"UPDATE missions SET {sets} WHERE id=?", vals)
        summary = "; ".join(f"{k}: {v}" for k, v in updates.items() if k in {"goal", "next_best_action", "owner_agent"}) or "structured mission state updated"
        conn.execute("INSERT INTO mission_events (id,mission_id,ts,kind,agent,text) VALUES (?,?,?,?,?,?)", (
            str(uuid.uuid4()), current["id"], updates["updated_at"], str(data.get("event_kind") or "update")[:40], str(data.get("agent") or updates.get("owner_agent") or current.get("owner_agent") or "orchestrator")[:80], summary[:2000]
        ))
        conn.commit()
    finally:
        conn.close()
    return mission_memory_current()


# ---------- Workflow Launchpad durable state ----------
def workflow_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(WORKFLOW_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_workflows() -> None:
    with workflow_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            title TEXT NOT NULL,
            owner TEXT NOT NULL,
            accent TEXT DEFAULT '#A78BFA',
            description TEXT DEFAULT '',
            steps TEXT DEFAULT '[]',
            prompt TEXT NOT NULL,
            is_builtin INTEGER DEFAULT 0
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            workflow_title TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            prompt TEXT NOT NULL,
            artifact TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_updated ON workflows(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_runs_created ON workflow_runs(created_at DESC)")
        # Keep built-in templates self-healing: new releases can add templates
        # without requiring operators to delete an existing workflows.db.
        ts = now_iso()
        builtin_templates = [
            {
                "id": "builtin-review-repo",
                "title": "Review this repo",
                "owner": "@forge",
                "accent": "#C98635",
                "description": "Inspect architecture, risky files, tests, and ship a prioritized review with evidence.",
                "steps": ["Map repo", "Read hot paths", "Run checks", "Report risks", "Suggest fixes"],
                "prompt": "Review this repository end-to-end. Map the architecture, identify risky or low-quality areas, run available verification checks, and return prioritized findings with file paths and evidence.",
            },
            {
                "id": "builtin-fix-tests",
                "title": "Fix failing tests",
                "owner": "@dev",
                "accent": "#D6A85A",
                "description": "Reproduce failures, find root cause, patch minimally, and verify green tests.",
                "steps": ["Reproduce", "Trace cause", "Patch", "Run focused test", "Run full suite"],
                "prompt": "Fix the failing tests systematically: reproduce the exact failure, identify the root cause before changing code, implement the smallest safe fix, then run focused and full verification.",
            },
            {
                "id": "builtin-research-competitor",
                "title": "Research competitor",
                "owner": "@scout",
                "accent": "#7DB79C",
                "description": "Gather sources, compare positioning, pricing, features, gaps, and actionable moves.",
                "steps": ["Find sources", "Extract facts", "Compare", "Spot gaps", "Recommend moves"],
                "prompt": "Research this competitor using current sources. Summarize positioning, pricing, feature set, proof points, weaknesses, and the top opportunities we can exploit. Include source URLs.",
            },
            {
                "id": "builtin-draft-pr",
                "title": "Draft PR",
                "owner": "@forge",
                "accent": "#B7E4C7",
                "description": "Turn local changes into a clean PR summary, test evidence, risks, and reviewer notes.",
                "steps": ["Read diff", "Group changes", "List tests", "Call out risks", "Draft PR"],
                "prompt": "Draft a high-quality pull request description for the current changes. Include summary, implementation notes, tests run, screenshots or artifacts if relevant, risks, and reviewer checklist.",
            },
            {
                "id": "builtin-debug-service",
                "title": "Debug local service",
                "owner": "@dev",
                "accent": "#D07868",
                "description": "Check listener, logs, health endpoints, auth, and exact failing boundary.",
                "steps": ["Check process", "Probe endpoints", "Read logs", "Trace auth", "Patch or report blocker"],
                "prompt": "Debug this local service live. Verify the listener, health endpoints, logs, environment/auth, and exact failing boundary. Fix only after root cause is clear, then prove it with a real request.",
            },
            {
                "id": "builtin-summarize-meeting",
                "title": "Summarize meeting",
                "owner": "@scribe",
                "accent": "#B08AC6",
                "description": "Turn notes or transcript into decisions, owners, action items, risks, and follow-up drafts.",
                "steps": ["Extract agenda", "Capture decisions", "Assign actions", "Flag risks", "Draft follow-up"],
                "prompt": "Summarize this meeting or transcript. Extract attendees if available, decisions, action items with owners and dates, unresolved questions, risks, and a concise follow-up message.",
            },
            {
                "id": "builtin-build-feature",
                "title": "Build feature from issue",
                "owner": "@forge",
                "accent": "#A989D6",
                "description": "Convert an issue into implementation, tests, verification, and ship notes.",
                "steps": ["Clarify acceptance", "Find code paths", "Implement", "Test", "Summarize"],
                "prompt": "Build the feature described in this issue. Determine acceptance criteria, inspect relevant code paths, implement the change, add or update tests where practical, run verification, and summarize files changed.",
            },
        ]
        for item in builtin_templates:
            row = {**item, "ts": ts, "updated_at": ts, "steps": json.dumps(item["steps"], ensure_ascii=False), "is_builtin": 1}
            conn.execute("INSERT OR IGNORE INTO workflows (id,ts,updated_at,title,owner,accent,description,steps,prompt,is_builtin) VALUES (:id,:ts,:updated_at,:title,:owner,:accent,:description,:steps,:prompt,:is_builtin)", row)


def workflow_list() -> list[dict[str, Any]]:
    init_workflows()
    init_missions()
    with workflow_conn() as conn:
        rows = rows_to_dicts(conn.execute("SELECT * FROM workflows ORDER BY updated_at DESC").fetchall())
    for row in rows:
        try:
            row["steps"] = json.loads(row.get("steps") or "[]")
        except Exception:
            row["steps"] = []
        row["is_builtin"] = bool(row.get("is_builtin"))
    return rows


def workflow_payload(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    title = str(data.get("title") if data.get("title") is not None else (existing or {}).get("title", "")).strip()
    prompt = str(data.get("prompt") if data.get("prompt") is not None else (existing or {}).get("prompt", "")).strip()
    if not title:
        raise ValueError("title is required")
    if not prompt:
        raise ValueError("prompt is required")
    owner = validate_bridge_target(data.get("owner") if data.get("owner") is not None else (existing or {}).get("owner", "@orchestrator"))
    accent = str(data.get("accent") if data.get("accent") is not None else (existing or {}).get("accent", "#A78BFA")).strip() or "#A78BFA"
    desc = str(data.get("description") if data.get("description") is not None else (existing or {}).get("description", ""))[:800]
    raw_steps = data.get("steps") if data.get("steps") is not None else (existing or {}).get("steps", [])
    if isinstance(raw_steps, str):
        steps = [x.strip() for x in raw_steps.split(",") if x.strip()]
    elif isinstance(raw_steps, list):
        steps = [str(x).strip() for x in raw_steps if str(x).strip()]
    else:
        steps = []
    return {"title": title, "owner": owner, "accent": accent, "description": desc, "steps": steps[:12], "prompt": prompt}


def workflow_create(data: dict[str, Any]) -> dict[str, Any]:
    init_workflows()
    item = workflow_payload(data)
    ts = now_iso()
    row = {"id": str(uuid.uuid4()), "ts": ts, "updated_at": ts, **item, "steps": json.dumps(item["steps"], ensure_ascii=False), "is_builtin": 0}
    with workflow_conn() as conn:
        conn.execute("INSERT INTO workflows (id,ts,updated_at,title,owner,accent,description,steps,prompt,is_builtin) VALUES (:id,:ts,:updated_at,:title,:owner,:accent,:description,:steps,:prompt,:is_builtin)", row)
    row["steps"] = item["steps"]
    row["is_builtin"] = False
    return row


def workflow_update(workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_workflows()
    if not workflow_id:
        raise ValueError("id is required")
    with workflow_conn() as conn:
        existing = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not existing:
            raise KeyError("workflow not found")
        existing_dict = dict(existing)
        try:
            existing_dict["steps"] = json.loads(existing_dict.get("steps") or "[]")
        except Exception:
            existing_dict["steps"] = []
        item = workflow_payload(data, existing_dict)
        updated = {"id": workflow_id, "updated_at": now_iso(), **item, "steps": json.dumps(item["steps"], ensure_ascii=False)}
        conn.execute("UPDATE workflows SET updated_at=:updated_at,title=:title,owner=:owner,accent=:accent,description=:description,steps=:steps,prompt=:prompt WHERE id=:id", updated)
    updated["steps"] = item["steps"]
    updated["is_builtin"] = bool(existing_dict.get("is_builtin"))
    updated["ts"] = existing_dict.get("ts")
    return updated


def workflow_delete(workflow_id: str) -> dict[str, Any]:
    init_workflows()
    if not workflow_id:
        raise ValueError("id is required")
    with workflow_conn() as conn:
        cur = conn.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
        return {"deleted": cur.rowcount, "id": workflow_id}


def workflow_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_workflows()
    limit = max(1, min(200, int(limit or 50)))
    with workflow_conn() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall())


def workflow_run_create(data: dict[str, Any]) -> dict[str, Any]:
    init_workflows()
    title = str(data.get("workflow_title") or data.get("title") or "Workflow run").strip()[:160]
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    target = validate_bridge_target(data.get("target") or data.get("owner") or "@orchestrator")
    status = str(data.get("status") or "loaded").strip()[:40] or "loaded"
    ts = now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "workflow_id": str(data.get("workflow_id") or data.get("id") or "custom")[:120],
        "workflow_title": title,
        "target": target,
        "status": status,
        "prompt": prompt,
        "artifact": str(data.get("artifact") or "")[:2000],
        "created_at": ts,
        "updated_at": ts,
    }
    with workflow_conn() as conn:
        conn.execute("INSERT INTO workflow_runs (id,workflow_id,workflow_title,target,status,prompt,artifact,created_at,updated_at) VALUES (:id,:workflow_id,:workflow_title,:target,:status,:prompt,:artifact,:created_at,:updated_at)", row)
    return row


def workflow_run_update(run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_workflows()
    if not run_id:
        raise ValueError("id is required")
    allowed = {"status", "artifact"}
    fields = {k: str(v) for k, v in data.items() if k in allowed}
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join([f"{k}=?" for k in fields])
    vals = list(fields.values()) + [run_id]
    with workflow_conn() as conn:
        cur = conn.execute(f"UPDATE workflow_runs SET {sets} WHERE id=?", vals)
        if cur.rowcount == 0:
            raise KeyError("workflow run not found")
        return dict(conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone())


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

    board_section = safe_call("board", lambda: {"tasks": board_list()})
    for row in (_payload(board_section, {}) or {}).get("tasks", []) or []:
        events.append({
            "id": f"task-{row.get('id')}",
            "ts": row.get("updated_at") or row.get("created_at"),
            "kind": "task",
            "agent": "operator",
            "status": str(row.get("status") or "pending"),
            "title": _short_text(row.get("title") or "Board task", 120),
            "detail": _short_text(row.get("notes") or "", 220),
            "artifact": f"priority: {row.get('priority') or 'medium'}",
            "source": "board.db",
        })

    workflow_section = safe_call("workflows", workflow_list)
    for row in _payload(workflow_section, []) or []:
        events.append({
            "id": f"workflow-{row.get('id')}",
            "ts": row.get("updated_at") or row.get("ts"),
            "kind": "workflow",
            "agent": str(row.get("owner") or "workflow").lstrip("@"),
            "status": "ready",
            "title": _short_text(row.get("title") or "Workflow", 120),
            "detail": _short_text(row.get("description") or row.get("prompt") or "", 240),
            "artifact": ", ".join(row.get("steps") or [])[:220],
            "source": "workflows.db",
        })

    for row in workflow_runs(limit):
        events.append({
            "id": f"workflow-run-{row.get('id')}",
            "ts": row.get("updated_at") or row.get("created_at"),
            "kind": "workflow_run",
            "agent": str(row.get("target") or "workflow").lstrip("@"),
            "status": str(row.get("status") or "loaded"),
            "title": _short_text(row.get("workflow_title") or "Workflow run", 120),
            "detail": _short_text(row.get("prompt") or "", 220),
            "artifact": _short_text(row.get("artifact") or "", 220),
            "source": "workflows.db",
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


def bridge_favorite(data: dict[str, Any]) -> dict[str, Any]:
    init_bridge()
    target = validate_bridge_target(data.get("target", "@orchestrator"))
    message = str(data.get("message", "")).strip()
    if not message:
        raise ValueError("message is required")
    name = str(data.get("name") or message[:48] or "Saved command").strip()
    item = {"id": str(uuid.uuid4()), "ts": now_iso(), "name": name, "target": target, "message": message}
    with bridge_conn() as conn:
        conn.execute("INSERT INTO bridge_favorites (id,ts,name,target,message) VALUES (:id,:ts,:name,:target,:message)", item)
    return item


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


def save_uploaded_files(files_or_form: Any) -> list[dict[str, Any]]:
    ensure_upload_root()
    attachments: list[dict[str, Any]] = []
    if isinstance(files_or_form, list):
        for item in files_or_form[:8]:
            meta = normalize_attachment_payload(item)
            if meta:
                attachments.append(meta)
        if not attachments:
            raise ValueError("no files were uploaded")
        return attachments
    form = files_or_form
    raw_items = form["files"] if "files" in form else []
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items is not None else []
    for item in raw_items[:8]:
        filename = sanitize_upload_name(getattr(item, "filename", "") or "upload")
        fileobj = getattr(item, "file", None)
        if not filename or fileobj is None:
            continue
        data = fileobj.read(MAX_UPLOAD_BYTES + 1)
        mime_type = str(getattr(item, "type", "") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        attachments.append(build_attachment_meta(filename, mime_type, data))
    if not attachments:
        raise ValueError("no files were uploaded")
    return attachments


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
        "@antigravity": "strategy and edge-case checks",
        "@chatgpt": "general reasoning",
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

# ---------- Content library ----------

def validate_content_path(raw_path: str | None) -> Path:
    if not raw_path:
        raise ValueError("path is required")
    candidate = Path(str(raw_path)).expanduser()
    if not candidate.is_absolute():
        candidate = CONTENT_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(CONTENT_ROOT)
    except ValueError as exc:
        raise ValueError("path must stay under /root/.hermes/content/") from exc
    if resolved.suffix.lower() not in {".md", ".html"}:
        raise ValueError("only .md and .html files are allowed")
    return resolved


def first_h1(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("# "):
                    return stripped[2:].strip() or path.stem
    except Exception:
        pass
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


def content_list() -> list[dict[str, Any]]:
    if not CONTENT_ROOT.exists():
        return []
    docs = []
    for path in sorted([*CONTENT_ROOT.rglob("*.md"), *CONTENT_ROOT.rglob("*.html")], key=lambda p: str(p).lower()):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(CONTENT_ROOT)
        except ValueError:
            continue
        parts = rel.parts
        agent = parts[0] if len(parts) > 1 else "root"
        filename = str(Path(*parts[1:])) if len(parts) > 1 else parts[0]
        st = resolved.stat()
        docs.append({
            "agent": agent,
            "filename": filename,
            "title": first_h1(resolved),
            "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "size": st.st_size,
            "type": resolved.suffix.lower().lstrip("."),
        })
    return docs


def content_get(raw_path: str | None) -> str:
    path = validate_content_path(raw_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("content file not found")
    return path.read_text(encoding="utf-8", errors="replace")


def content_save(data: dict[str, Any]) -> dict[str, Any]:
    path = validate_content_path(data.get("path"))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("content file not found")
    content = str(data.get("content", ""))
    path.write_text(content, encoding="utf-8")
    st = path.stat()
    return {
        "ok": True,
        "path": str(path),
        "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "size": st.st_size,
    }


# ---------- Mission State / Operating Graph ----------
def mission_state() -> dict[str, Any]:
    """Build a compact shared mission state from existing durable stores.

    The first version is intentionally derived from current Mission Control data
    instead of creating a new source of truth: bridge commands are the mission
    ledger, board rows are operator tasks, workflow runs are execution traces,
    and recent content files are artifacts.
    """
    history = bridge_history(12)
    tasks = board_list()
    workflows = workflow_list()
    runs = workflow_runs(12)
    docs = content_list()[:8]

    open_tasks = [t for t in tasks if str(t.get("status", "")).lower() != "completed"]
    active_tasks = [t for t in tasks if str(t.get("status", "")).lower() in {"in_progress", "active", "running"}]
    latest = history[0] if history else {}
    latest_target = latest.get("target") or "@orchestrator"
    latest_message = latest.get("message") or "No mission command captured yet."
    target_agent = BRIDGE_TARGETS.get(latest_target, {}).get("name", latest_target.lstrip("@").title())

    phases = [
        {
            "id": "goal",
            "label": "Goal",
            "owner": target_agent,
            "status": "active" if history else "waiting",
            "summary": latest_message[:220],
            "evidence": latest.get("ts"),
        },
        {
            "id": "plan",
            "label": "Plan",
            "owner": "Orchestrator",
            "status": "active" if open_tasks else "waiting",
            "summary": f"{len(open_tasks)} open board item(s), {len(active_tasks)} in progress.",
            "evidence": (open_tasks[0].get("title") if open_tasks else "No open board tasks."),
        },
        {
            "id": "build",
            "label": "Build",
            "owner": "Forge / Dev / Lumen",
            "status": "active" if runs or active_tasks else "standby",
            "summary": f"{len(runs)} recent workflow run(s) and {len(workflows)} saved workflow(s).",
            "evidence": (runs[0].get("workflow_title") if runs else (active_tasks[0].get("title") if active_tasks else "No active run.")),
        },
        {
            "id": "verify",
            "label": "Verify",
            "owner": "Rank / Forge",
            "status": "ready" if history else "waiting",
            "summary": "Use API checks, browser console, screenshots, and bridge sends before marking done.",
            "evidence": "Verification gate enabled in Mission Graph.",
        },
        {
            "id": "ship",
            "label": "Ship",
            "owner": "Scribe / Orchestrator",
            "status": "ready" if docs else "waiting",
            "summary": f"{len(docs)} recent artifact(s) available in content library.",
            "evidence": (docs[0].get("title") if docs else "No content artifact indexed."),
        },
    ]

    return {
        "objective": latest_message,
        "owner": target_agent,
        "target": latest_target,
        "updated_at": latest.get("ts") or now_iso(),
        "phase": next((p["label"] for p in phases if p["status"] == "active"), "Ready"),
        "open_questions": [
            "What acceptance evidence is required before this mission is done?",
            "Which agent owns the next irreversible action?",
        ],
        "decisions": [
            {"label": h.get("target", "@orchestrator"), "summary": h.get("message", "")[:160], "ts": h.get("ts")}
            for h in history[:6]
        ],
        "artifacts": [
            {"title": d.get("title") or d.get("filename"), "agent": d.get("agent"), "type": d.get("type"), "modified_at": d.get("modified_at")}
            for d in docs[:6]
        ],
        "phases": phases,
    }

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
    board_section = safe_call("board", lambda: {"tasks": board_list()})
    mission_graph_section = safe_call("mission_state", mission_state)
    missions_section = safe_call("missions", mission_summary)
    mission_memory_section = safe_call("mission_memory", mission_memory_current)

    activity = _payload(activity_section, {}) or {}
    sessions = _payload(sessions_section, {}) or {}
    vps = _payload(vps_section, {}) or {}
    gateway = _payload(gateway_section, {}) or {}
    cron = _payload(cron_section, {}) or {}
    board = _payload(board_section, {}) or {}
    mission_graph = _payload(mission_graph_section, {}) or {}
    missions = _payload(missions_section, {}) or {}

    ram = vps.get("ram") or {}
    disk = vps.get("disk") or {}
    agents = activity.get("per_agent") or []
    stats = activity.get("totals") or {"total": 0, "completed": 0, "failed": 0}
    totals = sessions.get("token_totals") or {}
    tasks = board.get("tasks") or []

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
        "kanban": {"total": len([t for t in tasks if t.get("status") != "completed"])},
        "cron_jobs": cron_section,
        "crons": normalized_crons(cron),
        "board": board_section,
        "mission": mission_graph,
        "missions": missions,
        "mission_state": mission_graph_section,
        "mission_memory": mission_memory_section,
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

    def read_multipart_form(self) -> multipart.MultipartForm:
        """Parse an upload body.

        Uses the local multipart module rather than `cgi`, which was removed
        in Python 3.13. MultipartError subclasses ValueError, so the existing
        handler that turns ValueError into a 400 keeps working unchanged.
        """
        return multipart.parse(self.headers, self.rfile)

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
            "/api/board", "/api/content", "/api/bridge/history", "/api/workflows", "/api/workflow-runs", "/api/flight-recorder",
            "/api/mission", "/api/missions", "/api/mission-events", "/api/uploads",
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
                "kanban": snap.get("kanban"),
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
        if parsed.path in {"/api/mission", "/api/missions"}:
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or [25])[0])
            active = mission_summary()
            payload = {"ok": True, "mission": active.get("active"), "missions": active.get("missions", []), "events": active.get("events", [])}
            self.send_json(payload)
            return
        if parsed.path == "/api/mission-events":
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or [40])[0])
            mission_id = (qs.get("mission_id") or [""])[0] or None
            events = mission_events(mission_id, limit)
            self.send_json({"ok": True, "events": [{**e, "summary": e.get("text", ""), "status": e.get("kind", "logged")} for e in events]})
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
        if parsed.path == "/api/board":
            self.send_json({"ok": True, "tasks": board_list()})
            return
        if parsed.path == "/api/content":
            try:
                self.send_json(content_list())
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            return
        if parsed.path == "/api/content/get":
            try:
                qs = parse_qs(parsed.query)
                raw_path = (qs.get("path") or [""])[0]
                ctype = "text/html; charset=utf-8" if str(raw_path).lower().endswith(".html") else "text/markdown; charset=utf-8"
                self.send_text(content_get(raw_path), content_type=ctype)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            return
        if parsed.path.startswith("/uploads/"):
            try:
                rel = parsed.path[len("/uploads/"):].strip("/")
                candidate = (UPLOAD_ROOT / rel).resolve()
                candidate.relative_to(UPLOAD_ROOT.resolve())
                if not candidate.exists() or not candidate.is_file():
                    raise FileNotFoundError("upload not found")
                data = candidate.read_bytes()
                ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                safe_write(self, data)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 404)
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
        if parsed.path == "/api/workflows":
            try:
                self.send_json({"ok": True, "workflows": workflow_list()})
            except Exception as exc:
                self.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            return
        if parsed.path == "/api/workflow-runs":
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or [50])[0])
                self.send_json({"ok": True, "runs": workflow_runs(limit)})
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
            if parsed.path == "/api/bridge/upload":
                form = self.read_multipart_form()
                self.send_json({"ok": True, "attachments": save_uploaded_files(form)}, 201)
                return
            data = self.read_body_json()
            if agency.handle_post(self, parsed, qs, data):
                return
            if parsed.path == "/api/board":
                self.send_json({"ok": True, "task": board_create(data)}, 201)
                return
            if parsed.path == "/api/board/update":
                task_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, "task": board_update(task_id, data)})
                return
            if parsed.path == "/api/board/delete":
                task_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, **board_delete(task_id)})
                return
            if parsed.path == "/api/content/save":
                self.send_json(content_save(data))
                return
            if parsed.path == "/api/bridge/favorite":
                self.send_json({"ok": True, "favorite": bridge_favorite(data)}, 201)
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
            if parsed.path in {"/api/mission", "/api/missions"}:
                self.send_json({"ok": True, "mission": mission_create(data)}, 201)
                return
            if parsed.path == "/api/missions/update":
                mission_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, "mission": mission_update(mission_id, data)})
                return
            if parsed.path == "/api/missions/fork":
                mission_id = (qs.get("id") or [data.get("mission_id", "")])[0]
                self.send_json({"ok": True, "mission": mission_fork(mission_id, str(data.get("instruction") or ""))}, 201)
                return
            if parsed.path in {"/api/missions/event", "/api/mission-events"}:
                self.send_json({"ok": True, "event": mission_add_event(data)}, 201)
                return
            if parsed.path == "/api/workflows":
                self.send_json({"ok": True, "workflow": workflow_create(data)}, 201)
                return
            if parsed.path == "/api/workflows/update":
                workflow_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, "workflow": workflow_update(workflow_id, data)})
                return
            if parsed.path == "/api/workflows/delete":
                workflow_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, **workflow_delete(workflow_id)})
                return
            if parsed.path == "/api/workflow-runs":
                self.send_json({"ok": True, "run": workflow_run_create(data)}, 201)
                return
            if parsed.path == "/api/workflow-runs/update":
                run_id = (qs.get("id") or [""])[0]
                self.send_json({"ok": True, "run": workflow_run_update(run_id, data)})
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
    init_board()
    init_bridge()
    init_workflows()
    init_missions()
    ensure_uploads_dir()
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

