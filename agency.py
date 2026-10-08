"""Agency OS backend module.

All new Agency Operating System logic lives here; server.py hooks in with
three one-line calls (handle_get / handle_post / static app serving).
Python stdlib only, same as the rest of Mission Control.

Data lives in agency.db (WAL). Every metric row carries source + provenance
(estimated | imported | verified) — the UI refuses to show numbers without
them. Mutations require a bearer token (see _load_or_create_token) and are
written to activity_log.
"""

from __future__ import annotations

import json
import mimetypes
import re
from urllib.parse import quote
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gsc

PROJECT_DIR = Path(__file__).resolve().parent
AGENCY_DB = PROJECT_DIR / "agency.db"
APP_DIR = PROJECT_DIR / "app"
TOKEN_PATH = PROJECT_DIR / ".agency_token"

PROVENANCE = {"estimated", "imported", "verified"}
APPROVAL_KINDS = {"copy", "deploy", "outreach", "proposal", "other"}
AGENT_STATES = {"idle", "planning", "running", "waiting", "needs_approval", "blocked", "failed", "completed"}
PROJECT_TYPES = {
    "marketing_website", "landing_page", "saas_app", "ecommerce", "mobile_app",
    "seo_campaign", "content_campaign", "redesign", "technical_audit", "other",
}

_init_lock = threading.Lock()
_initialized = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(AGENCY_DB, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _conn():
    """Transaction + guaranteed close. sqlite3's own context manager only
    commits/rolls back; it never closes, which leaks WAL file handles on
    Windows."""
    conn = _raw_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ---------- token ----------

def _load_or_create_token() -> str:
    try:
        tok = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(tok, encoding="utf-8")
    return tok


AGENCY_TOKEN = _load_or_create_token()


def _host_ok(handler) -> bool:
    """Reject DNS-rebinding style requests: Host must be a localhost form."""
    host = (handler.headers.get("Host") or "").split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "[::1]", "::1"}


def _auth_ok(handler) -> bool:
    header = handler.headers.get("Authorization") or ""
    return header == f"Bearer {AGENCY_TOKEN}"


