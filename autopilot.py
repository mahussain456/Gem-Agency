"""Autopilot: the agency keeps working without a click.

Two jobs, checked every ten minutes by a background thread in the server:

  * Scheduled campaigns. A live website can be put on autopilot: every N days
    (weekly by default) it gets a fresh SEO campaign, which audits the real
    URL and plans fixes, content, AEO/GEO and outreach. Off by default for
    every site, because each run spends model credits. A site with a run
    already in progress is skipped, never doubled.
  * Monthly client reports. Each month, every client with a website gets the
    client report (the same one on the Growth page) saved as a PDF, printed by
    the installed Chrome. It is generated once per client per month; a
    restart never generates it twice.

Everything is recorded in agency.db (autopilot, client_reports), so the
schedule survives restarts and the dashboard can say what ran and when.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agency

PROJECT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_DIR / "workspace" / "reports"
CHECK_EVERY = 600                          # seconds between checks
PLAYBOOK = "seo_campaign"
EVERY_DAYS = (7, 14, 30)

_started = False
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


# ---------------------------------------------------------------- settings

def settings(project_id: str) -> dict[str, Any]:
    with agency._conn() as conn:
        p = conn.execute("SELECT id, name, url, status FROM projects WHERE id=?", (project_id,)).fetchone()
        if not p:
            raise KeyError("project not found")
        row = conn.execute("SELECT * FROM autopilot WHERE project_id=?", (project_id,)).fetchone()
    s = dict(row) if row else {"project_id": project_id, "enabled": 0, "every_days": 7,
                                "last_run_at": "", "last_run_id": "", "last_note": ""}
    s["enabled"] = bool(s["enabled"])
    s["has_url"] = bool((p["url"] or "").strip())
    last = _parse(s["last_run_at"])
    s["next_at"] = ((last + timedelta(days=s["every_days"])) if last else _now()).isoformat() if s["enabled"] else ""
    return s


def set_autopilot(project_id: str, enabled: bool, every_days: int = 7) -> dict[str, Any]:
    if int(every_days) not in EVERY_DAYS:
        raise ValueError(f"run every {', '.join(map(str, EVERY_DAYS))} days")
    s = settings(project_id)
    if enabled and not s["has_url"]:
        raise ValueError("an SEO campaign audits the live website: add the site's address first")
    with agency._conn() as conn:
        conn.execute(
            "INSERT INTO autopilot (project_id, enabled, every_days, last_run_at, last_run_id, last_note) "
            "VALUES (?,?,?,'','','') ON CONFLICT(project_id) DO UPDATE SET enabled=excluded.enabled, "
            "every_days=excluded.every_days", (project_id, int(bool(enabled)), int(every_days)))
        agency.log_activity(conn, "autopilot_on" if enabled else "autopilot_off", "project", project_id,
                            {"every_days": int(every_days)})
    return settings(project_id)


# ---------------------------------------------------------------- campaigns

def due_campaigns(now: datetime | None = None) -> list[str]:
    now = now or _now()
    with agency._conn() as conn:
        rows = agency._rows(conn.execute(
            "SELECT a.project_id, a.every_days, a.last_run_at FROM autopilot a "
            "JOIN projects p ON p.id = a.project_id WHERE a.enabled = 1 AND p.status != 'archived' "
            "AND p.url != '' AND NOT EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.project_id = a.project_id "
            "AND r.state IN ('queued','running','awaiting_approval'))"))
    due = []
    for r in rows:
        last = _parse(r["last_run_at"])
        if not last or now - last >= timedelta(days=r["every_days"]):
            due.append(r["project_id"])
    return due


def run_campaigns(now: datetime | None = None) -> list[dict[str, Any]]:
    import runner
    out = []
    for pid in due_campaigns(now):
        stamp = (now or _now()).isoformat()
        try:
            run = runner.start_run(pid, PLAYBOOK)
            note, run_id = "started", run.get("id", "")
        except Exception as exc:                    # recorded, retried at the next interval
            note, run_id = f"could not start: {exc}"[:300], ""
        with agency._conn() as conn:
            conn.execute("UPDATE autopilot SET last_run_at=?, last_run_id=?, last_note=? WHERE project_id=?",
                         (stamp, run_id, note, pid))
        out.append({"project_id": pid, "note": note, "run_id": run_id})
    return out


# ---------------------------------------------------------------- reports

def period_of(now: datetime | None = None) -> str:
    return (now or _now()).strftime("%Y-%m")


def render_pdf(html: str, out: Path) -> Path:
    """Print an HTML page to PDF with the installed Chrome (Playwright)."""
    import browser
    from playwright.sync_api import sync_playwright
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        kw: dict[str, Any] = {"headless": True}
        exe = browser.find_browser()
        if exe:
            kw["executable_path"] = exe
        b = p.chromium.launch(**kw)
        try:
            page = b.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), format="A4", print_background=True,
                     margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        finally:
            b.close()
    return out


def generate_report(client_id: str, period: str | None = None) -> dict[str, Any]:
    period = period or period_of()
    rep = agency.client_report(client_id)
    html = agency._report_html(rep)
    safe = "".join(ch for ch in client_id if ch.isalnum() or ch == "-")
    out = render_pdf(html, REPORTS_DIR / safe / f"{period}.pdf")
    rid = str(uuid.uuid4())
    with agency._conn() as conn:
        conn.execute("INSERT INTO client_reports (id, client_id, period, path, created_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(client_id, period) DO UPDATE SET path=excluded.path, created_at=excluded.created_at",
                     (rid, client_id, period, str(out), agency.now_iso()))
        row = conn.execute("SELECT * FROM client_reports WHERE client_id=? AND period=?", (client_id, period)).fetchone()
        agency.log_activity(conn, "report_generated", "client", client_id, {"period": period})
    return dict(row)


def run_reports(now: datetime | None = None) -> list[dict[str, Any]]:
    """This month's report for each client with a website, once."""
    period = period_of(now)
    with agency._conn() as conn:
        clients = [r["id"] for r in conn.execute(
            "SELECT DISTINCT c.id FROM clients c JOIN projects p ON p.client_id = c.id "
            "WHERE c.status = 'active' AND p.status != 'archived' AND p.url != '' "
            "AND NOT EXISTS (SELECT 1 FROM client_reports r WHERE r.client_id = c.id AND r.period = ?)", (period,))]
    out = []
    for cid in clients:
        try:
            out.append(generate_report(cid, period))
        except Exception as exc:
            out.append({"client_id": cid, "error": f"{type(exc).__name__}: {exc}"[:300]})
    return out


