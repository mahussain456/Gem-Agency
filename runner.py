"""The pipeline runner — what turns the board into a machine.

A run is a project executing a playbook. A background worker thread walks the
stages in order, calling real agents through the Hermes gateway or running
real local tools (a live website audit, a live prospect verification), storing
every output as an artifact, and stopping dead at approval gates until a human
decides.

Design notes
  · One worker thread per process, one run at a time. Runs are long (minutes)
    and the gateway is a shared resource — parallelism would just queue anyway.
  · Every stage records started/finished/tokens/error, so a run is auditable.
  · A stage failure halts the run and is surfaced verbatim. Nothing is skipped
    silently and no placeholder output is ever written on failure.
  · Resuming after a gate is explicit: approving the gate re-queues the run.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

import agency
import audit
import browser
import llm
import playbooks
import providers

# Every stage thinks with the brain chain (Claude, ChatGPT, then local Ollama,
# in the order set on the Models page) unless its playbook names a provider.
DEFAULT_PROVIDER = providers.BRAIN

WORKSPACE = Path(__file__).resolve().parent / "workspace"

_jobs: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


# ---------------------------------------------------------------- lifecycle

def ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="pipeline-runner", daemon=True)
        _worker.start()


def _loop() -> None:
    while True:
        run_id = _jobs.get()
        try:
            _execute_run(run_id)
        except Exception:
            # never let the worker die on one bad run
            try:
                _fail_run(run_id, traceback.format_exc(limit=4))
            except Exception:
                pass
        finally:
            _jobs.task_done()


# ---------------------------------------------------------------- public API

def start_run(project_id: str, playbook_id: str) -> dict[str, Any]:
    """Create a run and queue it. Returns the run record."""
    pb = playbooks.get(playbook_id)
    project = _project(project_id)
    if not project:
        raise KeyError("project not found")
    run_id = str(uuid.uuid4())
    now = agency.now_iso()
    with agency._conn() as conn:
        active = conn.execute(
            "SELECT id FROM pipeline_runs WHERE project_id=? AND state IN ('queued','running','awaiting_approval')",
            (project_id,)).fetchone()
        if active:
            raise ValueError("this project already has a run in progress")
        conn.execute(
            "INSERT INTO pipeline_runs (id, project_id, playbook, state, stage_index, created_at, updated_at) "
            "VALUES (?,?,?,'queued',0,?,?)", (run_id, project_id, playbook_id, now, now))
        for idx, stage in enumerate(pb["stages"]):
            conn.execute(
                "INSERT INTO stage_runs (id, run_id, stage_id, title, kind, agent, state, position) "
                "VALUES (?,?,?,?,?,?,'pending',?)",
                (str(uuid.uuid4()), run_id, stage["id"], stage["title"], stage["kind"],
                 stage.get("agent", stage.get("tool", "")), idx))
        agency.log_activity(conn, "run_started", "project", project_id,
                            {"playbook": playbook_id, "run_id": run_id})
    ensure_worker()
    _jobs.put(run_id)
    return get_run(run_id)


def resume_run(run_id: str) -> dict[str, Any]:
    """Re-queue a run that was paused at a gate (called when the gate is decided)."""
    with agency._conn() as conn:
        row = conn.execute("SELECT state FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("run not found")
        if row["state"] not in ("awaiting_approval", "failed"):
            raise ValueError(f"run is {row['state']}, not resumable")
        conn.execute("UPDATE pipeline_runs SET state='queued', error='', updated_at=? WHERE id=?",
                     (agency.now_iso(), run_id))
    ensure_worker()
    _jobs.put(run_id)
    return get_run(run_id)


def cancel_run(run_id: str) -> dict[str, Any]:
    with agency._conn() as conn:
        conn.execute("UPDATE pipeline_runs SET state='cancelled', updated_at=? WHERE id=?",
                     (agency.now_iso(), run_id))
        conn.execute("UPDATE stage_runs SET state='skipped' WHERE run_id=? AND state='pending'", (run_id,))
    return get_run(run_id)


def get_run(run_id: str) -> dict[str, Any]:
    with agency._conn() as conn:
        run = conn.execute(
            "SELECT r.*, p.name AS project_name, p.brief, p.url FROM pipeline_runs r "
            "LEFT JOIN projects p ON p.id=r.project_id WHERE r.id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError("run not found")
        run = dict(run)
        run["stages"] = [dict(s) for s in conn.execute(
            "SELECT * FROM stage_runs WHERE run_id=? ORDER BY position", (run_id,))]
        run["artifacts"] = [dict(a) for a in conn.execute(
            "SELECT id, stage_id, title, kind, length(content) AS bytes, file_path, created_at "
            "FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,))]
    return run


def runs_list(limit: int = 20) -> list[dict[str, Any]]:
    with agency._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT r.*, p.name AS project_name FROM pipeline_runs r "
            "LEFT JOIN projects p ON p.id=r.project_id ORDER BY r.created_at DESC LIMIT ?",
            (max(1, min(limit, 100)),))]
        for r in rows:
            done = conn.execute("SELECT COUNT(*) AS n FROM stage_runs WHERE run_id=? AND state='done'",
                                (r["id"],)).fetchone()["n"]
            total = conn.execute("SELECT COUNT(*) AS n FROM stage_runs WHERE run_id=?",
                                 (r["id"],)).fetchone()["n"]
            r["done"], r["total"] = done, total
    return rows


def artifact(artifact_id: str) -> dict[str, Any]:
    with agency._conn() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError("artifact not found")
        return dict(row)


def artifacts_for_project(project_id: str) -> list[dict[str, Any]]:
    with agency._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, run_id, stage_id, title, kind, length(content) AS bytes, file_path, created_at "
            "FROM artifacts WHERE project_id=? ORDER BY created_at DESC", (project_id,))]


# ---------------------------------------------------------------- execution

def _project(project_id: str) -> dict[str, Any] | None:
    with agency._conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None


def _set_stage(run_id: str, stage_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with agency._conn() as conn:
        conn.execute(f"UPDATE stage_runs SET {sets} WHERE run_id=? AND stage_id=?",
                     [*fields.values(), run_id, stage_id])


def _set_run(run_id: str, **fields: Any) -> None:
    fields["updated_at"] = agency.now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with agency._conn() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {sets} WHERE id=?", [*fields.values(), run_id])


def _fail_run(run_id: str, error: str) -> None:
    _set_run(run_id, state="failed", error=error[:2000])
    with agency._conn() as conn:
        agency.log_activity(conn, "run_failed", "run", run_id, {"error": error[:300]})


def _store_artifact(run_id: str, project_id: str, stage: dict, content: str,
                    kind: str, file_path: str = "") -> str:
    art_id = str(uuid.uuid4())
    with agency._conn() as conn:
        conn.execute(
            "INSERT INTO artifacts (id, run_id, project_id, stage_id, title, kind, content, file_path, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (art_id, run_id, project_id, stage["id"], stage["title"], kind, content, file_path,
             agency.now_iso()))
    return art_id


def _context(run_id: str, project: dict[str, Any]) -> dict[str, Any]:
    """Project fields plus every artifact produced so far, keyed by stage id."""
    ctx: dict[str, Any] = {
        "name": project.get("name", ""),
        "brief": project.get("brief", "") or project.get("name", ""),
        "url": project.get("url", "") or "",
    }
    with agency._conn() as conn:
        for row in conn.execute("SELECT stage_id, kind, content FROM artifacts WHERE run_id=?", (run_id,)):
            val: Any = row["content"]
            if row["kind"] == "json":
                try:
                    val = json.loads(row["content"])
                except json.JSONDecodeError:
                    pass
            ctx[row["stage_id"]] = val
    return ctx


def _execute_run(run_id: str) -> None:
    run = get_run(run_id)
    if run["state"] == "cancelled":
        return
    pb = playbooks.get(run["playbook"])
    project = _project(run["project_id"])
    if not project:
        _fail_run(run_id, "project no longer exists")
        return
    _set_run(run_id, state="running", error="")

    for idx, stage in enumerate(pb["stages"]):
        current = get_run(run_id)
        if current["state"] == "cancelled":
            return
        st = next((s for s in current["stages"] if s["stage_id"] == stage["id"]), None)
        if st and st["state"] == "done":
            continue  # already completed on an earlier pass (resume after gate)

        _set_run(run_id, stage_index=idx)
        _set_stage(run_id, stage["id"], state="running", started_at=agency.now_iso(), error="")
        ctx = _context(run_id, project)

        # A stage may decide it has nothing to do. Skipping is recorded with a
        # reason, never silently: the run shows why the work was not needed.
        decide = stage.get("skip_if")
        if callable(decide):
            try:
                skip, reason = decide(ctx)
            except Exception as exc:
                skip, reason = False, f"skip check failed, running anyway: {exc}"
            if skip:
                _set_stage(run_id, stage["id"], state="skipped",
                           finished_at=agency.now_iso(), detail=reason[:400])
                continue
            if reason:
                _set_stage(run_id, stage["id"], detail=reason[:400])

        try:
            if stage["kind"] == "gate":
                approval = agency.approval_create({
                    "title": f"{stage['title']} — {project['name']}",
                    "detail": stage.get("gate_detail", ""),
                    "kind": stage.get("gate_kind", "proposal"),
                    "blocking": True,
                    "project_id": project["id"],
                    "source_agent": "pipeline",
                    "payload": {"run_id": run_id, "stage_id": stage["id"]},
                })
                _set_stage(run_id, stage["id"], state="awaiting_approval",
                           detail=f"approval {approval['id'][:8]}")
                _set_run(run_id, state="awaiting_approval")
                return  # worker moves on; approval decision re-queues this run

            if stage["kind"] == "tool":
                content, kind, note = _run_tool(stage, ctx, project, run_id)
            else:
                content, kind, note = _run_llm(stage, ctx, run_id)

            file_path = ""
            if stage.get("writes_file"):
                if stage.get("backup_first"):
                    existing = _project_dir(project) / stage["writes_file"]
                    if existing.exists():
                        (_project_dir(project) / "index.before-repair.html").write_text(
                            existing.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                file_path = _write_file(project, stage["writes_file"], content)

            _store_artifact(run_id, project["id"], stage, content, kind, file_path)
            _set_stage(run_id, stage["id"], state="done", finished_at=agency.now_iso(), detail=note)

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            _set_stage(run_id, stage["id"], state="failed", finished_at=agency.now_iso(), error=msg[:1000])
            _fail_run(run_id, f"stage '{stage['id']}' failed — {msg}")
            return

    _set_run(run_id, state="completed")
    with agency._conn() as conn:
        agency.log_activity(conn, "run_completed", "project", project["id"], {"run_id": run_id})
        # advance the project's visible stage to the end of its pipeline
        conn.execute("UPDATE projects SET stage=?, updated_at=? WHERE id=?",
                     (len(pb["stages"]), agency.now_iso(), project["id"]))


def _run_llm(stage: dict, ctx: dict, run_id: str) -> tuple[str, str, str]:
    """Run a generative stage on whichever provider it asks for.

    A stage may name a provider ("claude", "chatgpt", "ollama", "hermes") or
    leave it to the brain chain. Fallback is on: a provider that is out of
    credit or unreachable hands the stage to the next connected one instead of
    killing the run -- a real run was lost to an upstream 402 before this existed.
    """
    prompt = stage["prompt"](ctx)
    agent = stage.get("agent", "orchestrator")
    provider = stage.get("provider") or DEFAULT_PROVIDER

    if stage["kind"] == "json":
        res = _complete_json_via(provider, prompt, agent)
        content = json.dumps(res["data"], ensure_ascii=False, indent=2)
        kind = "json"
    else:
        res = providers.complete(prompt, provider=provider, agent=agent, fallback=True)
        content = res["text"].strip()
        kind = stage.get("output", "markdown")
        if kind == "html":
            content = _strip_fences(content)

    _set_stage(run_id, stage["id"], tokens_in=res.get("tokens_in", 0),
               tokens_out=res.get("tokens_out", 0), seconds=res.get("seconds", 0))
    used = res.get("provider", provider)
    label = res.get("model") or agent
    note = f"{used}:{label} · {res.get('seconds', 0)}s · {res.get('tokens_out', 0)} tokens out"
    if res.get("fallback_from"):
        failed = ", ".join(a["provider"] for a in res["fallback_from"])
        note += f" · fell back from {failed}"
    return content, kind, note


def _complete_json_via(provider: str, prompt: str, agent: str) -> dict[str, Any]:
    """JSON-mode completion for any provider.

    llm.complete_json owns the retry-and-reparse loop for the gateway; for the
    direct providers the same discipline is applied here so a stage that cannot
    read its own output still fails loudly rather than storing prose.
    """
    if provider == "hermes":
        return llm.complete_json(prompt, agent=agent)
    guard = ("You reply with valid JSON only. No prose, no markdown fences, "
             "no commentary before or after the JSON.")
    attempt_prompt = prompt
    last: Exception | None = None
    for _ in range(2):
        res = providers.complete(attempt_prompt, provider=provider, agent=agent,
                                 system=guard, fallback=True, json_mode=True)
        try:
            res["data"] = llm.extract_json(res["text"])
            return res
        except llm.LLMError as exc:
            last = exc
            attempt_prompt = (f"{prompt}\n\nYour previous reply was not valid JSON. "
                              f"Reply again with JSON only.")
    raise last or llm.LLMError("no JSON produced")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        t = t[first_nl + 1:] if first_nl != -1 else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _run_tool(stage: dict, ctx: dict, project: dict, run_id: str) -> tuple[str, str, str]:
    tool = stage["tool"]

    if tool == "audit_url":
        url = (project.get("url") or "").strip()
        if not url:
            raise ValueError("this project has no URL to audit — set the project URL first")
        report = audit.audit_page(url)
        return (json.dumps(report, ensure_ascii=False, indent=2), "json",
                f"score {report['score']} · {sum(report['counts'].values())} findings · verified fetch")

    if tool == "audit_artifact":
        path = _built_file(project)
        if not path or not path.exists():
            raise ValueError("no built file found to audit — the build stage must run first")
        report = _audit_local_html(path)
        return (json.dumps(report, ensure_ascii=False, indent=2), "json",
                f"score {report['score']} · {sum(report['counts'].values())} findings · local file")

    if tool == "verify_prospects":
        proposed = ctx.get("backlinks") or {}
        prospects = proposed.get("prospects", []) if isinstance(proposed, dict) else []
        if not prospects:
            raise ValueError("no prospects were proposed to verify")
        verified, discarded = [], []
        for p in prospects[:15]:
            domain = str(p.get("domain", "")).strip().lower().replace("https://", "").replace("http://", "").strip("/")
            if not domain:
                continue
            res = audit.fetch(f"https://{domain}", timeout=12)
            if res["ok"] and res["status"] < 400:
                p["verified"] = True
                p["verified_status"] = res["status"]
                verified.append(p)
                try:
                    agency.backlink_create({
                        "domain": domain, "client_id": project.get("client_id") or "",
                        "kind": p.get("kind", "other"), "url": res["final_url"],
                        "notes": p.get("why_relevant", ""), "draft": p.get("draft", ""),
                    })
                except Exception:
                    pass  # duplicate or invalid kind — verification result still reported
            else:
                p["verified"] = False
                p["verified_status"] = res.get("status", 0)
                discarded.append(p)
        out = {"verified": verified, "discarded": discarded,
               "note": "Each domain was fetched over HTTPS. Unreachable domains are discarded, not contacted. "
                       "Verified prospects were added to the backlink CRM at status 'identified'; "
                       "no message is sent without a separate human approval."}
        return (json.dumps(out, ensure_ascii=False, indent=2), "json",
                f"{len(verified)} reachable · {len(discarded)} discarded")

    if tool == "visual_qa":
        # Screenshot the page we just built, at the widths people actually use.
        path = _built_file(project)
        if not path or not path.exists():
            raise ValueError("no built file to capture — the build stage must run first")
        shots_dir = _project_dir(project) / "screenshots"
        shots = browser.capture_set(browser.file_url(path), shots_dir,
                                    viewports=("desktop", "tablet", "mobile"))
        served = path.read_text(encoding="utf-8", errors="replace")
        gap = audit.render_gap(browser.file_url(path), served)

        # Measure the layout, do not merely photograph it: a screenshot shows a
        # heading running off the edge, the probe says which element and by how
        # many pixels, which is what the repair stage needs to act on.
        layout: list[dict[str, Any]] = []
        probes: list[dict[str, Any]] = []
        for viewport in ("mobile", "tablet", "desktop"):
            try:
                probe = browser.layout_probe(path, viewport=viewport)
            except browser.BrowserError as exc:
                layout.append({"severity": "low", "area": "layout",
                               "title": f"Layout probe failed at {viewport}", "detail": str(exc), "fix": ""})
                continue
            probes.append(probe)
            layout.extend(browser.layout_findings(probe))

        defects = [f for f in layout + gap["findings"] if f["severity"] != "good"]
        out = {
            "screenshots": [{"viewport": s["viewport"], "path": s["path"],
                             "image_width": s.get("image_width", s["width"]),
                             "content_width": s.get("content_width", s["width"]),
                             "height": s["height"], "bytes": s["bytes"], "note": s.get("note", "")}
                            for s in shots],
            "layout": {"probes": probes, "findings": layout},
            "render_check": gap,
            "defects": defects,
            "note": "Screenshots and measurements come from the local browser rendering the built file. "
                    "Layout numbers are measured inside a frame of the exact target width, so narrow "
                    "widths are real rather than a crop of a wider layout.",
        }
        worst = min((f["severity"] for f in defects),
                    key=lambda s: audit.SEV_ORDER.get(s, 9), default="none")
        return (json.dumps(out, ensure_ascii=False, indent=2), "json",
                f"{len(shots)} viewports · {len(defects)} defect(s) · worst: {worst}")

    if tool == "render_check":
        url = (project.get("url") or "").strip()
        if not url:
            raise ValueError("this project has no URL to render — set the project URL first")
        page = audit.fetch(url)
        if not page["ok"]:
            raise ValueError(f"could not fetch {url} — {page.get('error', 'unknown error')}")
        gap = audit.render_gap(page["final_url"], page["body"])
        shots_dir = _project_dir(project) / "screenshots"
        try:
            shots = browser.capture_set(page["final_url"], shots_dir, viewports=("desktop", "mobile"))
            gap["screenshots"] = [{"viewport": s["viewport"], "path": s["path"]} for s in shots]
        except browser.BrowserError as exc:
            # The comparison is the deliverable; a failed screenshot is noted,
            # not fatal.
            gap["screenshots"] = []
            gap["screenshot_error"] = str(exc)
        worst = min((f["severity"] for f in gap["findings"]),
                    key=lambda s: audit.SEV_ORDER.get(s, 9), default="good")
        return (json.dumps(gap, ensure_ascii=False, indent=2), "json",
                f"rendered in {gap['render_ms']}ms · worst finding: {worst}")

    if tool == "verify_repair":
        # Prove the repair helped. If it did not, put the previous build back:
        # a stage that can silently make the deliverable worse is not a fix.
        path = _built_file(project)
        backup = _project_dir(project) / "index.before-repair.html"
        if not path or not path.exists():
            raise ValueError("no built file to verify")
        if not backup.exists():
            raise ValueError("no pre-repair build to compare against")

        def measure(target: Path) -> dict[str, Any]:
            report = _audit_local_html(target)
            defects: list[dict[str, Any]] = []
            for viewport in ("mobile", "tablet", "desktop"):
                try:
                    probe = browser.layout_probe(target, viewport=viewport)
                except browser.BrowserError:
                    continue
                defects.extend(f for f in browser.layout_findings(probe) if f["severity"] != "good")
            return {"score": report["score"], "audit_defects": [f for f in report["findings"]
                                                               if f["severity"] != "good"],
                    "layout_defects": defects, "bytes": target.stat().st_size}

        after = measure(path)
        before = measure(backup)
        after_total = len(after["audit_defects"]) + len(after["layout_defects"])
        before_total = len(before["audit_defects"]) + len(before["layout_defects"])
        shrank = after["bytes"] < before["bytes"] * 0.7

        reverted = False
        if after["score"] < before["score"] or after_total > before_total or shrank:
            path.write_text(backup.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            reverted = True

        shots_dir = _project_dir(project) / "screenshots"
        try:
            browser.capture_set(browser.file_url(path), shots_dir,
                                viewports=("desktop", "tablet", "mobile"))
        except browser.BrowserError:
            pass  # verification verdict stands even if re-capture fails

        out = {
            "before": {"score": before["score"], "defects": before_total, "bytes": before["bytes"]},
            "after": {"score": after["score"], "defects": after_total, "bytes": after["bytes"]},
            "reverted": reverted,
            "remaining_defects": (before if reverted else after)["audit_defects"]
                                 + (before if reverted else after)["layout_defects"],
            "verdict": ("Repair made the page worse or dropped content; the previous build was restored."
                        if reverted else
                        f"Repair accepted: score {before['score']} to {after['score']}, "
                        f"defects {before_total} to {after_total}."),
            "note": "Measured with the same audit and layout probe used before the repair.",
        }
        return (json.dumps(out, ensure_ascii=False, indent=2), "json",
                ("reverted — repair rejected" if reverted else
                 f"accepted · score {before['score']}→{after['score']} · defects {before_total}→{after_total}"))

    if tool == "measure_keywords":
        # The agent proposes terms and is forbidden to invent numbers for them.
        # This is where the numbers actually come from.
        import dataforseo
        proposed = ctx.get("keywords") or {}
        clusters = proposed.get("clusters", []) if isinstance(proposed, dict) else []
        terms: list[str] = []
        for cluster in clusters:
            for kw in (cluster.get("keywords") or []):
                term = str(kw).strip()
                if term and term.lower() not in {t.lower() for t in terms}:
                    terms.append(term)
        for quick in (proposed.get("quick_wins") or []) if isinstance(proposed, dict) else []:
            term = str(quick.get("keyword", "")).strip()
            if term and term.lower() not in {t.lower() for t in terms}:
                terms.append(term)
        if not terms:
            raise ValueError("the keyword stage proposed no terms to measure")

        st = dataforseo.status()
        if not st.get("ok"):
            # Not a failure of the run: say plainly that the numbers are absent
            # and why, rather than letting anything downstream invent them.
            out = {
                "measured": False,
                "reason": st.get("error", "DataForSEO is not connected"),
                "terms_proposed": len(terms),
                "note": "Keyword terms stand, but volume, difficulty and CPC are unavailable. "
                        "Connect DataForSEO to measure them. No figures were estimated.",
            }
            return (json.dumps(out, ensure_ascii=False, indent=2), "json",
                    f"{len(terms)} terms proposed · not measured (source not connected)")

        capped = terms[:dataforseo.MAX_KEYWORDS]
        volumes = dataforseo.search_volume(capped)
        cost = volumes["cost_usd"]
        by_term = {str(v["keyword"]).lower(): v for v in volumes["keywords"] if v.get("keyword")}
        difficulty: dict[str, Any] = {}
        try:
            diff = dataforseo.keyword_difficulty(capped)
            cost += diff["cost_usd"]
            difficulty = {str(x["keyword"]).lower(): x.get("difficulty")
                          for x in diff["keywords"] if x.get("keyword")}
        except dataforseo.DataForSEOError:
            pass

        measured = []
        for term in capped:
            hit = by_term.get(term.lower(), {})
            measured.append({
                "keyword": term,
                "volume": hit.get("volume"),
                "difficulty": difficulty.get(term.lower()),
                "cpc": hit.get("cpc"),
                "competition": hit.get("competition"),
                "provenance": "imported" if hit else "unmeasured",
                "source": "dataforseo" if hit else "",
            })
        # Store them against the client so the SEO workspace shows real numbers.
        stored = 0
        client_id = project.get("client_id") or ""
        if client_id:
            existing = {k["keyword"].lower() for k in agency.keywords_list(client_id)}
            for row in measured:
                if row["provenance"] != "imported" or row["keyword"].lower() in existing:
                    continue
                try:
                    agency.keyword_create({
                        "keyword": row["keyword"], "client_id": client_id,
                        "volume": row["volume"], "difficulty": row["difficulty"],
                        "source": "dataforseo", "provenance": "imported",
                    })
                    stored += 1
                except Exception:
                    pass  # a duplicate or invalid row must not fail the stage

        with_volume = [m for m in measured if m["volume"] is not None]
        out = {
            "measured": True,
            "keywords": measured,
            "terms_proposed": len(terms),
            "terms_measured": len(capped),
            "with_volume": len(with_volume),
            "stored_against_client": stored,
            "cost_usd": round(cost, 4),
            "note": "Volume, difficulty and CPC are measured by DataForSEO and carry provenance "
                    "'imported'. Terms with no row returned are marked 'unmeasured' rather than "
                    "given a guessed figure.",
        }
        return (json.dumps(out, ensure_ascii=False, indent=2), "json",
                f"{len(with_volume)}/{len(capped)} measured · ${round(cost, 4)} · {stored} stored")

    raise ValueError(f"unknown tool '{tool}'")


def _project_dir(project: dict) -> Path:
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in project["name"].lower())[:40]
    d = WORKSPACE / f"{slug}-{project['id'][:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_file(project: dict, filename: str, content: str) -> str:
    path = _project_dir(project) / filename
    path.write_text(content, encoding="utf-8")
    return str(path)


def _built_file(project: dict) -> Path | None:
    d = _project_dir(project)
    candidate = d / "index.html"
    return candidate if candidate.exists() else None


def _audit_local_html(path: Path) -> dict[str, Any]:
    """Run the same checks against a local build (no network fetch of the page)."""
    html_text = path.read_text(encoding="utf-8", errors="replace")
    p = audit.PageParser()
    p.feed(html_text)
    findings: list[dict[str, str]] = []
    f = audit._f
    if not p.title:
        findings.append(f("critical", "on-page", "Missing <title>", "No title element in the build.", "Add a title."))
    else:
        n = len(p.title)
        findings.append(f("good" if 15 <= n <= 65 else "medium", "on-page",
                          f"Title is {n} characters", p.title[:120],
                          "" if 15 <= n <= 65 else "Aim for 50–60 characters."))
    desc = p.meta.get("description", "")
    findings.append(f("good", "on-page", f"Meta description is {len(desc)} characters", desc[:160])
                    if desc else f("high", "on-page", "Missing meta description", "None in the build.",
                                   "Add a 140–160 character description."))
    h1s = [t for lvl, t in p.headings if lvl == 1]
    findings.append(f("good", "on-page", "Single H1", h1s[0][:120]) if len(h1s) == 1 else
                    f("high", "on-page", f"{len(h1s)} H1 headings", "Exactly one is required.", "Fix heading structure."))
    findings.append(f("good", "technical", "Viewport meta present", "Mobile rendering declared.")
                    if p.has_viewport else
                    f("high", "technical", "No viewport meta", "Mobile rendering unreliable.", "Add the viewport meta."))
    findings.append(f("good", "technical", "lang declared", p.lang) if p.lang else
                    f("medium", "technical", "No lang attribute", "Language not declared.", 'Add lang="en".'))
    findings.append(f("good", "technical", "Canonical declared", p.canonical) if p.canonical else
                    f("medium", "technical", "No canonical link", "Add a self-referencing canonical.", ""))
    types = audit.schema_types(p.jsonld)
    findings.append(f("good", "schema", f"Structured data: {', '.join(types)}", f"{len(p.jsonld)} block(s).")
                    if p.jsonld else
                    f("high", "schema", "No JSON-LD in the build", "Nothing for rich results or AI citation.",
                      "Embed the JSON-LD produced by the schema stage."))
    aeo = {"FAQPage", "QAPage", "HowTo"} & set(types)
    findings.append(f("good", "aeo", "Answer-engine schema present", ", ".join(sorted(aeo))) if aeo else
                    f("medium", "aeo", "No FAQ/HowTo schema", "Answer engines prefer marked Q&A.", "Add FAQPage."))
    q_h = [t for _, t in p.headings if t.strip().endswith("?") or t.lower().startswith(audit.QUESTION_WORDS)]
    findings.append(f("good", "aeo", f"{len(q_h)} question-shaped heading(s)", "; ".join(h[:50] for h in q_h[:3]))
                    if q_h else
                    f("medium", "aeo", "No question-shaped headings", "Add headings phrased as buyer questions.", ""))
    missing_alt = [i for i in p.images if i.get("alt") is None or not str(i.get("alt")).strip()]
    if p.images:
        findings.append(f("good", "accessibility", "All images have alt text", f"{len(p.images)} checked.")
                        if not missing_alt else
                        f("medium", "accessibility", f"{len(missing_alt)} images lack alt text", "", "Describe each image."))
    if p.og.get("og:title"):
        findings.append(f("good", "geo", "Open Graph tags present", p.og["og:title"][:100]))
    else:
        findings.append(f("low", "geo", "No Open Graph title", "Shared links render uncontrolled.", "Add og: tags."))
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html_text)
    if ext:
        findings.append(f("medium", "performance", f"{len(ext)} external request(s) in the build",
                          "; ".join(ext[:3]), "Inline or self-host assets for an offline-capable page."))
    else:
        findings.append(f("good", "performance", "No external requests", "The page renders standalone."))

    findings.sort(key=lambda x: audit.SEV_ORDER.get(x["severity"], 9))
    counts: dict[str, int] = {}
    for x in findings:
        counts[x["severity"]] = counts.get(x["severity"], 0) + 1
    return {
        "source": "local build", "file": str(path), "bytes": len(html_text),
        "provenance": "verified", "counts": counts, "score": audit.health_score(counts),
        "page": {"title": p.title, "description": desc, "h1": h1s[0] if h1s else "",
                 "schema_types": types, "images": len(p.images), "headings": len(p.headings)},
        "findings": findings,
        "note": "Checks run against the generated file on disk. Live-URL checks (robots, sitemap, HTTPS, "
                "redirects) apply only after deployment.",
    }