# ---------- migrations ----------

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        company TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        services TEXT DEFAULT '[]',
        contacts TEXT DEFAULT '[]',
        notes TEXT DEFAULT '',
        mrr_cents INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        type TEXT DEFAULT 'other',
        status TEXT DEFAULT 'active',
        stage INTEGER DEFAULT 1,
        stage_total INTEGER DEFAULT 20,
        health TEXT DEFAULT 'on_track',
        brief TEXT DEFAULT '',
        due_date TEXT DEFAULT '',
        budget_cents INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'medium',
        assignee TEXT DEFAULT '',
        due_date TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        project_id TEXT DEFAULT '',
        title TEXT NOT NULL,
        detail TEXT DEFAULT '',
        kind TEXT DEFAULT 'other',
        status TEXT DEFAULT 'pending',
        blocking INTEGER DEFAULT 0,
        source_agent TEXT DEFAULT '',
        payload TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        decided_at TEXT DEFAULT '',
        decided_by TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT DEFAULT '',
        purpose TEXT DEFAULT '',
        gateway_target TEXT DEFAULT '',
        permissions TEXT DEFAULT '[]',
        approval_required INTEGER DEFAULT 1,
        status TEXT DEFAULT 'idle',
        last_run_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        project_id TEXT DEFAULT '',
        state TEXT DEFAULT 'running',
        summary TEXT DEFAULT '',
        cost_cents INTEGER DEFAULT 0,
        tokens_in INTEGER DEFAULT 0,
        tokens_out INTEGER DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT DEFAULT '',
        error TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS seo_keywords (
        id TEXT PRIMARY KEY,
        client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
        keyword TEXT NOT NULL,
        intent TEXT DEFAULT '',
        volume INTEGER,
        difficulty INTEGER,
        position INTEGER,
        prev_position INTEGER,
        url TEXT DEFAULT '',
        source TEXT DEFAULT 'manual',
        provenance TEXT NOT NULL DEFAULT 'estimated',
        tracked_since TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS boards (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT DEFAULT 'custom',
        client_id TEXT DEFAULT '',
        layout TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS activity_log (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        actor TEXT DEFAULT 'operator',
        action TEXT NOT NULL,
        entity_type TEXT DEFAULT '',
        entity_id TEXT DEFAULT '',
        detail TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
    CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
    CREATE INDEX IF NOT EXISTS idx_keywords_client ON seo_keywords(client_id);
    CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(ts DESC);
    """),
    (2, """
    ALTER TABLE seo_keywords ADD COLUMN clicks INTEGER;
    ALTER TABLE seo_keywords ADD COLUMN impressions INTEGER;
    ALTER TABLE seo_keywords ADD COLUMN ctr REAL;
    CREATE TABLE IF NOT EXISTS gsc_properties (
        client_id TEXT PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
        site_url TEXT NOT NULL,
        mapped_at TEXT NOT NULL,
        last_sync_at TEXT DEFAULT '',
        last_sync_note TEXT DEFAULT ''
    );
    """),
    (3, """
    CREATE TABLE IF NOT EXISTS backlink_prospects (
        id TEXT PRIMARY KEY,
        client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
        domain TEXT NOT NULL,
        url TEXT DEFAULT '',
        contact TEXT DEFAULT '',
        kind TEXT DEFAULT 'other',
        status TEXT DEFAULT 'identified',
        notes TEXT DEFAULT '',
        draft TEXT DEFAULT '',
        approval_id TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS competitors (
        id TEXT PRIMARY KEY,
        client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
        domain TEXT NOT NULL,
        name TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_backlinks_client ON backlink_prospects(client_id);
    CREATE INDEX IF NOT EXISTS idx_competitors_client ON competitors(client_id);
    """),
    (4, """
    ALTER TABLE projects ADD COLUMN url TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN playbook TEXT DEFAULT '';
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        playbook TEXT NOT NULL,
        state TEXT DEFAULT 'queued',
        stage_index INTEGER DEFAULT 0,
        error TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS stage_runs (
        id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES pipeline_runs(id) ON DELETE CASCADE,
        stage_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        kind TEXT DEFAULT 'llm',
        agent TEXT DEFAULT '',
        state TEXT DEFAULT 'pending',
        detail TEXT DEFAULT '',
        error TEXT DEFAULT '',
        tokens_in INTEGER DEFAULT 0,
        tokens_out INTEGER DEFAULT 0,
        seconds REAL DEFAULT 0,
        position INTEGER DEFAULT 0,
        started_at TEXT DEFAULT '',
        finished_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES pipeline_runs(id) ON DELETE CASCADE,
        project_id TEXT,
        stage_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        kind TEXT DEFAULT 'markdown',
        content TEXT DEFAULT '',
        file_path TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_runs_project ON pipeline_runs(project_id);
    CREATE INDEX IF NOT EXISTS idx_stage_runs_run ON stage_runs(run_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);
    """),
    # 2026-10-08: Antigravity and ChatGPT retired. In four months neither ran a
    # stage, raised an approval or logged activity; ChatGPT is a model (the
    # brain's second engine), not a team member. Rows stay for their history.
    (5, """
    UPDATE agents SET status = 'retired' WHERE id IN ('antigravity', 'chatgpt');
    """),
    # Autopilot: scheduled campaigns per site, and monthly client reports as PDFs.
    (6, """
    CREATE TABLE IF NOT EXISTS autopilot (
        project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0,
        every_days INTEGER NOT NULL DEFAULT 7,
        last_run_at TEXT NOT NULL DEFAULT '',
        last_run_id TEXT NOT NULL DEFAULT '',
        last_note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS client_reports (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (client_id, period)
    );
    """),
]

BACKLINK_KINDS = {"resource_page", "broken_link", "guest_post", "digital_pr", "partnership", "unlinked_mention", "other"}
BACKLINK_STATUSES = ["identified", "qualified", "outreach_drafted", "approved", "sent", "responded", "linked", "lost"]

DEFAULT_AGENTS = [
    ("orchestrator", "Orchestrator", "Pipeline coordination", "@orchestrator"),
    ("scout", "Scout", "Research and discovery", "@scout"),
    ("scribe", "Scribe", "Content and copywriting", "@scribe"),
    ("reach", "Reach", "Outreach and communication", "@reach"),
    ("dev", "Dev", "Development and integrations", "@dev"),
    ("lumen", "Lumen", "Design and UX", "@lumen"),
    ("forge", "Forge", "Build and tooling", "@forge"),
    ("rank", "Rank", "SEO analysis and tracking", "@rank"),
    ("stitch", "Stitch", "UI generation and visual prototyping", "@stitch"),
]


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        with _conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )""")
            applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)", (version, now_iso()))
            count = conn.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"]
            if count == 0:
                for agent_id, name, role, target in DEFAULT_AGENTS:
                    conn.execute(
                        "INSERT INTO agents (id,name,role,purpose,gateway_target,status) VALUES (?,?,?,?,?,'idle')",
                        (agent_id, name, role, role, target),
                    )
            board = conn.execute("SELECT COUNT(*) AS n FROM boards WHERE kind='default'").fetchone()["n"]
            if board == 0:
                conn.execute(
                    "INSERT INTO boards (id,name,kind,layout,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "Command Deck", "default", "[]", now_iso(), now_iso()),
                )
        _initialized = True


def _cutoff_iso(days: int) -> str:
    """Cutoff timestamp in the same format the columns actually store.

    Timestamps are written by now_iso() as '2026-09-09T08:30:46.4+00:00' while
    SQLite's datetime('now') yields '2026-09-09 08:30:46'. Comparing those as
    strings goes wrong at the boundary: 'T' sorts after ' ', so any row from the
    cutoff *date* counted as later than the cutoff whatever its time, widening
    every window by up to a day. Building the cutoff in the stored format makes
    the comparison exact.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def log_activity(conn: sqlite3.Connection, action: str, entity_type: str, entity_id: str, detail: dict | None = None, actor: str = "operator") -> None:
    conn.execute(
        "INSERT INTO activity_log (id,ts,actor,action,entity_type,entity_id,detail) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), now_iso(), actor, action, entity_type, entity_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def _rows(cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def _json_field(row: dict[str, Any], key: str) -> None:
    try:
        row[key] = json.loads(row.get(key) or "[]")
    except Exception:
        row[key] = []


# ---------- CRUD ----------

def clients_list() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = _rows(conn.execute("SELECT * FROM clients ORDER BY name COLLATE NOCASE"))
    for r in rows:
        _json_field(r, "services")
        _json_field(r, "contacts")
    return rows


def client_create(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "company": str(data.get("company", "")).strip(),
        "status": str(data.get("status", "active")),
        "services": json.dumps(data.get("services") or [], ensure_ascii=False),
        "contacts": json.dumps(data.get("contacts") or [], ensure_ascii=False),
        "notes": str(data.get("notes", "")),
        "mrr_cents": int(data.get("mrr_cents") or 0),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO clients (id,name,company,status,services,contacts,notes,mrr_cents,created_at,updated_at) "
            "VALUES (:id,:name,:company,:status,:services,:contacts,:notes,:mrr_cents,:created_at,:updated_at)", row)
        log_activity(conn, "create", "client", row["id"], {"name": name})
    _json_field(row, "services")
    _json_field(row, "contacts")
    return row


def client_update(client_id: str, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "company", "status", "notes"}
    fields: dict[str, Any] = {k: str(v) for k, v in data.items() if k in allowed}
    if "mrr_cents" in data:
        fields["mrr_cents"] = int(data["mrr_cents"] or 0)
    for jkey in ("services", "contacts"):
        if jkey in data:
            fields[jkey] = json.dumps(data[jkey] or [], ensure_ascii=False)
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE clients SET {sets} WHERE id=?", [*fields.values(), client_id])
        if cur.rowcount == 0:
            raise KeyError("client not found")
        log_activity(conn, "update", "client", client_id, {"fields": sorted(fields)})
        row = dict(conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone())
    _json_field(row, "services")
    _json_field(row, "contacts")
    return row


def client_delete(client_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "client", client_id)
        return {"deleted": cur.rowcount, "id": client_id}


def projects_list(client_id: str = "") -> list[dict[str, Any]]:
    q = "SELECT p.*, c.name AS client_name FROM projects p LEFT JOIN clients c ON c.id=p.client_id"
    args: tuple = ()
    if client_id:
        q += " WHERE p.client_id=?"
        args = (client_id,)
    q += " ORDER BY p.updated_at DESC"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def project_create(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    ptype = str(data.get("type", "other"))
    if ptype not in PROJECT_TYPES:
        raise ValueError(f"type must be one of {sorted(PROJECT_TYPES)}")
    row = {
        "id": str(uuid.uuid4()),
        "client_id": str(data.get("client_id") or "") or None,
        "name": name,
        "type": ptype,
        "status": str(data.get("status", "active")),
        "stage": int(data.get("stage") or 1),
        "stage_total": int(data.get("stage_total") or 20),
        "health": str(data.get("health", "on_track")),
        "brief": str(data.get("brief", "")),
        "due_date": str(data.get("due_date", "")),
        "budget_cents": int(data.get("budget_cents") or 0),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO projects (id,client_id,name,type,status,stage,stage_total,health,brief,due_date,budget_cents,created_at,updated_at) "
            "VALUES (:id,:client_id,:name,:type,:status,:stage,:stage_total,:health,:brief,:due_date,:budget_cents,:created_at,:updated_at)", row)
        log_activity(conn, "create", "project", row["id"], {"name": name})
    return row


PROJECT_STATUSES = {"active", "live", "paused", "archived"}


def project_update(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    # "url" was missing here, so editing a project's website address was
    # silently dropped while the save reported success.
    allowed = {"name", "status", "health", "brief", "due_date", "client_id", "type", "url"}
    fields: dict[str, Any] = {k: str(v).strip() for k, v in data.items() if k in allowed}
    if "type" in fields and fields["type"] not in PROJECT_TYPES:
        raise ValueError(f"type must be one of {sorted(PROJECT_TYPES)}")
    if "status" in fields and fields["status"] not in PROJECT_STATUSES:
        raise ValueError(f"status must be one of {sorted(PROJECT_STATUSES)}")
    if fields.get("url") and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", fields["url"], re.I):
        raise ValueError("the website URL must start with http:// or https://")
    if fields.get("status") == "live":
        # "live" means a public address exists: the overview counts live sites by it
        current = None
        with _conn() as conn:
            row = conn.execute("SELECT url FROM projects WHERE id=?", (project_id,)).fetchone()
            current = row["url"] if row else None
        if not (fields.get("url") or current):
            raise ValueError("add the live website's URL before marking it live")
    for ikey in ("stage", "stage_total", "budget_cents"):
        if ikey in data:
            fields[ikey] = int(data[ikey] or 0)
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE projects SET {sets} WHERE id=?", [*fields.values(), project_id])
        if cur.rowcount == 0:
            raise KeyError("project not found")
        log_activity(conn, "update", "project", project_id, {"fields": sorted(fields)})
        return dict(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())


def project_delete(project_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "project", project_id)
        return {"deleted": cur.rowcount, "id": project_id}


def agency_tasks_list(project_id: str = "") -> list[dict[str, Any]]:
    q = "SELECT t.*, p.name AS project_name FROM tasks t LEFT JOIN projects p ON p.id=t.project_id"
    args: tuple = ()
    if project_id:
        q += " WHERE t.project_id=?"
        args = (project_id,)
    q += " ORDER BY CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, t.created_at DESC"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def agency_task_create(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("title is required")
    row = {
        "id": str(uuid.uuid4()),
        "project_id": str(data.get("project_id") or "") or None,
        "title": title,
        "status": str(data.get("status", "pending")),
        "priority": str(data.get("priority", "medium")),
        "assignee": str(data.get("assignee", "")),
        "due_date": str(data.get("due_date", "")),
        "notes": str(data.get("notes", "")),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id,project_id,title,status,priority,assignee,due_date,notes,created_at,updated_at) "
            "VALUES (:id,:project_id,:title,:status,:priority,:assignee,:due_date,:notes,:created_at,:updated_at)", row)
        log_activity(conn, "create", "task", row["id"], {"title": title})
    return row


def agency_task_update(task_id: str, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"title", "status", "priority", "assignee", "due_date", "notes", "project_id"}
    fields = {k: str(v) for k, v in data.items() if k in allowed}
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", [*fields.values(), task_id])
        if cur.rowcount == 0:
            raise KeyError("task not found")
        log_activity(conn, "update", "task", task_id, {"fields": sorted(fields)})
        return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def agency_task_delete(task_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "task", task_id)
        return {"deleted": cur.rowcount, "id": task_id}


def approvals_list(status: str = "") -> list[dict[str, Any]]:
    q = "SELECT a.*, p.name AS project_name FROM approvals a LEFT JOIN projects p ON p.id=a.project_id"
    args: tuple = ()
    if status:
        q += " WHERE a.status=?"
        args = (status,)
    q += " ORDER BY a.blocking DESC, a.created_at ASC"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def approval_create(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("title is required")
    kind = str(data.get("kind", "other"))
    if kind not in APPROVAL_KINDS:
        raise ValueError(f"kind must be one of {sorted(APPROVAL_KINDS)}")
    row = {
        "id": str(uuid.uuid4()),
        "project_id": str(data.get("project_id") or ""),
        "title": title,
        "detail": str(data.get("detail", "")),
        "kind": kind,
        "status": "pending",
        "blocking": 1 if data.get("blocking") else 0,
        "source_agent": str(data.get("source_agent", "")),
        "payload": json.dumps(data.get("payload") or {}, ensure_ascii=False),
        "created_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO approvals (id,project_id,title,detail,kind,status,blocking,source_agent,payload,created_at) "
            "VALUES (:id,:project_id,:title,:detail,:kind,:status,:blocking,:source_agent,:payload,:created_at)", row)
        log_activity(conn, "create", "approval", row["id"], {"title": title, "kind": kind})
    return row


def approval_decide(approval_id: str, decision: str, decided_by: str = "operator") -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be 'approved' or 'rejected'")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='pending'",
            (decision, now_iso(), decided_by, approval_id))
        if cur.rowcount == 0:
            raise KeyError("pending approval not found")
        log_activity(conn, decision, "approval", approval_id)
        row = dict(conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone())
        # outreach approvals gate the backlink CRM: reflect the decision there
        if row.get("kind") == "outreach":
            try:
                prospect_id = json.loads(row.get("payload") or "{}").get("prospect_id", "")
            except json.JSONDecodeError:
                prospect_id = ""
            if prospect_id:
                new_status = "approved" if decision == "approved" else "qualified"
                conn.execute("UPDATE backlink_prospects SET status=?, updated_at=? WHERE id=?",
                             (new_status, now_iso(), prospect_id))
    # a pipeline gate: record the decision on the stage, then resume or stop the run
    try:
        payload = json.loads(row.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}
    run_id, stage_id = payload.get("run_id"), payload.get("stage_id")
    if run_id and stage_id:
        with _conn() as conn:
            conn.execute("UPDATE stage_runs SET state=?, finished_at=?, detail=? WHERE run_id=? AND stage_id=?",
                         ("done" if decision == "approved" else "rejected", now_iso(),
                          f"{decision} by {decided_by}", run_id, stage_id))
        import runner
        if decision == "approved":
            try:
                runner.resume_run(run_id)
            except Exception as exc:  # a resume failure must be visible, not silent
                with _conn() as conn:
                    conn.execute("UPDATE pipeline_runs SET state='failed', error=? WHERE id=?",
                                 (f"could not resume after approval: {exc}", run_id))
        else:
            with _conn() as conn:
                conn.execute("UPDATE pipeline_runs SET state='stopped', error='gate rejected by operator', "
                             "updated_at=? WHERE id=?", (now_iso(), run_id))
    return row


def agents_list(include_retired: bool = False) -> list[dict[str, Any]]:
    """The team. Retired agents keep their row (and history) but are not listed."""
    with _conn() as conn:
        rows = _rows(conn.execute("SELECT * FROM agents ORDER BY name"))
    if not include_retired:
        rows = [r for r in rows if r.get("status") != "retired"]
    with _conn() as conn:
        runs = _rows(conn.execute(
            "SELECT agent_id, state, summary, started_at FROM agent_runs "
            "WHERE finished_at='' ORDER BY started_at DESC"))
    live = {}
    for r in runs:
        live.setdefault(r["agent_id"], r)
    for row in rows:
        _json_field(row, "permissions")
        run = live.get(row["id"])
        if run:
            row["status"] = run["state"]
            row["current_run"] = run
    return rows


LIVE_RUN_STATES = ("queued", "running", "awaiting_approval")
_TEST_PROJECT = re.compile(r"(^zz[\s_-]|\btest\b|\bselftest\b|\be2e\b)", re.I)   # as the Builder page hides them


def office_state() -> dict[str, Any]:
    """Who is working on what, for the office view. Live records only.

    An agent is 'working' while a stage of a live pipeline run names it (or an
    agent run from Ask the agency is open), 'waiting' while a run is paused on
    an approval its stage raised (or an approval it raised is pending), and
    'free' otherwise. A stage left 'running' inside a run that ended is stale
    and ignored. Automated stages (audits, checks) are shown at the
    Orchestrator's desk, since it runs them.
    """
    agents = agents_list()
    ids = {a["id"] for a in agents}
    live = ",".join("?" * len(LIVE_RUN_STATES))
    with _conn() as conn:
        stages = _rows(conn.execute(
            f"SELECT s.agent, s.title, s.state, s.started_at, r.id AS run_id, r.playbook, "
            f"p.id AS project_id, p.name AS project_name "
            f"FROM stage_runs s JOIN pipeline_runs r ON r.id = s.run_id "
            f"LEFT JOIN projects p ON p.id = r.project_id "
            f"WHERE s.state IN ('running', 'awaiting_approval') AND r.state IN ({live}) "
            f"ORDER BY s.started_at", LIVE_RUN_STATES))
        pending = _rows(conn.execute(
            "SELECT a.source_agent, a.title, a.project_id, p.name AS project_name FROM approvals a "
            "LEFT JOIN projects p ON p.id = a.project_id WHERE a.status = 'pending' ORDER BY a.created_at"))
        recent = _rows(conn.execute(
            "SELECT s.agent, s.title, s.state, s.started_at, s.finished_at, p.name AS project_name "
            "FROM stage_runs s JOIN pipeline_runs r ON r.id = s.run_id LEFT JOIN projects p ON p.id = r.project_id "
            "WHERE s.started_at != '' AND (s.state IN ('done', 'failed') "
            f"OR (s.state IN ('running', 'awaiting_approval') AND r.state IN ({live}))) "
            "ORDER BY COALESCE(NULLIF(s.finished_at, ''), s.started_at) DESC LIMIT 20", LIVE_RUN_STATES))
    owner = lambda agent: agent if agent in ids else "orchestrator"
    state: dict[str, dict[str, Any]] = {}
    for s in stages:
        who = owner(s["agent"] or "")
        waiting = s["state"] == "awaiting_approval"
        task = s["title"] if who == s["agent"] else f"Running: {s['title']}"
        cur = state.get(who)
        if waiting or not cur or cur["state"] != "waiting":
            state[who] = {"state": "waiting" if waiting else "working", "task": task,
                          "project": s["project_name"] or "", "project_id": s["project_id"] or "",
                          "since": s["started_at"]}
    for p in pending:
        who = owner(p["source_agent"] or "")
        state[who] = {"state": "waiting", "task": p["title"], "project": p["project_name"] or "",
                      "project_id": p["project_id"] or "", "since": ""}
    for a in agents:
        run = a.get("current_run")
        if run and a["id"] not in state:
            state[a["id"]] = {"state": "working", "task": run.get("summary") or "Working on a request",
                              "project": "", "project_id": "", "since": run.get("started_at", "")}
    out = []
    for a in agents:
        s = state.get(a["id"], {"state": "free", "task": "", "project": "", "project_id": "", "since": ""})
        out.append({"id": a["id"], "name": a["name"], "role": a.get("role") or "", **s})
    feed = []
    for r in recent:
        if _TEST_PROJECT.search(r["project_name"] or ""):
            continue                                   # the dashboard's own test runs are not team news
        who = next((a["name"] for a in agents if a["id"] == r["agent"]), "Orchestrator")
        verb = {"done": "finished", "failed": "hit a problem on", "running": "started",
                "awaiting_approval": "is waiting on you for"}[r["state"]]
        feed.append({"text": f"{who} {verb}: {r['title']}", "project": r["project_name"] or "",
                     "at": r["finished_at"] or r["started_at"]})
    return {"agents": out, "feed": feed[:6],
            "counts": {k: sum(1 for a in out if a["state"] == k) for k in ("working", "waiting", "free")}}


def keywords_list(client_id: str = "") -> list[dict[str, Any]]:
    q = "SELECT k.*, c.name AS client_name FROM seo_keywords k LEFT JOIN clients c ON c.id=k.client_id"
    args: tuple = ()
    if client_id:
        q += " WHERE k.client_id=?"
        args = (client_id,)
    q += " ORDER BY k.updated_at DESC"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def keyword_create(data: dict[str, Any]) -> dict[str, Any]:
    keyword = str(data.get("keyword", "")).strip()
    if not keyword:
        raise ValueError("keyword is required")
    provenance = str(data.get("provenance", "")).strip()
    if provenance not in PROVENANCE:
        raise ValueError(f"provenance is required and must be one of {sorted(PROVENANCE)}")
    def _opt_int(key: str):
        v = data.get(key)
        return None if v in (None, "") else int(v)
    row = {
        "id": str(uuid.uuid4()),
        "client_id": str(data.get("client_id") or "") or None,
        "keyword": keyword,
        "intent": str(data.get("intent", "")),
        "volume": _opt_int("volume"),
        "difficulty": _opt_int("difficulty"),
        "position": _opt_int("position"),
        "prev_position": _opt_int("prev_position"),
        "url": str(data.get("url", "")),
        "source": str(data.get("source", "manual")),
        "provenance": provenance,
        "tracked_since": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO seo_keywords (id,client_id,keyword,intent,volume,difficulty,position,prev_position,url,source,provenance,tracked_since,updated_at) "
            "VALUES (:id,:client_id,:keyword,:intent,:volume,:difficulty,:position,:prev_position,:url,:source,:provenance,:tracked_since,:updated_at)", row)
        log_activity(conn, "create", "keyword", row["id"], {"keyword": keyword, "provenance": provenance})
    return row


def keyword_update(keyword_id: str, data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for skey in ("keyword", "intent", "url", "source", "client_id"):
        if skey in data:
            fields[skey] = str(data[skey])
    for ikey in ("volume", "difficulty", "position", "prev_position"):
        if ikey in data:
            fields[ikey] = None if data[ikey] in (None, "") else int(data[ikey])
    if "provenance" in data:
        if str(data["provenance"]) not in PROVENANCE:
            raise ValueError(f"provenance must be one of {sorted(PROVENANCE)}")
        fields["provenance"] = str(data["provenance"])
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE seo_keywords SET {sets} WHERE id=?", [*fields.values(), keyword_id])
        if cur.rowcount == 0:
            raise KeyError("keyword not found")
        log_activity(conn, "update", "keyword", keyword_id, {"fields": sorted(fields)})
        return dict(conn.execute("SELECT * FROM seo_keywords WHERE id=?", (keyword_id,)).fetchone())


def keyword_delete(keyword_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM seo_keywords WHERE id=?", (keyword_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "keyword", keyword_id)
        return {"deleted": cur.rowcount, "id": keyword_id}


def boards_list() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = _rows(conn.execute("SELECT * FROM boards ORDER BY CASE kind WHEN 'default' THEN 0 ELSE 1 END, name"))
    for r in rows:
        _json_field(r, "layout")
    return rows


def board_create(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "kind": str(data.get("kind", "custom")),
        "client_id": str(data.get("client_id") or ""),
        "layout": json.dumps(data.get("layout") or [], ensure_ascii=False),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO boards (id,name,kind,client_id,layout,created_at,updated_at) "
            "VALUES (:id,:name,:kind,:client_id,:layout,:created_at,:updated_at)", row)
        log_activity(conn, "create", "board", row["id"], {"name": name})
    _json_field(row, "layout")
    return row


def board_update(board_id: str, data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "name" in data:
        fields["name"] = str(data["name"])
    if "layout" in data:
        fields["layout"] = json.dumps(data["layout"] or [], ensure_ascii=False)
    if not fields:
        raise ValueError("no update fields provided")
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE boards SET {sets} WHERE id=?", [*fields.values(), board_id])
        if cur.rowcount == 0:
            raise KeyError("board not found")
        log_activity(conn, "update", "board", board_id, {"fields": sorted(fields)})
        row = dict(conn.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone())
    _json_field(row, "layout")
    return row


def board_delete(board_id: str) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT kind FROM boards WHERE id=?", (board_id,)).fetchone()
        if row and row["kind"] == "default":
            raise ValueError("the default board cannot be deleted")
        cur = conn.execute("DELETE FROM boards WHERE id=?", (board_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "board", board_id)
        return {"deleted": cur.rowcount, "id": board_id}


# ---------- backlink CRM ----------
# Ethics rule enforced in code: a prospect can only reach status 'sent' when a
# linked outreach approval has been explicitly approved by a human.

def backlinks_list(client_id: str = "") -> list[dict[str, Any]]:
    q = "SELECT b.*, c.name AS client_name FROM backlink_prospects b LEFT JOIN clients c ON c.id=b.client_id"
    args: tuple = ()
    if client_id:
        q += " WHERE b.client_id=?"
        args = (client_id,)
    q += " ORDER BY b.updated_at DESC"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def backlink_create(data: dict[str, Any]) -> dict[str, Any]:
    domain = str(data.get("domain", "")).strip().lower()
    if not domain:
        raise ValueError("domain is required")
    kind = str(data.get("kind", "other"))
    if kind not in BACKLINK_KINDS:
        raise ValueError(f"kind must be one of {sorted(BACKLINK_KINDS)}")
    row = {
        "id": str(uuid.uuid4()),
        "client_id": str(data.get("client_id") or "") or None,
        "domain": domain,
        "url": str(data.get("url", "")),
        "contact": str(data.get("contact", "")),
        "kind": kind,
        "status": "identified",
        "notes": str(data.get("notes", "")),
        "draft": str(data.get("draft", "")),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO backlink_prospects (id,client_id,domain,url,contact,kind,status,notes,draft,created_at,updated_at) "
            "VALUES (:id,:client_id,:domain,:url,:contact,:kind,:status,:notes,:draft,:created_at,:updated_at)", row)
        log_activity(conn, "create", "backlink", row["id"], {"domain": domain})
    return row


def backlink_update(prospect_id: str, data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("domain", "url", "contact", "notes", "draft", "client_id"):
        if key in data:
            fields[key] = str(data[key])
    if "kind" in data:
        if str(data["kind"]) not in BACKLINK_KINDS:
            raise ValueError(f"kind must be one of {sorted(BACKLINK_KINDS)}")
        fields["kind"] = str(data["kind"])
    with _conn() as conn:
        row = conn.execute("SELECT * FROM backlink_prospects WHERE id=?", (prospect_id,)).fetchone()
        if not row:
            raise KeyError("prospect not found")
        if "status" in data:
            new_status = str(data["status"])
            if new_status not in BACKLINK_STATUSES:
                raise ValueError(f"status must be one of {BACKLINK_STATUSES}")
            if new_status == "sent":
                apr = conn.execute("SELECT status FROM approvals WHERE id=?", (row["approval_id"],)).fetchone()
                if not apr or apr["status"] != "approved":
                    raise ValueError("outreach must be approved before it can be marked sent — request approval first")
            fields["status"] = new_status
        if not fields:
            raise ValueError("no update fields provided")
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE backlink_prospects SET {sets} WHERE id=?", [*fields.values(), prospect_id])
        log_activity(conn, "update", "backlink", prospect_id, {"fields": sorted(fields)})
        return dict(conn.execute("SELECT * FROM backlink_prospects WHERE id=?", (prospect_id,)).fetchone())


def backlink_request_approval(prospect_id: str) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM backlink_prospects WHERE id=?", (prospect_id,)).fetchone()
    if not row:
        raise KeyError("prospect not found")
    if not (row["draft"] or "").strip():
        raise ValueError("write the outreach draft first — approvals are for a concrete message, not a blank")
    approval = approval_create({
        "title": f"Outreach to {row['domain']}",
        "detail": (row["draft"] or "")[:500],
        "kind": "outreach",
        "blocking": False,
        "source_agent": "backlink-crm",
        "payload": {"prospect_id": prospect_id},
    })
    with _conn() as conn:
        conn.execute("UPDATE backlink_prospects SET approval_id=?, status='outreach_drafted', updated_at=? WHERE id=?",
                     (approval["id"], now_iso(), prospect_id))
    return approval


def backlink_delete(prospect_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM backlink_prospects WHERE id=?", (prospect_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "backlink", prospect_id)
        return {"deleted": cur.rowcount, "id": prospect_id}


# ---------- competitors ----------

def competitors_list(client_id: str = "") -> list[dict[str, Any]]:
    q = "SELECT co.*, c.name AS client_name FROM competitors co LEFT JOIN clients c ON c.id=co.client_id"
    args: tuple = ()
    if client_id:
        q += " WHERE co.client_id=?"
        args = (client_id,)
    q += " ORDER BY co.domain"
    with _conn() as conn:
        return _rows(conn.execute(q, args))


def competitor_create(data: dict[str, Any]) -> dict[str, Any]:
    domain = str(data.get("domain", "")).strip().lower()
    if not domain:
        raise ValueError("domain is required")
    row = {
        "id": str(uuid.uuid4()),
        "client_id": str(data.get("client_id") or "") or None,
        "domain": domain,
        "name": str(data.get("name", "")),
        "notes": str(data.get("notes", "")),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO competitors (id,client_id,domain,name,notes,created_at,updated_at) "
            "VALUES (:id,:client_id,:domain,:name,:notes,:created_at,:updated_at)", row)
        log_activity(conn, "create", "competitor", row["id"], {"domain": domain})
    return row


def competitor_delete(competitor_id: str) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM competitors WHERE id=?", (competitor_id,))
        if cur.rowcount:
            log_activity(conn, "delete", "competitor", competitor_id)
        return {"deleted": cur.rowcount, "id": competitor_id}


# ---------- search ----------

def search(qtext: str, limit: int = 30) -> list[dict[str, Any]]:
    qtext = qtext.strip()
    if not qtext:
        return []
    like = f"%{qtext}%"
    sources = [
        ("client", "SELECT id, name AS title, company AS detail, id AS client_id FROM clients WHERE name LIKE ? OR company LIKE ?"),
        ("project", "SELECT id, name AS title, type AS detail, client_id FROM projects WHERE name LIKE ? OR brief LIKE ?"),
        ("task", "SELECT id, title, notes AS detail, '' AS client_id FROM tasks WHERE title LIKE ? OR notes LIKE ?"),
        ("keyword", "SELECT id, keyword AS title, intent AS detail, client_id FROM seo_keywords WHERE keyword LIKE ? OR url LIKE ?"),
        ("approval", "SELECT id, title, kind AS detail, '' AS client_id FROM approvals WHERE title LIKE ? OR detail LIKE ?"),
        ("backlink", "SELECT id, domain AS title, status AS detail, client_id FROM backlink_prospects WHERE domain LIKE ? OR notes LIKE ?"),
        ("competitor", "SELECT id, domain AS title, name AS detail, client_id FROM competitors WHERE domain LIKE ? OR name LIKE ?"),
    ]
    out: list[dict[str, Any]] = []
    with _conn() as conn:
        for kind, sql in sources:
            for row in conn.execute(sql + " LIMIT 8", (like, like)):
                out.append({"kind": kind, "id": row["id"], "title": row["title"],
                            "detail": row["detail"] or "", "client_id": row["client_id"] or ""})
                if len(out) >= limit:
                    return out
    return out


# ---------- attention (derived notifications — no table to maintain) ----------

def attention() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with _conn() as conn:
        for a in conn.execute("SELECT id, title, blocking FROM approvals WHERE status='pending' ORDER BY blocking DESC, created_at"):
            items.append({
                "kind": "approval", "id": a["id"], "severity": "high" if a["blocking"] else "medium",
                "title": ("BLOCKING: " if a["blocking"] else "") + f"Approve or reject — {a['title']}",
            })
        for r in conn.execute(
                "SELECT r.id, r.summary, a.name FROM agent_runs r LEFT JOIN agents a ON a.id=r.agent_id "
                "WHERE r.state='failed' AND r.finished_at > ? ORDER BY r.finished_at DESC LIMIT 5", (_cutoff_iso(1),)):
            items.append({"kind": "run_failed", "id": r["id"], "severity": "high",
                          "title": f"Agent run failed — {r['name'] or 'fan-out'}: {r['summary'][:80]}"})
        for r in conn.execute(
                "SELECT r.id, r.error, r.playbook, p.name FROM pipeline_runs r "
                "LEFT JOIN projects p ON p.id=r.project_id "
                "WHERE r.state IN ('failed','stopped') AND r.updated_at > ? "
                "ORDER BY r.updated_at DESC LIMIT 5", (_cutoff_iso(3),)):
            items.append({"kind": "pipeline_failed", "id": r["id"], "severity": "high",
                          "title": f"Pipeline halted — {r['name'] or r['playbook']}: "
                                   f"{(r['error'] or 'no reason recorded')[:90]}"})
        for p in conn.execute("SELECT id, name FROM projects WHERE status='active' AND health='blocked'"):
            items.append({"kind": "project_blocked", "id": p["id"], "severity": "high",
                          "title": f"Project blocked — {p['name']}"})
        for d in conn.execute(
                "SELECT id, name, due_date FROM projects WHERE status='active' AND due_date!='' "
                "AND due_date <= date('now','+3 day') ORDER BY due_date"):
            items.append({"kind": "deadline", "id": d["id"], "severity": "medium",
                          "title": f"Due {d['due_date']} — {d['name']}"})
    gsc_state = _gsc_integration_state()
    if gsc_state.get("state") == "error":
        items.append({"kind": "integration", "id": "gsc", "severity": "medium",
                      "title": f"Search Console error — {gsc_state.get('error', '')[:80]}"})
    order = {"high": 0, "medium": 1}
    items.sort(key=lambda x: order.get(x["severity"], 2))
    return items


# ---------- agent run lifecycle (hooked from server.py bridge) ----------

def bridge_run_started(target: str, message: str) -> str:
    """Record a run when the bridge sends real work to an agent. Status checks
    are not recorded (the caller filters them). '@all' fan-outs get a single
    unattributed run rather than 10 fake per-agent ones."""
    init_db()
    agent_id: str | None = target.lstrip("@").lower()
    run_id = str(uuid.uuid4())
    with _conn() as conn:
        if agent_id == "all" or not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            agent_id = None
        conn.execute(
            "INSERT INTO agent_runs (id, agent_id, state, summary, started_at) VALUES (?,?,?,?,?)",
            (run_id, agent_id, "running", f"{target}: {message[:180]}", now_iso()))
        if agent_id:
            conn.execute("UPDATE agents SET last_run_at=? WHERE id=?", (now_iso(), agent_id))
    return run_id


def bridge_run_finished(target: str, status: str) -> None:
    """Close the newest open run for this target. No open run → no-op (e.g.
    status checks, or terminal handlers firing twice)."""
    init_db()
    agent_id: str | None = target.lstrip("@").lower()
    state = "completed" if status == "completed" else "failed"
    with _conn() as conn:
        if agent_id == "all" or not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            agent_id = None
        row = conn.execute(
            "SELECT id FROM agent_runs WHERE finished_at='' AND (agent_id=? OR (?1 IS NULL AND agent_id IS NULL)) "
            "ORDER BY started_at DESC LIMIT 1", (agent_id,)).fetchone()
        if not row:
            return
        conn.execute("UPDATE agent_runs SET state=?, finished_at=?, error=? WHERE id=?",
                     (state, now_iso(), "" if state == "completed" else "bridge reported failure", row["id"]))


def runs_list(limit: int = 30) -> list[dict[str, Any]]:
    with _conn() as conn:
        return _rows(conn.execute(
            "SELECT r.*, a.name AS agent_name FROM agent_runs r LEFT JOIN agents a ON a.id=r.agent_id "
            "ORDER BY r.started_at DESC LIMIT ?", (max(1, min(limit, 200)),)))


# ---------- Google Search Console ----------

def gsc_map_client(client_id: str, site_url: str) -> dict[str, Any]:
    if not client_id or not site_url:
        raise ValueError("client_id and site_url are required")
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone():
            raise KeyError("client not found")
        conn.execute(
            "INSERT INTO gsc_properties (client_id, site_url, mapped_at) VALUES (?,?,?) "
            "ON CONFLICT(client_id) DO UPDATE SET site_url=excluded.site_url, mapped_at=excluded.mapped_at",
            (client_id, site_url, now_iso()))
        log_activity(conn, "map_gsc", "client", client_id, {"site_url": site_url})
    return {"client_id": client_id, "site_url": site_url}


def _site_domain(url: str) -> str:
    """'https://www.Site.com/x', 'sc-domain:site.com' and 'site.com:443' all give 'site.com'."""
    text = (url or "").strip().lower().removeprefix("sc-domain:")
    host = re.sub(r"^[a-z]+://", "", text).split("/")[0].split(":")[0]
    return host.removeprefix("www.")


def gsc_auto_map(sites: list[str] | None = None) -> dict[str, Any]:
    """Map every unmapped client to the Search Console property for one of its
    websites, by domain. A domain property (sc-domain:) wins over a URL-prefix
    one. Clients with no matching property are listed, never guessed."""
    if sites is None:
        sites = [s.get("site_url", "") for s in gsc.list_sites()]
    by_domain: dict[str, str] = {}
    for site in sorted(sites, key=lambda s: not s.startswith("sc-domain:")):
        by_domain.setdefault(_site_domain(site), site)
    mapped, unmatched = [], []
    with _conn() as conn:
        done = {r["client_id"] for r in conn.execute("SELECT client_id FROM gsc_properties")}
        clients = _rows(conn.execute("SELECT id, name FROM clients"))
        urls = _rows(conn.execute("SELECT client_id, url FROM projects WHERE url != '' AND client_id IS NOT NULL"))
    for c in clients:
        if c["id"] in done:
            continue
        site = next((by_domain[_site_domain(u["url"])] for u in urls
                     if u["client_id"] == c["id"] and _site_domain(u["url"]) in by_domain), "")
        if site:
            gsc_map_client(c["id"], site)
            mapped.append({"client": c["name"], "site_url": site})
        else:
            unmatched.append(c["name"])
    return {"mapped": mapped, "unmatched": unmatched}


def gsc_mappings() -> list[dict[str, Any]]:
    with _conn() as conn:
        return _rows(conn.execute(
            "SELECT g.*, c.name AS client_name FROM gsc_properties g JOIN clients c ON c.id=g.client_id"))


def gsc_sync_client(client_id: str, import_limit: int = 25) -> dict[str, Any]:
    """Pull last-28d query data for the client's mapped property. Updates
    tracked keywords that match a GSC query (provenance→verified) and imports
    up to `import_limit` new top queries. Never invents data: keywords with no
    GSC row are left untouched."""
    with _conn() as conn:
        row = conn.execute("SELECT site_url FROM gsc_properties WHERE client_id=?", (client_id,)).fetchone()
    if not row:
        raise ValueError("client has no GSC property mapped")
    site_url = row["site_url"]
    rows = gsc.top_queries(site_url)  # raises GSCError if not connected
    by_query = {r["keyword"].lower(): r for r in rows}
    updated = imported = 0
    ts = now_iso()
    with _conn() as conn:
        existing = _rows(conn.execute("SELECT id, keyword, position FROM seo_keywords WHERE client_id=?", (client_id,)))
        existing_lower = set()
        for kw in existing:
            existing_lower.add(kw["keyword"].lower())
            hit = by_query.get(kw["keyword"].lower())
            if not hit:
                continue
            conn.execute(
                "UPDATE seo_keywords SET prev_position=position, position=?, clicks=?, impressions=?, ctr=?, "
                "provenance='verified', source='gsc', updated_at=? WHERE id=?",
                (round(hit["position"]), hit["clicks"], hit["impressions"], hit["ctr"], ts, kw["id"]))
            updated += 1
        for r in rows:
            if imported >= import_limit:
                break
            if r["keyword"].lower() in existing_lower or r["impressions"] < 10:
                continue
            conn.execute(
                "INSERT INTO seo_keywords (id, client_id, keyword, position, clicks, impressions, ctr, "
                "source, provenance, tracked_since, updated_at) VALUES (?,?,?,?,?,?,?,'gsc','verified',?,?)",
                (str(uuid.uuid4()), client_id, r["keyword"], round(r["position"]), r["clicks"],
                 r["impressions"], r["ctr"], ts, ts))
            imported += 1
        note = f"updated {updated}, imported {imported} of {len(rows)} GSC queries"
        conn.execute("UPDATE gsc_properties SET last_sync_at=?, last_sync_note=? WHERE client_id=?",
                     (ts, note, client_id))
        log_activity(conn, "gsc_sync", "client", client_id, {"note": note, "site_url": site_url})
    return {"client_id": client_id, "site_url": site_url, "updated": updated, "imported": imported,
            "gsc_rows": len(rows), "note": note}


def activity_list(limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as conn:
        return _rows(conn.execute("SELECT * FROM activity_log ORDER BY ts DESC LIMIT ?", (max(1, min(limit, 500)),)))


def _billing_connected() -> bool:
    """True only when a billing source can supply revenue figures."""
    return False  # no billing integration is wired yet; Stripe would set this


def _model_integration_state(name: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Offline check only — a live probe would cost an API call per page load."""
    try:
        if name == "jev":
            import jev
            engines = jev.engines()
            return {"state": "configured" if engines else "not_connected",
                    "engines": [jev.LABELS[e] for e in engines]}
        import providers
        st = (status if status is not None else providers.status()).get(name) or {}
        if st.get("connected"):
            return {"state": "configured", "model": st.get("model", "")}
        return {"state": "not_connected", "error": st.get("error", "")}
    except Exception as exc:
        return {"state": "error", "error": str(exc)}


def _brain_integration_state(status: dict[str, Any]) -> dict[str, Any]:
    """The whole dashboard thinks through one chain; this is its health.

    error        nothing in the chain can answer: every thinking task fails
    degraded     the first choice is down and a fallback is carrying the load
    configured   the first choice is answering
    """
    b = status.get("_brain") or {}
    order, live = b.get("order") or [], b.get("live") or []
    fails = b.get("failing") or []
    if not live:
        return {"state": "error", "order": order, "live": [], "failing": [],
                "error": "No thinking engine is connected. Pipelines, Jarvis and Ask the agency cannot run."}
    active = b.get("active") or live[0]
    if len(fails) == len(live):
        return {"state": "error", "order": order, "live": live, "failing": fails, "active": active,
                "error": "Every connected engine failed on its last call: "
                         + "; ".join(f"{p}: {(status.get(p) or {}).get('failing', '')[:120]}" for p in fails)}
    state = "configured" if order and active == order[0] else "degraded"
    out = {"state": state, "order": order, "live": live, "failing": fails, "active": active,
           "model": (status.get(active) or {}).get("model", "")}
    if fails:
        out["reason"] = f"{fails[0]}: {(status.get(fails[0]) or {}).get('failing', '')[:160]}"
    return out


def _provider_status_safe() -> dict[str, Any]:
    try:
        import providers
        return providers.status()
    except Exception as exc:
        return {"_error": str(exc)}


def _dfs_integration_state() -> dict[str, Any]:
    """Cheap, offline check — status() would cost an API round trip."""
    try:
        import dataforseo
        creds = dataforseo.credentials()
    except Exception as exc:
        return {"state": "error", "error": str(exc)}
    return {"state": "configured" if creds.get("login") else "not_connected"}


_openseo_state_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _openseo_integration_state() -> dict[str, Any]:
    """Installed / running, from a local health probe, cached briefly for the overview."""
    c = _openseo_state_cache
    if c["value"] is not None and time.time() - c["at"] < 10:
        return c["value"]
    try:
        import openseo
        st = openseo.status()
        state = ("running" if st["running"] else "error" if st["phase"] == "failed"
                 else "configured" if st["installed"] else "not_connected")
        value = {"state": state, "url": st["url"] if st["running"] else "", "dataforseo": st["dataforseo"],
                 **({"error": st["error"]} if st["error"] else {})}
    except Exception as exc:
        value = {"state": "error", "error": str(exc)[:300]}
    c.update(at=time.time(), value=value)
    return value


_gsc_state_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _gsc_integration_state() -> dict[str, Any]:
    # status() can hit Google's token endpoint; cache 5 min so the overview
    # endpoint stays fast and offline-safe.
    import time as _time
    if _gsc_state_cache["value"] is not None and _time.time() - _gsc_state_cache["at"] < 300:
        return _gsc_state_cache["value"]
    value = _gsc_integration_state_uncached()
    _gsc_state_cache.update(at=_time.time(), value=value)
    return value


def _gsc_integration_state_uncached() -> dict[str, Any]:
    try:
        st = gsc.status()
    except Exception as exc:
        return {"state": "error", "error": str(exc)}
    if st["connected"]:
        return {"state": "connected"}
    if st["error"]:
        return {"state": "error", "error": st["error"]}
    return {"state": "configured" if st["configured"] else "not_connected"}


def overview() -> dict[str, Any]:
    with _conn() as conn:
        counts = {
            "clients": conn.execute("SELECT COUNT(*) AS n FROM clients WHERE status='active'").fetchone()["n"],
            "projects": conn.execute("SELECT COUNT(*) AS n FROM projects WHERE status='active'").fetchone()["n"],
            "approvals_pending": conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'").fetchone()["n"],
            "approvals_blocking": conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE status='pending' AND blocking=1").fetchone()["n"],
            "tasks_open": conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status!='completed'").fetchone()["n"],
            "keywords": conn.execute("SELECT COUNT(*) AS n FROM seo_keywords").fetchone()["n"],
        }
        mrr = conn.execute("SELECT COALESCE(SUM(mrr_cents),0) AS n FROM clients WHERE status='active'").fetchone()["n"]
        deadlines = _rows(conn.execute(
            "SELECT id, name AS title, due_date, 'project' AS kind FROM projects WHERE status='active' AND due_date!='' "
            "UNION ALL SELECT id, title, due_date, 'task' AS kind FROM tasks WHERE status!='completed' AND due_date!='' "
            "ORDER BY due_date ASC LIMIT 8"))
    pstatus = _provider_status_safe()
    return {
        "generated_at": now_iso(),
        "counts": counts,
        "mrr_cents": mrr,
        # MRR is typed in by hand on the client record. "verified" in this
        # system means a connected source measured it, so this stays "declared"
        # until a billing integration (Stripe) actually supplies the figure.
        # It previously read "verified" for any non-zero value, which put the
        # strongest provenance label on an operator's own estimate.
        "mrr_provenance": "verified" if _billing_connected() else "declared",
        "clients": clients_list(),
        "projects": projects_list(),
        "approvals": approvals_list("pending"),
        "agents": agents_list(),
        "deadlines": deadlines,
        "activity": activity_list(20),
        "attention": attention(),
        "integrations": {
            "hermes_gateway": {"state": "configured"},
            "gsc": _gsc_integration_state(),
            "dataforseo": _dfs_integration_state(),
            "brain": _brain_integration_state(pstatus),
            "claude": _model_integration_state("claude", pstatus),
            "chatgpt": _model_integration_state("chatgpt", pstatus),
            "ollama": _model_integration_state("ollama", pstatus),
            "jev": _model_integration_state("jev"),
            "ga4": {"state": "not_connected"},
            "openseo": _openseo_integration_state(),
            "stripe": {"state": "not_connected"},
        },
    }


# ---------- client report ----------
# The report is the one artefact a client reads, so it is held to a stricter
# rule than the dashboard: every status is derived from what actually happened
# (runs, stages, due dates), never from a hand-set health flag, and nothing
# that failed quality checks (e.g. a provider error stored as output) is ever
# listed as delivered.

AREA_LABELS = {
    "technical": "Technical", "aeo": "AI answers", "schema": "Structured data",
    "accessibility": "Accessibility", "geo": "AI citations", "performance": "Speed", "on-page": "On-page",
}
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]


def _nice_date(value: str) -> str:
    """'2026-08-14' or an ISO timestamp -> '14 Aug 2026'. Empty stays empty."""
    if not value:
        return ""
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")) if "T" in value else datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return value
    return f"{d.day} {d.strftime('%b %Y')}"


def _client_title(title: str) -> str:
    """Stage titles carry operator detail like '(real fetch)'; clients don't need it."""
    return re.sub(r"\s*\([^)]*\)", "", title or "").strip()


def _project_state(p: dict[str, Any], run: dict[str, Any] | None, stages: list[dict[str, Any]],
                   today) -> dict[str, Any]:
    """What a client should be told about one project, from recorded facts only."""
    total = (run or {}).get("total") or p.get("stage_total") or 0
    if run:
        done = run.get("done", 0)
    else:
        # projects.stage is a 1-based "current stage" until a run completes it
        done = total if total and (p.get("stage") or 0) >= total else max(0, (p.get("stage") or 1) - 1)
    rstate = (run or {}).get("state", "")
    delivered = rstate == "completed" or (not run and total and done >= total)
    current = next((s for s in stages if s["state"] in ("failed", "running", "awaiting_approval")), None) \
        or next((s for s in stages if s["state"] == "pending"), None)
    step = _client_title(current["title"]) if current else ""
    progress = f"{done} of {total} steps" if total else ""

    overdue_days = 0
    if p.get("due_date") and not delivered:
        try:
            overdue_days = (today - datetime.strptime(p["due_date"][:10], "%Y-%m-%d").date()).days
        except ValueError:
            overdue_days = 0

    if delivered:
        state, label = "delivered", "Delivered"
        detail = f"Finished {_nice_date((run or {}).get('updated_at', ''))}".strip() if run else "Complete"
    elif overdue_days > 0:
        state, label = "overdue", f"{overdue_days} day{'s' if overdue_days != 1 else ''} overdue"
        detail = f"Due {_nice_date(p['due_date'])} · " + ("not yet started" if done == 0 else f"{progress} done")
    elif rstate in ("failed", "stopped"):
        state, label = "paused", "Paused"
        detail = (f"Paused on our side at {step.lower()}. " if step else "Paused on our side. ") + "Nothing needed from you."
    elif rstate == "cancelled":
        state, label, detail = "stopped", "Stopped", f"Stopped after {done} step{'s' if done != 1 else ''}"
    elif rstate == "awaiting_approval":
        state, label, detail = "review", "Ready for review", f"Waiting for sign-off: {step.lower()}" if step else "Waiting for sign-off"
    elif rstate in ("running", "queued"):
        state, label, detail = "in_progress", "In progress", f"Now working on {step.lower()}" if step else "Work under way"
    elif p.get("health") == "blocked":
        state, label, detail = "blocked", "Blocked", "Waiting on a decision"
    elif done == 0:
        state, label = "not_started", "Not started"
        detail = f"Due {_nice_date(p['due_date'])}" if p.get("due_date") else "Scheduled"
    else:
        state, label, detail = "in_progress", "In progress", progress
    return {"state": state, "label": label, "detail": detail, "done": done, "total": total,
            "overdue_days": overdue_days, "url": p.get("url") or ""}


def _report_facts(conn: sqlite3.Connection, projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Runs, verified audits and clean deliverables for a client's projects."""
    import llm
    runs: dict[str, dict[str, Any]] = {}
    stages: dict[str, list[dict[str, Any]]] = {}
    audits: dict[str, dict[str, Any]] = {}
    deliverables: dict[str, dict[str, Any]] = {}
    for p in projects:
        run = conn.execute("SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                           (p["id"],)).fetchone()
        if run:
            run = dict(run)
            rows = _rows(conn.execute("SELECT stage_id, title, state FROM stage_runs WHERE run_id=? ORDER BY position",
                                      (run["id"],)))
            run["total"] = len(rows)
            run["done"] = sum(1 for s in rows if s["state"] == "done")
            runs[p["id"]], stages[p["id"]] = run, rows
        for art in _rows(conn.execute(
                "SELECT stage_id, title, kind, content, created_at FROM artifacts WHERE project_id=? "
                "ORDER BY created_at", (p["id"],))):
            if llm._provider_error(art["content"] or ""):
                continue  # a refused AI call is not a deliverable
            if art["stage_id"] in ("audit", "self_audit") and art["kind"] == "json":
                try:
                    data = json.loads(art["content"])
                except (json.JSONDecodeError, TypeError):
                    continue
                site = data.get("final_url") or data.get("url") or p.get("url") or p["name"]
                # prefer the live-site audit over a self-audit of a built page, then the newest
                prev = audits.get(site)
                rank = (art["stage_id"] == "audit", art["created_at"])
                if not prev or rank >= prev["_rank"]:
                    audits[site] = {**data, "_rank": rank, "_at": art["created_at"]}
            key = art["stage_id"]
            deliverables[key] = {"title": _client_title(art["title"]), "at": art["created_at"]}
    sites = []
    for site, a in audits.items():
        findings = a.get("findings") or []
        areas: dict[str, dict[str, int]] = {}
        for f in findings:
            b = areas.setdefault(f.get("area", "other"), {"good": 0, "total": 0})
            b["total"] += 1
            b["good"] += f.get("severity") == "good"
        issues = sorted((f for f in findings if f.get("severity") != "good"),
                        key=lambda f: _SEVERITY_RANK.get(f.get("severity"), 3))
        sites.append({
            "site": site, "score": a.get("score"), "audited_at": a["_at"], "provenance": "verified",
            "areas": [{"area": k, "label": AREA_LABELS.get(k, k.title()), **v}
                      for k, v in sorted(areas.items(), key=lambda kv: -(kv[1]["good"] / max(kv[1]["total"], 1)))],
            "issues": [{"severity": f.get("severity"), "area": f.get("area"), "title": f.get("title", ""),
                        "fix": f.get("fix", "")} for f in issues],
            "counts": {s: sum(1 for f in issues if f.get("severity") == s) for s in ("high", "medium", "low")},
        })
    sites.sort(key=lambda s: s["audited_at"], reverse=True)
    return {"runs": runs, "stages": stages, "sites": sites,
            "deliverables": sorted(deliverables.values(), key=lambda d: d["at"])}


def client_report(client_id: str) -> dict[str, Any]:
    """Client-ready report. Statuses come from runs and dates, figures from
    verified audits; sections with no source say so instead of guessing."""
    with _conn() as conn:
        client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            raise KeyError("client not found")
        client = dict(client)
        projects = _rows(conn.execute("SELECT * FROM projects WHERE client_id=? AND status!='archived' "
                                      "ORDER BY created_at", (client_id,)))
        keywords = _rows(conn.execute("SELECT * FROM seo_keywords WHERE client_id=? ORDER BY "
                                      "CASE WHEN position IS NULL THEN 999 ELSE position END", (client_id,)))
        backlinks = _rows(conn.execute("SELECT status, COUNT(*) AS n FROM backlink_prospects "
                                       "WHERE client_id=? GROUP BY status", (client_id,)))
        competitors = _rows(conn.execute("SELECT domain, name FROM competitors WHERE client_id=?", (client_id,)))
        gsc_map = conn.execute("SELECT * FROM gsc_properties WHERE client_id=?", (client_id,)).fetchone()
        facts = _report_facts(conn, projects)

    today = datetime.now(timezone.utc).date()
    shaped = []
    for p in projects:
        st = _project_state(p, facts["runs"].get(p["id"]), facts["stages"].get(p["id"], []), today)
        shaped.append({"name": p["name"], "status": p["status"], "health": p["health"], "stage": p["stage"],
                       "stage_total": p["stage_total"], "due_date": p["due_date"], **st})
    order = {"overdue": 0, "blocked": 1, "paused": 2, "review": 3, "in_progress": 4, "not_started": 5,
             "delivered": 6, "stopped": 7}
    shaped.sort(key=lambda s: order.get(s["state"], 9))

    movers = [k for k in keywords if k.get("prev_position") is not None and k.get("position") is not None
              and k["prev_position"] != k["position"]]
    gains = sorted((k for k in movers if k["prev_position"] > k["position"]), key=lambda k: k["position"])[:10]
    losses = sorted((k for k in movers if k["prev_position"] < k["position"]),
                    key=lambda k: k["prev_position"] - k["position"])[:10]

    gsc_state = _gsc_integration_state().get("state", "not_connected")
    gsc_perf: dict[str, Any] | None = None
    if gsc_map:
        try:
            gsc_perf = gsc.performance(gsc_map["site_url"])
            gsc_note = ""
        except Exception as exc:
            gsc_note = f"Search Console fetch failed: {exc}"
    elif gsc_state == "connected":
        gsc_note = ("Search Console is connected, but this client's site is not linked to it yet, "
                    "so no traffic figures are shown.")
    else:
        gsc_note = "Google Search Console is not connected — no traffic data is shown rather than estimates."

    link_counts = {row["status"]: row["n"] for row in backlinks}
    approved_links = sum(link_counts.get(s, 0) for s in ("approved", "sent", "responded", "linked"))
    site = facts["sites"][0] if facts["sites"] else None
    need_push = [s for s in shaped if s["state"] in ("overdue", "paused", "blocked")]

    return {
        "generated_at": now_iso(),
        "period_days": 28,
        "period": {"start": _nice_date((datetime.now(timezone.utc) - timedelta(days=28)).isoformat()),
                   "end": _nice_date(now_iso())},
        "client": {"id": client["id"], "name": client["name"], "company": client["company"]},
        "summary": _report_summary(site, approved_links, need_push),
        "projects": shaped,
        "site_health": facts["sites"],
        "deliverables": facts["deliverables"],
        "next_steps": _report_next_steps(shaped, site, approved_links, bool(gsc_map), gsc_state),
        "seo": {
            "keywords_tracked": len(keywords),
            "keywords_verified": len([k for k in keywords if k["provenance"] == "verified"]),
            "top_gains": [{"keyword": k["keyword"], "from": k["prev_position"], "to": k["position"],
                           "provenance": k["provenance"]} for k in gains],
            "top_losses": [{"keyword": k["keyword"], "from": k["prev_position"], "to": k["position"],
                            "provenance": k["provenance"]} for k in losses],
            "gsc_performance": gsc_perf,
            "gsc_state": "linked" if gsc_map else gsc_state,
            "gsc_note": gsc_note,
        },
        "backlinks": link_counts,
        "approved_links": approved_links,
        "competitors": competitors,
        "data_policy": "Verified figures come from fetching the live site or from Google Search Console. "
                       "Nothing in this report is estimated; where there is no source, the section says so.",
    }


def _domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/")


def _report_summary(site: dict[str, Any] | None, approved_links: int,
                    need_push: list[dict[str, Any]]) -> dict[str, str]:
    """Headline and one paragraph, built only from facts in the report.
    Returned as plain text plus an HTML variant with the figures in bold."""
    from html import escape as h
    if site and site.get("score") is not None:
        s = site["score"]
        line1 = "Your site is healthy." if s >= 80 else "Your site needs some work." if s >= 55 else "Your site needs attention."
    else:
        line1 = "Here is where things stand."
    n = len(need_push)
    line2 = "Everything is on track." if n == 0 else \
        f"{_NUMBER_WORDS[n] if n < 10 else n} project{'s' if n != 1 else ''} need{'s' if n == 1 else ''} a push."
    parts = []
    if site and site.get("score") is not None:
        high = site["counts"]["high"]
        parts.append(f"{h(_domain(site['site']))} scores <b>{site['score']} out of 100</b> in our verified audit"
                     + (", with no high-risk issues." if high == 0 else f", with <b>{high} high-risk issue{'s' if high != 1 else ''}</b> to fix first."))
    if approved_links:
        parts.append(f"We approved <b>{approved_links} link prospect{'s' if approved_links != 1 else ''}</b> for outreach.")
    for p in need_push[:3]:
        if p["state"] == "overdue":
            parts.append(f"{h(p['name'])} is <b>{p['overdue_days']} day{'s' if p['overdue_days'] != 1 else ''} past its due date</b>.")
        elif p["state"] == "paused":
            parts.append(f"{h(p['name'])} is paused on our side.")
        else:
            parts.append(f"{h(p['name'])} is blocked.")
    if need_push:
        parts.append("Both are covered in the plan below." if n == 2 else "The plan below covers it." if n == 1 else "The plan below covers each one.")
    html = " ".join(parts) or "No work has been recorded for this client yet."
    return {"headline": line1, "subline": line2, "html": html, "text": re.sub(r"<[^>]+>", "", html)}


def _report_next_steps(projects: list[dict[str, Any]], site: dict[str, Any] | None, approved_links: int,
                       gsc_linked: bool, gsc_state: str) -> list[str]:
    """Proposed actions that follow directly from the report's own findings."""
    steps = []
    for p in projects:
        if p["state"] == "overdue":
            steps.append(f"Agree a new date for {p['name']} and "
                         + ("start the work this week." if p["done"] == 0 else "finish the remaining steps."))
    if site and site["issues"]:
        k = len(site["issues"])
        steps.append(f"Ship the {k} fix{'es' if k != 1 else ''} listed above and re-audit the site to confirm.")
    if approved_links:
        steps.append(f"Begin outreach to the {approved_links} approved link prospect{'s' if approved_links != 1 else ''}.")
    for p in projects:
        if p["state"] == "paused":
            steps.append(f"Resume {p['name']}.")
        elif p["state"] == "review":
            steps.append(f"Review and sign off {p['name']}.")
    if not gsc_linked:
        steps.append("Link the site in Google Search Console so clicks and rankings appear in the next report."
                     if gsc_state == "connected" else
                     "Connect Google Search Console so clicks and rankings appear in the next report.")
    return steps[:6]


BRAND_MARK = APP_DIR / "q" / "brand" / "gem-mark.svg"


def _brand_mark_svg() -> str:
    """The Gem Agency mark, inlined so a saved or printed report keeps it.
    A missing file drops the mark rather than breaking the report."""
    try:
        return BRAND_MARK.read_text(encoding="utf-8")
    except OSError:
        return ""


_REPORT_CSS = """
:root{--paper:#FBFAF7;--ink:#17151F;--ink2:#5B5868;--ink3:#8B8798;--rule:#E7E3EC;--ac:#6E5CF6;--ac-w:#EFEBFF;
  --ok:#17915A;--ok-w:#E3F4EA;--warn:#B7791F;--warn-w:#FBF0DA;--crit:#C8374E;--crit-w:#FBE6EA;--mute:#EEEDF1;
  --serif:"Instrument Serif","Iowan Old Style","Palatino Linotype",Georgia,serif;
  --sans:Inter,"Segoe UI",system-ui,-apple-system,sans-serif;--mono:"JetBrains Mono",Consolas,monospace}
*{margin:0;padding:0;box-sizing:border-box}
body{background:#E9E7E2;color:var(--ink);font:14px/1.55 var(--sans);-webkit-font-smoothing:antialiased;padding:40px 16px 60px}
.doc{max-width:880px;margin:0 auto;background:var(--paper);box-shadow:0 30px 80px -40px rgba(20,16,40,.35);padding:60px 68px 52px}
.top{display:flex;align-items:center;gap:12px;padding-bottom:22px;border-bottom:1px solid var(--rule)}
.top svg{width:30px;height:30px}
.top b{font:400 22px/1 var(--serif)}
.top .r{margin-left:auto;text-align:right;font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);line-height:1.7}
.hero{padding:40px 0 32px}
.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--ac);font-weight:600}
h1{font:400 58px/1.02 var(--serif);letter-spacing:-.015em;margin:12px 0 18px}
h1 i{color:var(--ink3)}
.summary{font-size:16.5px;line-height:1.6;color:var(--ink2);max-width:660px}
.summary b{color:var(--ink);font-weight:600}
.figs{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--ink);border-bottom:1px solid var(--rule)}
.fig{padding:20px 16px 18px;border-right:1px solid var(--rule)}
.fig:first-child{padding-left:0}.fig:last-child{border-right:0}
.fig .k{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3)}
.fig .n{font:400 50px/1 var(--serif);margin:12px 0 6px}
.fig .n small{font-size:19px;color:var(--ink3)}
.fig .c{font-size:12px;color:var(--ink2)}
.chip{display:inline-block;font:600 9.5px/1.6 var(--sans);letter-spacing:.08em;text-transform:uppercase;padding:0 7px;border-radius:20px;vertical-align:1px}
.chip.verified{background:var(--ok-w);color:var(--ok)}
h2{font:400 29px/1.2 var(--serif);margin:46px 0 4px;display:flex;align-items:baseline;gap:12px;break-after:avoid}
h2 span{font:500 10.5px var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--ink3)}
.lede{color:var(--ink2);font-size:13.5px;margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font:600 10.5px var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);padding:10px 0;border-bottom:1px solid var(--ink)}
td{padding:14px 0;border-bottom:1px solid var(--rule);vertical-align:top}
tr{break-inside:avoid}
td .s{display:block;font-size:12.5px;color:var(--ink3);margin-top:3px}
td.pr{width:28%;font-size:13.5px}
.st{display:inline-flex;align-items:center;gap:6px;font:600 11.5px var(--sans);padding:3px 10px;border-radius:20px;white-space:nowrap}
.st::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.st.delivered{background:var(--ok-w);color:var(--ok)}
.st.overdue,.st.blocked{background:var(--crit-w);color:var(--crit)}
.st.paused,.st.review{background:var(--warn-w);color:var(--warn)}
.st.in_progress{background:var(--ac-w);color:var(--ac)}
.st.stopped,.st.not_started{background:var(--mute);color:var(--ink2)}
.bar{height:6px;border-radius:3px;background:#ECEAF0;width:150px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;border-radius:3px;background:var(--ac)}
.bar i.delivered{background:var(--ok)}.bar i.stopped{background:var(--ink3)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:40px}
.area{display:grid;grid-template-columns:112px 1fr 44px;align-items:center;gap:14px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:13.5px}
.area .t{height:6px;background:#ECEAF0;border-radius:3px;overflow:hidden}
.area .t i{display:block;height:100%;border-radius:3px}
.area .v{text-align:right;font:12px var(--mono);color:var(--ink2)}
.fix{display:grid;grid-template-columns:66px 1fr;gap:12px;padding:12px 0;border-bottom:1px solid var(--rule);break-inside:avoid}
.fix b{font-weight:600;font-size:13.5px;display:block}
.fix p{font-size:12.5px;color:var(--ink2);margin-top:2px}
.sev{font:600 10px/1.9 var(--sans);letter-spacing:.06em;text-transform:uppercase}
.sev.high{color:var(--crit)}.sev.medium{color:var(--warn)}.sev.low{color:var(--ink3)}
ul.del{list-style:none;columns:2;column-gap:40px}
ul.del li{padding:9px 0;border-bottom:1px solid var(--rule);font-size:13.5px;break-inside:avoid;display:flex;gap:10px}
ul.del li::before{content:"\\2713";color:var(--ok);font-weight:700}
.callout{margin-top:12px;padding:18px 20px;border-radius:12px;background:var(--ac-w)}
.callout b{display:block;font-size:14.5px}
.callout p{font-size:13px;color:var(--ink2);margin-top:2px}
.traffic{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--ink)}
.traffic > div{padding:16px 16px 14px 0;font-size:12.5px}
.traffic .k{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3)}
.traffic .n{font:400 40px/1 var(--serif);margin:10px 0 4px}
.up{color:var(--ok)}.dn{color:var(--crit)}
ol.next{list-style:none;counter-reset:n;margin-top:4px}
ol.next li{display:grid;grid-template-columns:38px 1fr;padding:12px 0;border-bottom:1px solid var(--rule);font-size:14px;break-inside:avoid}
ol.next li::before{counter-increment:n;content:counter(n,decimal-leading-zero);font:400 22px/1 var(--serif);color:var(--ac)}
.muted{color:var(--ink3);font-size:13.5px}
.foot{margin-top:46px;padding-top:16px;border-top:1px solid var(--ink);display:flex;gap:30px;font-size:11.5px;color:var(--ink3);line-height:1.6}
.foot b{color:var(--ink2)}
@media screen and (max-width:760px){.doc{padding:34px 22px}h1{font-size:40px}.figs{grid-template-columns:repeat(2,1fr)}
  .fig:nth-child(2){border-right:0}.fig:nth-child(3){padding-left:0}.two{grid-template-columns:1fr;gap:0}ul.del{columns:1}
  .pr{display:none}}
@page{size:A4;margin:14mm}
@media print{body{background:#fff;padding:0}.doc{box-shadow:none;max-width:none;padding:0;background:#fff}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}h2{margin-top:32px}
  h1{font-size:50px}.fig .n{font-size:42px}.two{gap:28px}.area{grid-template-columns:96px 1fr 40px}}
"""


def _report_html(rep: dict[str, Any]) -> str:
    from html import escape as h

    name = h(rep["client"]["name"])
    summary = rep.get("summary") or {"headline": "Client report", "subline": "", "html": ""}
    site = (rep.get("site_health") or [None])[0]
    seo = rep["seo"]

    # --- figures: only numbers the report can stand behind ---
    figs = []
    if site and site.get("score") is not None:
        c = site["counts"]
        figs.append(("Site health", f"{site['score']}<small>/100</small>",
                     f"<span class='chip verified'>Verified</span> {h(_nice_date(site['audited_at']))}"))
        figs.append(("Open issues", str(sum(c.values())), f"{c['high']} high · {c['medium']} medium · {c['low']} low"))
    else:
        figs.append(("Site health", "&mdash;", "No audit run yet"))
    figs.append(("Link prospects", str(rep.get("approved_links", 0)), "approved for outreach"))
    figs.append(("Deliverables", str(len(rep.get("deliverables") or [])), "completed to date"))
    if seo["keywords_tracked"] and len(figs) < 4:
        figs.append(("Keywords tracked", str(seo["keywords_tracked"]), f"{seo['keywords_verified']} verified"))
    figs_html = "".join(f"<div class='fig'><div class='k'>{h(k)}</div><div class='n'>{v}</div><div class='c'>{c}</div></div>"
                        for k, v, c in figs[:4])

    # --- projects ---
    def project_row(p):
        pct = round(100 * p["done"] / p["total"]) if p["total"] else 0
        prog = (f"{p['done']} of {p['total']} steps<div class='bar'><i class='{h(p['state'])}' style='width:{pct}%'></i></div>"
                if p["total"] else "&mdash;")
        return (f"<tr><td><b>{h(p['name'])}</b><span class='s'>{h(p['detail'])}</span></td>"
                f"<td class='pr'>{prog}</td>"
                f"<td style='text-align:right'><span class='st {h(p['state'])}'>{h(p['label'])}</span></td></tr>")
    projects_html = ("<table><tr><th style='width:52%'>Project</th><th class='pr'>Progress</th>"
                     "<th style='text-align:right'>Status</th></tr>"
                     + "".join(project_row(p) for p in rep["projects"]) + "</table>") if rep["projects"] \
        else "<p class='muted'>No projects are active for this client.</p>"

    # --- site health + fixes ---
    health_html = ""
    if site:
        def area_row(a):
            share = a["good"] / a["total"] if a["total"] else 0
            ink = "var(--ok)" if share >= .8 else "var(--warn)" if share >= .5 else "var(--crit)"
            return (f"<div class='area'><span>{h(a['label'])}</span><span class='t'><i style='width:{round(share * 100)}%;"
                    f"background:{ink}'></i></span><span class='v'>{a['good']}/{a['total']}</span></div>")
        fixes = site["issues"][:6]
        fixes_html = "".join(
            f"<div class='fix'><span class='sev {h(f['severity'])}'>{h(f['severity'])}</span><div><b>{h(f['title'])}</b>"
            f"{'<p>' + h(f['fix']) + '</p>' if f['fix'] else ''}</div></div>" for f in fixes) \
            or "<p class='muted'>Nothing to fix: every check passed.</p>"
        health_html = f"""<div class="two">
  <div><h2>Search health <span>{h(_domain(site['site']))}</span></h2>
    <p class="lede">Share of checks passing on the live site.</p>{''.join(area_row(a) for a in site['areas'])}</div>
  <div><h2>What we fix next <span>{len(site['issues'])} issue{'s' if len(site['issues']) != 1 else ''}</span></h2>
    <p class="lede">Most important first.</p>{fixes_html}</div></div>"""

    deliverables = rep.get("deliverables") or []
    deliverables_html = (f"<h2>Delivered to date <span>{len(deliverables)} deliverable{'s' if len(deliverables) != 1 else ''}</span></h2>"
                         "<ul class='del'>" + "".join(f"<li>{h(d['title'])}</li>" for d in deliverables) + "</ul>") if deliverables else ""

    # --- search traffic ---
    perf = seo.get("gsc_performance")
    if perf:
        cur, prev = perf["current"], perf["previous"]
        def delta(c, p, lower_is_better=False):
            if not p:
                return "<span class='muted'>no prior period</span>"
            d = round(100 * (c - p) / p)
            good = d <= 0 if lower_is_better else d >= 0
            return f"<span class='{'up' if good else 'dn'}'>{'+' if d >= 0 else ''}{d}% vs previous 28 days</span>"
        traffic_html = f"""<h2>Search traffic <span>Last 28 days · Google</span></h2>
  <div class="traffic">
    <div><div class="k">Clicks <span class='chip verified'>Verified</span></div><div class="n">{cur['clicks']:,}</div>{delta(cur['clicks'], prev['clicks'])}</div>
    <div><div class="k">Impressions</div><div class="n">{cur['impressions']:,}</div>{delta(cur['impressions'], prev['impressions'])}</div>
    <div><div class="k">Average position</div><div class="n">{cur['position']}</div>{delta(cur['position'], prev['position'], True)}</div>
  </div>"""
    else:
        state = seo.get("gsc_state")
        title = "Clicks and rankings aren't in this report yet"
        body = (f"{h(seo['gsc_note'])} Adding our team as a user on the site's Search Console property is all it takes."
                if state == "connected" else h(seo["gsc_note"]))
        traffic_html = (f"<h2>Search traffic <span>Not linked yet</span></h2>"
                        f"<div class='callout'><b>{title}</b><p>{body}</p></div>")

    # --- keyword movement: only when there is movement to show ---
    kw_html = ""
    if seo["top_gains"] or seo["top_losses"]:
        rows = "".join(
            f"<tr><td><b>{h(str(k['keyword']))}</b></td><td class='pr'>#{k['from']} &rarr; #{k['to']}</td>"
            f"<td style='text-align:right' class='{'up' if k['from'] > k['to'] else 'dn'}'>"
            f"{'&#9650;' if k['from'] > k['to'] else '&#9660;'} {abs(k['from'] - k['to'])}</td></tr>"
            for k in (seo["top_gains"] + seo["top_losses"])[:12])
        kw_html = (f"<h2>Keyword movement <span>{seo['keywords_verified']} of {seo['keywords_tracked']} verified</span></h2>"
                   f"<table><tr><th>Keyword</th><th class='pr'>Position</th><th style='text-align:right'>Change</th></tr>{rows}</table>")

    steps = rep.get("next_steps") or []
    next_html = ("<h2>Next 30 days <span>Proposed</span></h2><ol class='next'>"
                 + "".join(f"<li>{h(s)}</li>" for s in steps) + "</ol>") if steps else ""

    period = rep.get("period") or {}
    generated = h(_nice_date(rep["generated_at"]))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} · Client report · Gem Agency</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono&display=swap">
<style>{_REPORT_CSS}</style></head><body>
<div class="doc">
  <div class="top">{_brand_mark_svg()}<b>Gem Agency</b>
    <div class="r">Client report<br>{h(period.get('start', ''))} &ndash; {h(period.get('end', ''))}</div></div>
  <div class="hero">
    <div class="eyebrow">Prepared for {name}</div>
    <h1>{h(summary['headline'])}<br><i>{h(summary['subline'])}</i></h1>
    <p class="summary">{summary['html']}</p>
  </div>
  <div class="figs">{figs_html}</div>
  <h2>Your projects <span>Status as of {generated}</span></h2>
  {projects_html}
  {health_html}
  {deliverables_html}
  {traffic_html}
  {kw_html}
  {next_html}
  <div class="foot"><div><b>How to read the numbers.</b> <span class="chip verified">Verified</span> {h(rep['data_policy'])}</div>
    <div style="white-space:nowrap">Gem Agency &middot; {generated}</div></div>
</div>
</body></html>"""


def health() -> dict[str, Any]:
    with _conn() as conn:
        version = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()["v"]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return {"ok": integrity == "ok", "db": str(AGENCY_DB.name), "schema_version": version, "integrity": integrity}


# ---------- HTTP integration (called from server.py) ----------

def _serve_static(handler, rel: str) -> bool:
    base = APP_DIR.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        handler.send_json({"ok": False, "error": "invalid path"}, 400)
        return True
    if not target.is_file():
        handler.send_json({"ok": False, "error": "not found"}, 404)
        return True
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    if target.suffix == ".js":
        mime = "text/javascript"
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or "javascript" in mime else mime)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def handle_get(handler, parsed) -> bool:
    """Route agency GETs. Returns True when the request was handled."""
    path = parsed.path
    init_db()

    # "/" serves the Quantum interface, the only UI. The retired /ledger and
    # /legacy editions were removed along with their HTML.
    if path == "/" and (APP_DIR / "q" / "index.html").is_file():
        return _serve_static(handler, "q/index.html")
    if path.startswith("/app/"):
        return _serve_static(handler, path[len("/app/"):])

    if not path.startswith("/api/agency/"):
        return False
    if not _host_ok(handler):
        handler.send_json({"ok": False, "error": "invalid host"}, 403)
        return True

    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    q = {k: v[0] for k, v in qs.items() if v}

    try:
        if path == "/api/agency/gsc/callback":
            code, state = q.get("code", ""), q.get("state", "")
            if not code or not gsc.consume_state(state):
                handler.send_text("Invalid or expired OAuth state. Close this tab and retry Connect.", 400,
                                  "text/html; charset=utf-8")
                return True
            host = (handler.headers.get("Host") or "127.0.0.1:51764")
            hostname, _, port = host.partition(":")
            gsc.exchange_code(code, hostname, int(port or "80"))
            _gsc_state_cache["value"] = None
            try:
                gsc_auto_map()              # link each client's site to its property right away
            except Exception:
                pass                        # mapping can be retried from Integrations; the connection stands
            handler.send_text("<h2>Google Search Console connected.</h2><p>You can close this tab "
                              "and return to Gem Agency.</p>", 200, "text/html; charset=utf-8")
            return True
        if path == "/api/agency/gsc/status":
            st = gsc.status()
            st["mappings"] = gsc_mappings()
            handler.send_json({"ok": True, **st})
            return True
        if path == "/api/agency/gsc/auth-url":
            host = (handler.headers.get("Host") or "127.0.0.1:51764")
            hostname, _, port = host.partition(":")
            handler.send_json({"ok": True, "url": gsc.auth_url(hostname, int(port or "80"))})
            return True
        if path == "/api/agency/gsc/sites":
            handler.send_json({"ok": True, "sites": gsc.list_sites()})
            return True
        if path == "/api/agency/gsc/performance":
            site = q.get("site", "")
            if not site:
                raise ValueError("site query param is required")
            handler.send_json({"ok": True, **gsc.performance(site, days=int(q.get("days", "28")),
                                                          include_series=q.get("series") == "1")})
            return True
        if path == "/api/agency/token":
            handler.send_json({"ok": True, "token": AGENCY_TOKEN})
        elif path == "/api/agency/overview":
            handler.send_json({"ok": True, **overview()})
        elif path == "/api/agency/health":
            handler.send_json(health())
        elif path == "/api/agency/clients":
            handler.send_json({"ok": True, "clients": clients_list()})
        elif path == "/api/agency/projects":
            handler.send_json({"ok": True, "projects": projects_list(q.get("client_id", ""))})
        elif path == "/api/agency/tasks":
            handler.send_json({"ok": True, "tasks": agency_tasks_list(q.get("project_id", ""))})
        elif path == "/api/agency/approvals":
            handler.send_json({"ok": True, "approvals": approvals_list(q.get("status", ""))})
        elif path == "/api/agency/agents":
            handler.send_json({"ok": True, "agents": agents_list()})
        elif path == "/api/agency/profiles":
            import profiles as _p
            handler.send_json({"ok": True, **_p.list_profiles()})
        elif path == "/api/agency/profiles/render":
            import profiles as _p
            target = q.get("target", "prompt")
            try:
                text = _p.render(_p.get(q.get("name", "")), target)
            except ValueError as exc:
                handler.send_json({"ok": False, "error": str(exc)}, 400)
                return True
            except KeyError as exc:
                handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
                return True
            handler.send_json({"ok": True, "target": target, "text": text})
        elif path == "/api/agency/keywords":
            handler.send_json({"ok": True, "keywords": keywords_list(q.get("client_id", ""))})
        elif path == "/api/agency/boards":
            handler.send_json({"ok": True, "boards": boards_list()})
        elif path == "/api/agency/activity":
            handler.send_json({"ok": True, "activity": activity_list(int(q.get("limit", "50")))})
        elif path == "/api/agency/search":
            handler.send_json({"ok": True, "results": search(q.get("q", ""))})
        elif path == "/api/agency/attention":
            handler.send_json({"ok": True, "attention": attention()})
        elif path == "/api/agency/backlinks":
            handler.send_json({"ok": True, "prospects": backlinks_list(q.get("client_id", ""))})
        elif path == "/api/agency/competitors":
            handler.send_json({"ok": True, "competitors": competitors_list(q.get("client_id", ""))})
        elif path == "/api/agency/runs":
            handler.send_json({"ok": True, "runs": runs_list(int(q.get("limit", "30")))})
        elif path == "/api/agency/report":
            handler.send_json({"ok": True, **client_report(q.get("client_id", ""))})
        elif path == "/api/agency/report.html":
            handler.send_text(_report_html(client_report(q.get("client_id", ""))), 200, "text/html; charset=utf-8")
        elif path == "/api/agency/playbooks":
            import playbooks as _pb
            handler.send_json({"ok": True, "playbooks": _pb.summaries(), "engine": _engine_health()})
        elif path == "/api/agency/pipeline/runs":
            import runner as _r
            handler.send_json({"ok": True, "runs": _r.runs_list(int(q.get("limit", "20")))})
        elif path == "/api/agency/pipeline/run":
            import runner as _r
            handler.send_json({"ok": True, "run": _r.get_run(q.get("id", ""))})
        elif path == "/api/agency/artifact":
            import runner as _r
            handler.send_json({"ok": True, "artifact": _r.artifact(q.get("id", ""))})
        elif path == "/api/agency/artifact/raw":
            import runner as _r
            art = _r.artifact(q.get("id", ""))
            ctype = {"html": "text/html", "json": "application/json"}.get(art["kind"], "text/plain")
            handler.send_text(art["content"], 200, f"{ctype}; charset=utf-8")
        elif path == "/api/agency/site/thumb":
            _send_site_thumb(handler, q.get("project_id", ""), q.get("refresh") == "1")
        elif path == "/api/agency/media/status":
            import media as _m
            handler.send_json({"ok": True, **_m.status()})
        elif path == "/api/agency/media/catalog":
            import media as _m
            handler.send_json({"ok": True, **_m.catalog()})
        elif path == "/api/agency/media/jobs":
            import media as _m
            handler.send_json({"ok": True, "jobs": _m.jobs(int(q.get("limit", "60")), q.get("project_id", ""))})
        elif path == "/api/agency/media/job":
            import media as _m
            try:
                handler.send_json({"ok": True, "job": _m.job(q.get("id", ""))})
            except KeyError as exc:
                handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
        elif path == "/api/agency/media/file":
            _send_media(handler, q.get("path", ""))
        elif path == "/api/agency/computer/status":
            import computer as _c
            handler.send_json({"ok": True, **_c.status()})
        elif path == "/api/agency/computer/sessions":
            import computer as _c
            handler.send_json({"ok": True, "sessions": _c.sessions(int(q.get("limit", "20")))})
        elif path == "/api/agency/computer/session":
            import computer as _c
            try:
                handler.send_json({"ok": True, "session": _c.get(q.get("id", ""), int(q.get("since", "0")))})
            except KeyError as exc:
                handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
        elif path == "/api/agency/autopilot":
            import autopilot as _ap
            handler.send_json({"ok": True, "autopilot": _ap.settings(q.get("project_id", ""))})
        elif path == "/api/agency/reports":
            import autopilot as _ap
            handler.send_json({"ok": True, "clients": _ap.reports()})
        elif path == "/api/agency/reports/file":
            import autopilot as _ap
            try:
                data = _ap.report_file(q.get("id", "")).read_bytes()
            except KeyError as exc:
                handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
                return True
            handler.send_response(200)
            handler.send_header("Content-Type", "application/pdf")
            handler.send_header("Content-Disposition", 'inline; filename="client-report.pdf"')
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        elif path == "/api/agency/office":
            handler.send_json({"ok": True, **office_state()})
        elif path == "/api/agency/openseo/status":
            import openseo as _o
            handler.send_json({"ok": True, "openseo": _o.status()})
        elif path == "/api/agency/dataforseo/status":
            import dataforseo as _d
            handler.send_json({"ok": True, **_d.status()})
        elif path == "/api/agency/models/status":
            handler.send_json({"ok": True, **_models_status()})
        elif path == "/api/agency/laya/status":
            import laya_engine as _l
            handler.send_json({"ok": True, "laya": _l.status()})
        elif path == "/api/agency/jev/status":
            import jev as _j
            handler.send_json({"ok": True, **_j.status()})
        elif path == "/api/agency/browser/status":
            import browser as _b
            handler.send_json({"ok": True, **_b.status()})
        elif path == "/api/agency/screenshot":
            _send_screenshot(handler, q.get("path", ""))
        elif path == "/api/agency/artifacts":
            import runner as _r
            handler.send_json({"ok": True, "artifacts": _r.artifacts_for_project(q.get("project_id", ""))})
        else:
            handler.send_json({"ok": False, "error": "unknown agency endpoint"}, 404)
    except Exception as exc:  # surface, never swallow
        handler.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
    return True


POST_ROUTES = {
    "/api/agency/clients": lambda q, d: {"client": client_create(d)},
    "/api/agency/clients/update": lambda q, d: {"client": client_update(q.get("id", ""), d)},
    "/api/agency/clients/delete": lambda q, d: client_delete(q.get("id", "")),
    "/api/agency/projects": lambda q, d: {"project": project_create(d)},
    "/api/agency/projects/update": lambda q, d: {"project": project_update(q.get("id", ""), d)},
    "/api/agency/projects/delete": lambda q, d: project_delete(q.get("id", "")),
    "/api/agency/tasks": lambda q, d: {"task": agency_task_create(d)},
    "/api/agency/tasks/update": lambda q, d: {"task": agency_task_update(q.get("id", ""), d)},
    "/api/agency/tasks/delete": lambda q, d: agency_task_delete(q.get("id", "")),
    "/api/agency/approvals": lambda q, d: {"approval": approval_create(d)},
    "/api/agency/approvals/decide": lambda q, d: {"approval": approval_decide(q.get("id", ""), str(d.get("decision", "")))},
    "/api/agency/keywords": lambda q, d: {"keyword": keyword_create(d)},
    "/api/agency/keywords/update": lambda q, d: {"keyword": keyword_update(q.get("id", ""), d)},
    "/api/agency/keywords/delete": lambda q, d: keyword_delete(q.get("id", "")),
    "/api/agency/boards": lambda q, d: {"board": board_create(d)},
    "/api/agency/boards/update": lambda q, d: {"board": board_update(q.get("id", ""), d)},
    "/api/agency/boards/delete": lambda q, d: board_delete(q.get("id", "")),
    "/api/agency/backlinks": lambda q, d: {"prospect": backlink_create(d)},
    "/api/agency/backlinks/update": lambda q, d: {"prospect": backlink_update(q.get("id", ""), d)},
    "/api/agency/backlinks/delete": lambda q, d: backlink_delete(q.get("id", "")),
    "/api/agency/backlinks/request-approval": lambda q, d: {"approval": backlink_request_approval(q.get("id", ""))},
    "/api/agency/competitors": lambda q, d: {"competitor": competitor_create(d)},
    "/api/agency/competitors/delete": lambda q, d: competitor_delete(q.get("id", "")),
    "/api/agency/pipeline/start": lambda q, d: _pipeline_start(d),
    "/api/agency/pipeline/resume": lambda q, d: _pipeline_call("resume_run", q.get("id", "")),
    "/api/agency/pipeline/cancel": lambda q, d: _pipeline_call("cancel_run", q.get("id", "")),
    "/api/agency/openseo/install": lambda q, d: {"openseo": __import__("openseo").install()},
    "/api/agency/openseo/start": lambda q, d: {"openseo": __import__("openseo").start()},
    "/api/agency/openseo/stop": lambda q, d: {"openseo": __import__("openseo").stop()},
    "/api/agency/dataforseo/setup": lambda q, d: _dfs_setup(d),
    "/api/agency/dataforseo/disconnect": lambda q, d: _dfs_disconnect(),
    "/api/agency/dataforseo/enrich": lambda q, d: _dfs_enrich(d),
    "/api/agency/models/setup": lambda q, d: _provider_setup(d),
    "/api/agency/models/list": lambda q, d: _provider_models(d),
    "/api/agency/models/select": lambda q, d: _provider_model(d),
    "/api/agency/models/disconnect": lambda q, d: _provider_disconnect(d),
    "/api/agency/models/test": lambda q, d: _provider_test(d),
    "/api/agency/laya/install": lambda q, d: {"laya": __import__("laya_engine").install()},
    "/api/agency/laya/start": lambda q, d: {"laya": __import__("laya_engine").start()},
    "/api/agency/laya/stop": lambda q, d: {"laya": __import__("laya_engine").stop()},
    "/api/agency/jev/setup": lambda q, d: _jev_setup(d),
    "/api/agency/jev/disconnect": lambda q, d: _jev_disconnect(d),
    "/api/agency/voice/ask": lambda q, d: _voice_ask(d),
    "/api/agency/models/brain": lambda q, d: _brain_order(d),
    "/api/agency/media/key": lambda q, d: (__import__("media").save_key(str(d.get("api_key", ""))),
                                           {"media": __import__("media").status()})[1],
    "/api/agency/media/disconnect": lambda q, d: (__import__("media").clear_key(), {"media": __import__("media").status()})[1],
    "/api/agency/media/upload": lambda q, d: {"url": __import__("media").upload_reference(
        str(d.get("filename", "")), str(d.get("data", "")))},
    "/api/agency/media/generate": lambda q, d: {"job": __import__("media").generate(
        str(d.get("model", "")), d.get("params") if isinstance(d.get("params"), dict) else {},
        str(d.get("project_id", "")), str(d.get("note", "")))},
    "/api/agency/media/cancel": lambda q, d: {"job": __import__("media").cancel(str(d.get("id", "")))},
    "/api/agency/media/improve": lambda q, d: __import__("media").improve_prompt(
        str(d.get("prompt", "")), str(d.get("kind", ""))),
    "/api/agency/media/seo": lambda q, d: {"seo": __import__("media").seo_metadata(str(d.get("id", "")))},
    "/api/agency/computer/start": lambda q, d: {"session": __import__("computer").start(
        str(d.get("task", "")), str(d.get("url", "")), d.get("max_steps") or 25, bool(d.get("visible")),
        brain=str(d.get("brain") or "auto"))},
    "/api/agency/computer/stop": lambda q, d: {"session": __import__("computer").stop(str(d.get("id", "")))},
    "/api/agency/computer/decide": lambda q, d: {"session": __import__("computer").decide(
        str(d.get("id", "")), d.get("approve") is True)},
    "/api/agency/models/ollama": lambda q, d: _ollama_setup(d),
    "/api/agency/browser/capture": lambda q, d: _browser_capture(d),
    "/api/agency/gsc/setup": lambda q, d: _gsc_setup(d),
    "/api/agency/gsc/map": lambda q, d: gsc_map_client(str(d.get("client_id", "")), str(d.get("site_url", ""))),
    "/api/agency/gsc/automap": lambda q, d: gsc_auto_map(),
    "/api/agency/autopilot": lambda q, d: {"autopilot": __import__("autopilot").set_autopilot(
        str(d.get("project_id", "")), d.get("enabled") is True, int(d.get("every_days") or 7))},
    "/api/agency/reports/generate": lambda q, d: {"report": __import__("autopilot").generate_report(
        str(d.get("client_id", "")))},
    "/api/agency/gsc/sync": lambda q, d: gsc_sync_client(str(d.get("client_id", ""))),
    "/api/agency/gsc/disconnect": lambda q, d: _gsc_disconnect(),
    "/api/agency/profiles/install": lambda q, d: _profiles_install(d),
}


def _profiles_install(d: dict) -> dict[str, Any]:
    """Install or remove agency profiles for Claude Code / Cursor from the UI."""
    import profiles
    names = d.get("names") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("names must be a list of agent names")
    result = profiles.install(str(d.get("where", "")), names or None,
                              uninstall=bool(d.get("uninstall")), force=bool(d.get("force")))
    with _conn() as conn:
        log_activity(conn, "uninstall_profiles" if result["uninstall"] else "install_profiles", "agency",
                     result["where"], {"changed": result["changed"], "skipped": result["skipped"]})
    return {"result": result}


_engine_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _engine_health() -> dict[str, Any]:
    """Gateway liveness, cached 30s. Probing it on every page load made the
    Press Room take two seconds to paint."""
    import time as _time
    import llm as _llm
    if _engine_cache["value"] is not None and _time.time() - _engine_cache["at"] < 30:
        return _engine_cache["value"]
    value = _llm.health()
    _engine_cache.update(at=_time.time(), value=value)
    return value


def _pipeline_start(d: dict) -> dict[str, Any]:
    """Start a run. Optionally creates the project first from a raw idea, so the
    UI can go from a typed sentence to a running pipeline in one call."""
    import runner
    project_id = str(d.get("project_id") or "")
    playbook = str(d.get("playbook") or "")
    if not playbook:
        raise ValueError("playbook is required")
    if not project_id:
        name = str(d.get("name", "")).strip()
        brief = str(d.get("brief", "")).strip()
        if not name and not brief:
            raise ValueError("give the project a name or an idea to start from")
        ptype = "seo_campaign" if playbook == "seo_campaign" else "marketing_website"
        project = project_create({
            "name": name or brief[:60], "brief": brief, "type": ptype,
            "client_id": d.get("client_id") or "",
            "stage_total": len(__import__("playbooks").get(playbook)["stages"]),
        })
        project_id = project["id"]
        url = str(d.get("url", "")).strip()
        if url or playbook:
            with _conn() as conn:
                conn.execute("UPDATE projects SET url=?, playbook=? WHERE id=?", (url, playbook, project_id))
    else:
        url = str(d.get("url", "")).strip()
        if url:
            with _conn() as conn:
                conn.execute("UPDATE projects SET url=?, playbook=? WHERE id=?", (url, playbook, project_id))
    return {"run": runner.start_run(project_id, playbook)}


def _pipeline_call(fn: str, run_id: str) -> dict[str, Any]:
    import runner
    if not run_id:
        raise ValueError("run id is required")
    return {"run": getattr(runner, fn)(run_id)}


THUMB_DIR = PROJECT_DIR / "workspace" / "_thumbs"
THUMB_MAX_AGE = 24 * 3600
_THUMB_LOCK = threading.Lock()


def site_thumb(project_id: str, refresh: bool = False) -> Path:
    """A real screenshot of a project's public website, cached for a day.

    The design system asks for actual website previews wherever they exist and
    a monogram only as the fallback. Sites the agency did not build have no
    preview artifact, so their first appearance takes one photograph with the
    local headless browser; one capture at a time, since every card on the
    overview asks at once.
    """
    import browser
    with _conn() as conn:
        row = conn.execute("SELECT url FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise KeyError("project not found")
    url = (row["url"] or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise KeyError("this project has no website address")
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    out = THUMB_DIR / f"{re.sub(r'[^a-zA-Z0-9-]', '', project_id)}.png"
    fresh = out.exists() and out.stat().st_size > 2048 and time.time() - out.stat().st_mtime < THUMB_MAX_AGE
    if fresh and not refresh:
        return out
    with _THUMB_LOCK:
        fresh = out.exists() and out.stat().st_size > 2048 and time.time() - out.stat().st_mtime < THUMB_MAX_AGE
        if refresh or not fresh:
            browser.screenshot(url, out, viewport="desktop")
    if not out.exists():
        raise KeyError("the website could not be captured")
    return out


def _send_site_thumb(handler, project_id: str, refresh: bool) -> None:
    try:
        path = site_thumb(project_id, refresh)
    except KeyError as exc:
        handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
        return
    except Exception as exc:  # an unreachable site is a missing preview, not a server error
        handler.send_json({"ok": False, "error": f"capture failed: {type(exc).__name__}: {exc}"}, 404)
        return
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "private, max-age=3600")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}


def _send_media(handler, raw_path: str) -> None:
    """Serve a generated image or video, confined to workspace/media.

    Honours a single byte Range so a video can be scrubbed without first
    downloading the whole file.
    """
    root = (PROJECT_DIR / "workspace" / "media").resolve()
    try:
        target = Path(raw_path).expanduser().resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        handler.send_json({"ok": False, "error": "path is outside the media folder"}, 403)
        return
    ctype = _MEDIA_TYPES.get(target.suffix.lower())
    if not target.is_file() or not ctype:
        handler.send_json({"ok": False, "error": "file not found"}, 404)
        return
    size = target.stat().st_size
    start, end = 0, size - 1
    rng = handler.headers.get("Range", "")
    m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip()) if rng else None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                   # suffix range: the last N bytes
            start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
    length = end - start + 1
    handler.send_response(206 if m else 200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "private, max-age=3600")
    handler.send_header("Content-Length", str(length))
    if m:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with open(target, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(1 << 16, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def _send_screenshot(handler, raw_path: str) -> None:
    """Serve a captured PNG.

    Screenshots live under workspace/. The path is resolved and confined there
    so a crafted query cannot walk out and read arbitrary files.
    """
    workspace = (PROJECT_DIR / "workspace").resolve()
    if not raw_path:
        handler.send_json({"ok": False, "error": "path is required"}, 400)
        return
    try:
        target = Path(raw_path).expanduser().resolve()
        target.relative_to(workspace)
    except (ValueError, OSError):
        handler.send_json({"ok": False, "error": "path is outside the workspace"}, 403)
        return
    if not target.is_file() or target.suffix.lower() != ".png":
        handler.send_json({"ok": False, "error": "screenshot not found"}, 404)
        return
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _dfs_setup(d: dict) -> dict[str, Any]:
    import dataforseo
    dataforseo.save_credentials(str(d.get("login", "")), str(d.get("password", "")))
    _openseo_follow_key()
    return {"configured": True, **dataforseo.status()}


def _dfs_disconnect() -> dict[str, Any]:
    import dataforseo
    dataforseo.disconnect()
    _openseo_follow_key()
    return {"disconnected": True}


def _openseo_follow_key() -> None:
    """OpenSEO reads the same DataForSEO key: a running one restarts to pick up the change."""
    import openseo
    _openseo_state_cache["value"] = None
    threading.Thread(target=openseo.restart_if_running, name="openseo-rekey", daemon=True).start()


def _dfs_enrich(d: dict) -> dict[str, Any]:
    """Fill stored keywords with measured volume and difficulty.

    Only rows for the given client are touched, and only ones that are not
    already 'verified' -- Search Console data outranks a third-party estimate
    and must not be overwritten by it.
    """
    import dataforseo
    client_id = str(d.get("client_id", "")).strip()
    if not client_id:
        raise ValueError("client_id is required")
    rows = [k for k in keywords_list(client_id) if k["provenance"] != "verified"]
    if not rows:
        return {"updated": 0, "cost_usd": 0.0,
                "note": "Nothing to enrich: every tracked keyword already carries verified data."}
    terms = [r["keyword"] for r in rows][:dataforseo.MAX_KEYWORDS]
    volumes = dataforseo.search_volume(terms)
    by_term = {str(v["keyword"]).lower(): v for v in volumes["keywords"] if v.get("keyword")}
    cost = volumes["cost_usd"]
    difficulty: dict[str, Any] = {}
    try:
        diff = dataforseo.keyword_difficulty(terms)
        cost += diff["cost_usd"]
        difficulty = {str(x["keyword"]).lower(): x.get("difficulty")
                      for x in diff["keywords"] if x.get("keyword")}
    except dataforseo.DataForSEOError:
        pass  # volume alone is still worth storing; difficulty is a bonus call

    updated = 0
    with _conn() as conn:
        for row in rows:
            hit = by_term.get(row["keyword"].lower())
            if not hit:
                continue
            conn.execute(
                "UPDATE seo_keywords SET volume=?, difficulty=?, source='dataforseo', "
                "provenance='imported', updated_at=? WHERE id=?",
                (hit.get("volume"), difficulty.get(row["keyword"].lower()), now_iso(), row["id"]))
            updated += 1
        log_activity(conn, "enrich_keywords", "client", client_id,
                     {"updated": updated, "cost_usd": round(cost, 4)})
    return {"updated": updated, "requested": len(terms), "cost_usd": round(cost, 4),
            "note": "Volume and difficulty are measured by DataForSEO and stored as 'imported'. "
                    "Keywords already verified in Search Console were left untouched."}


def _models_status() -> dict[str, Any]:
    """Provider + Jev connection state for the Integrations page."""
    import jev
    import providers
    out = {"providers": providers.status()}
    out["signin"] = providers.signin_hint()
    import laya_engine
    out["jev"] = {"connected": jev.connected(), "cloud": jev.cloud_connected(),
                  "engines": jev.engines(), "laya": laya_engine.status(),
                  "label": "Typed decisions",
                  "note": "Classification, routing and scoring with a calibrated confidence, "
                          "instead of a full model call. Laya runs free on this computer; "
                          "Jev (TypeSafe) is the optional cloud engine."}
    return out


def _provider_setup(d: dict) -> dict[str, Any]:
    import providers
    providers.save_credentials(str(d.get("provider", "")), str(d.get("api_key", "")),
                               str(d.get("model", "")))
    return {"providers": providers.status()}


def _provider_models(d: dict) -> dict[str, Any]:
    """List what the account can really use, so no model id is ever guessed."""
    import providers
    return {"models": providers.models(str(d.get("provider", "")))}


def _provider_model(d: dict) -> dict[str, Any]:
    import providers
    providers.set_model(str(d.get("provider", "")), str(d.get("model", "")))
    return {"providers": providers.status()}


def _brain_order(d: dict) -> dict[str, Any]:
    """Set the order every thinking task walks: first choice, then fallbacks."""
    import providers
    providers.set_brain_order(d.get("order"))
    return {"providers": providers.status()}


def _ollama_setup(d: dict) -> dict[str, Any]:
    """Point the local fallback at an Ollama server and pick a model on it."""
    import providers
    providers.set_ollama(str(d.get("host", "")), str(d.get("model", "")))
    return {"providers": providers.status()}


def _provider_disconnect(d: dict) -> dict[str, Any]:
    import providers
    providers.disconnect(str(d.get("provider", "")))
    return {"providers": providers.status()}


def _provider_test(d: dict) -> dict[str, Any]:
    """Spend one small call to prove the connection really works."""
    import providers
    provider = str(d.get("provider", ""))
    res = providers.complete("Reply with exactly: ok", provider=provider, timeout=60)
    return {"ok": True, "provider": res["provider"], "model": res.get("model", ""),
            "reply": res["text"][:120], "seconds": res.get("seconds", 0),
            "tokens_out": res.get("tokens_out", 0)}


def _voice_chat_sse(handler, d: dict) -> None:
    """Jarvis conversation turn, streamed as server-sent events so speech can
    start on the first sentence: meta, token..., actions?, done | error."""
    import jarvis

    def send(kind: str, data: Any) -> bool:
        try:
            handler.wfile.write(("data: " + json.dumps({"type": kind, "data": data}, ensure_ascii=False)
                                 + "\n\n").encode("utf-8"))
            handler.wfile.flush()
            return True
        except OSError:
            return False                     # the operator interrupted and the page hung up

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    history = d.get("history") if isinstance(d.get("history"), list) else []
    try:
        for kind, data in jarvis.chat_events(str(d.get("text", "")), history, str(d.get("page", ""))):
            if not send(kind, data):
                return
    except Exception as exc:
        send("error", str(exc)[:600])


def _voice_ask(d: dict) -> dict[str, Any]:
    """Jarvis: a spoken question the browser could not answer locally."""
    import jarvis
    history = d.get("history") if isinstance(d.get("history"), list) else []
    return jarvis.ask(str(d.get("text", "")), history)


def _jev_setup(d: dict) -> dict[str, Any]:
    import jev
    jev.save_credentials(str(d.get("api_key", "")))
    return jev.status()


def _jev_disconnect(d: dict) -> dict[str, Any]:
    import jev
    jev.disconnect()
    return {"disconnected": True}


def _browser_capture(d: dict) -> dict[str, Any]:
    """Screenshot any allowed URL on demand, from the UI."""
    import browser
    url = str(d.get("url", "")).strip()
    viewport = str(d.get("viewport", "desktop"))
    if not url:
        raise ValueError("url is required")
    out_dir = PROJECT_DIR / "workspace" / "_captures"
    stamp = now_iso().replace(":", "-").replace(".", "-")
    target = out_dir / f"{stamp}-{viewport}.png"
    shot = browser.screenshot(url, target, viewport=viewport)
    shot["view_url"] = "/api/agency/screenshot?path=" + quote(str(target))
    return {"screenshot": shot}


def _gsc_setup(d: dict) -> dict[str, Any]:
    gsc.save_credentials(str(d.get("client_id", "")), str(d.get("client_secret", "")))
    _gsc_state_cache["value"] = None
    return {"configured": True}


def _gsc_disconnect() -> dict[str, Any]:
    gsc.disconnect()
    _gsc_state_cache["value"] = None
    return {"disconnected": True}


def handle_post(handler, parsed, qs: dict, data: dict) -> bool:
    """Route agency POSTs. Returns True when the request was handled."""
    path = parsed.path
    if not path.startswith("/api/agency/"):
        return False
    init_db()
    if not _host_ok(handler):
        handler.send_json({"ok": False, "error": "invalid host"}, 403)
        return True
    if not _auth_ok(handler):
        handler.send_json({"ok": False, "error": "missing or invalid bearer token"}, 401)
        return True
    if path == "/api/agency/voice/chat":
        _voice_chat_sse(handler, data)
        return True
    route = POST_ROUTES.get(path)
    if route is None:
        handler.send_json({"ok": False, "error": "unknown agency endpoint"}, 404)
        return True
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in qs.items()}
    try:
        result = route(q, data)
        handler.send_json({"ok": True, **result}, 201 if path in {
            "/api/agency/clients", "/api/agency/projects", "/api/agency/tasks",
            "/api/agency/approvals", "/api/agency/keywords", "/api/agency/boards"} else 200)
    except (ValueError,) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    except KeyError as exc:
        handler.send_json({"ok": False, "error": str(exc).strip("'")}, 404)
    except Exception as exc:
        handler.send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
    return True