def reports() -> list[dict[str, Any]]:
    with agency._conn() as conn:
        clients = agency._rows(conn.execute("SELECT id, name FROM clients WHERE status='active' ORDER BY name"))
        rows = agency._rows(conn.execute("SELECT id, client_id, period, created_at FROM client_reports "
                                         "ORDER BY period DESC"))
    for c in clients:
        c["reports"] = [r for r in rows if r["client_id"] == c["id"]][:12]
    return clients


def report_file(report_id: str) -> Path:
    with agency._conn() as conn:
        row = conn.execute("SELECT path FROM client_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise KeyError("report not found")
    path = Path(row["path"]).resolve()
    if REPORTS_DIR.resolve() not in path.parents or not path.is_file():
        raise KeyError("report file is missing")
    return path


# ---------------------------------------------------------------- the loop

def tick(now: datetime | None = None) -> dict[str, Any]:
    with _lock:
        return {"campaigns": run_campaigns(now), "reports": run_reports(now)}


def _loop() -> None:
    time.sleep(30)                                   # let the server finish starting
    while True:
        try:
            tick()
        except Exception:
            pass                                     # one bad tick must not stop the schedule
        time.sleep(CHECK_EVERY)


def start() -> None:
    """Start the background schedule (once per process)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="autopilot", daemon=True).start()
