#!/usr/bin/env python3
"""
Mission Control end-to-end feature test.

Exercises every API the UI depends on: reads, write round-trips (create ->
update -> delete), the pipeline engine, the artifact store and the live event
streams. Write tests use clearly-named scratch records and always clean up
after themselves, so running this against the working database is safe.

    python verification/test_mission_control.py            # read + write
    python verification/test_mission_control.py --read-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:51764"
PROJECT_DIR = Path(__file__).resolve().parent.parent
MARK = "ZZ-selftest"            # every scratch record carries this marker

results: list[tuple[str, str, str]] = []   # (status, name, detail)
_created: dict[str, list[str]] = {}        # kind -> ids, for teardown


# ---------------------------------------------------------------- plumbing
def _token() -> str:
    with urllib.request.urlopen(f"{BASE}/api/agency/token", timeout=10) as r:
        return json.load(r)["token"]


TOKEN: str | None = None


def call(method: str, path: str, body: dict | None = None, timeout: int = 30):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if method != "GET":
        # delete endpoints take ?id= and no body, but still require the token
        req.add_header("Content-Type", "application/json")
        if TOKEN:
            req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        ctype = r.headers.get("Content-Type", "")
        if "json" in ctype:
            return r.status, json.loads(raw) if raw else {}
        return r.status, raw


def check(name: str, fn):
    """Run one assertion. fn returns a detail string, or raises."""
    try:
        detail = fn() or ""
        results.append(("PASS", name, str(detail)[:150]))
        return True
    except AssertionError as e:
        results.append(("FAIL", name, str(e)[:300]))
        return False
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "replace")[:200]
        results.append(("FAIL", name, f"HTTP {e.code}: {payload}"))
        return False
    except Exception as e:
        results.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        return False


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- read tests
def test_reads():
    def _serves(path, needle, name=None):
        def run():
            st, body = call("GET", path)
            ok(st == 200, f"status {st}")
            text = body if isinstance(body, str) else json.dumps(body)
            ok(needle in text, f"missing {needle!r} in response")
            return f"{st}, {len(text)} bytes"
        check(name or f"GET {path}", run)

    # --- shells -----------------------------------------------------------
    _serves("/", "Gem Agency", "shell: / serves the Gem Agency UI")
    # The Ledger and Mission Control editions were removed; their paths now
    # fall through to the SPA handler and serve the Quantum shell, so old
    # bookmarks land on the live UI instead of a 404.
    _serves("/ledger", "quantum.css", "shell: retired /ledger falls back to Quantum")
    _serves("/legacy", "quantum.css", "shell: retired /legacy falls back to Quantum")

    # --- static assets the Quantum UI imports -----------------------------
    for asset in [
        "/app/q/css/quantum.css",
        "/app/q/index.html",
        "/app/q/brand/gem-mark.svg",
        "/app/q/js/core.js", "/app/q/js/boot.js", "/app/q/js/routes.js", "/app/q/js/palette.js",
        "/app/q/js/pages/deck.js", "/app/q/js/pages/builder.js",
        "/app/q/js/pages/optimize.js", "/app/q/js/pages/agents.js", "/app/q/js/pages/approvals.js",
        "/app/q/js/pages/ai.js", "/app/q/js/pages/projects.js", "/app/q/js/pages/misc.js",
        "/app/q/js/pages/monitor.js",
    ]:
        def run(a=asset):
            st, body = call("GET", a)
            ok(st == 200, f"status {st}")
            ok(len(body) > 200, "suspiciously small")
            return f"{len(body)} bytes"
        check(f"asset {asset}", run)

    # --- The Agency: specialist profiles ----------------------------------
    def agency_profiles():
        st, b = call("GET", "/api/agency/profiles")
        ok(st == 200 and b.get("ok"), "not ok")
        ok(len(b["agents"]) >= 13, f"only {len(b['agents'])} profiles")
        ok({d["id"] for d in b["divisions"]} >= {"engineering", "design", "marketing", "sales", "product"},
           "a division is missing")
        states = {s for a in b["agents"] for s in a["installed"].values()}
        ok(states <= {"synced", "outdated", "foreign", "missing"}, f"unexpected install states {states}")
        return f"{len(b['agents'])} specialists"
    check("GET /api/agency/profiles", agency_profiles)

    def agency_render():
        st, b = call("GET", "/api/agency/profiles/render?target=claude&name=frontend-developer")
        ok(st == 200 and b["text"].startswith("---\nname: frontend-developer\n"), "bad claude render")
        try:
            call("GET", "/api/agency/profiles/render?target=prompt&name=../etc")
            raise AssertionError("path-like agent name was accepted")
        except urllib.error.HTTPError as e:
            ok(e.code == 400, f"expected 400, got {e.code}")
        return "renders, rejects path-like names"
    check("GET /api/agency/profiles/render", agency_render)

    # --- core agency reads -------------------------------------------------
    def overview():
        st, b = call("GET", "/api/agency/overview")
        ok(st == 200 and b.get("ok"), "not ok")
        for key in ("counts", "clients", "projects", "approvals", "agents",
                    "deadlines", "activity", "attention", "integrations"):
            ok(key in b, f"missing key {key}")
        return (f"{len(b['projects'])} projects, {len(b['clients'])} clients, "
                f"{len(b['agents'])} agents")
    check("GET /api/agency/overview", overview)

    for path, key in [
        ("/api/agency/health", "schema_version"),
        ("/api/agency/clients", "clients"),
        ("/api/agency/projects", "projects"),
        ("/api/agency/approvals", "approvals"),
        ("/api/agency/tasks", "tasks"),
        ("/api/agency/keywords", "keywords"),
        ("/api/agency/backlinks", "prospects"),
        ("/api/agency/competitors", "competitors"),
        ("/api/agency/agents", "agents"),
        ("/api/agency/activity", "activity"),
        ("/api/agency/attention", "attention"),
        ("/api/agency/runs", "runs"),
        ("/api/agency/playbooks", "playbooks"),
        ("/api/agency/pipeline/runs?limit=5", "runs"),
        ("/api/agency/boards", "boards"),
    ]:
        def run(p=path, k=key):
            st, b = call("GET", p)
            ok(st == 200 and b.get("ok"), f"status {st}")
            ok(k in b, f"missing {k}")
            v = b[k]
            return f"{len(v)} rows" if isinstance(v, list) else str(v)
        check(f"GET {path}", run)

    # --- system reads ------------------------------------------------------
    for path in ["/api/health", "/api/status", "/api/snapshot", "/api/sessions",
                 "/api/flight-recorder?limit=5", "/api/bridge/history?limit=5",
                 "/api/agency/gsc/status"]:
        def run(p=path):
            st, b = call("GET", p)
            ok(st == 200, f"status {st}")
            return "ok"
        check(f"GET {path}", run)

    # --- search ------------------------------------------------------------
    def search():
        st, b = call("GET", "/api/agency/search?q=a")
        ok(st == 200 and b.get("ok"), "not ok")
        ok("results" in b, "missing results")
        return f"{len(b['results'])} results"
    check("GET /api/agency/search", search)

    # --- playbook integrity: the UI maps every stage into a phase ---------
    def phases():
        st, b = call("GET", "/api/agency/playbooks")
        known = set()
        core = (PROJECT_DIR / "app/q/js/core.js").read_text(encoding="utf-8")
        block = core.split("export const STAGE_PHASE = {")[1].split("};")[0]
        for part in block.replace("\n", " ").split(","):
            if ":" in part:
                known.add(part.split(":")[0].strip())
        missing = []
        for pb in b["playbooks"]:
            for s in pb["stages"]:
                if s["id"] not in known:
                    missing.append(f"{pb['id']}.{s['id']}")
        ok(not missing, f"stages with no phase mapping: {missing}")
        return f"{len(known)} stage ids mapped, all playbook stages covered"
    check("every playbook stage maps to a UI phase", phases)

    # --- plain-language coverage ------------------------------------------
    def plain_cover():
        st, b = call("GET", "/api/agency/playbooks")
        core = (PROJECT_DIR / "app/q/js/core.js").read_text(encoding="utf-8")
        block = core.split("export const PLAIN = {")[1].split("};")[0]
        named = {p.split(":")[0].strip() for p in block.replace("\n", " ").split(",") if ":" in p}
        missing = [s["id"] for pb in b["playbooks"] for s in pb["stages"] if s["id"] not in named]
        ok(not missing, f"stages with no plain-English name: {missing}")
        return f"{len(named)} stages named"
    check("every stage has a plain-English name", plain_cover)

    # --- artifacts + audit data -------------------------------------------
    def artifacts():
        st, b = call("GET", "/api/agency/projects")
        total, audits, htmls = 0, 0, 0
        for p in b["projects"]:
            st2, b2 = call("GET", f"/api/agency/artifacts?project_id={p['id']}")
            ok(st2 == 200 and b2.get("ok"), f"artifacts failed for {p['id']}")
            arts = b2["artifacts"]
            total += len(arts)
            for a in arts:
                if a["stage_id"] in ("audit", "self_audit"):
                    audits += 1
                if a["kind"] == "html":
                    htmls += 1
        ok(total > 0, "no artifacts at all")
        return f"{total} artifacts, {audits} audits, {htmls} built pages"
    check("artifacts readable for every project", artifacts)

    def audit_shape():
        st, b = call("GET", "/api/agency/projects")
        found = 0
        for p in b["projects"]:
            _, b2 = call("GET", f"/api/agency/artifacts?project_id={p['id']}")
            for a in b2["artifacts"]:
                if a["stage_id"] not in ("audit", "self_audit"):
                    continue
                _, art = call("GET", f"/api/agency/artifact?id={a['id']}")
                d = json.loads(art["artifact"]["content"])
                for k in ("score", "findings", "page", "counts"):
                    ok(k in d, f"audit missing {k}")
                # "site" (robots/sitemap/llms) is only gathered when auditing a
                # live URL; self_audit grades a freshly built page instead.
                if a["stage_id"] == "audit":
                    ok("site" in d, "live-site audit missing site checks")
                for f in d["findings"]:
                    ok(f.get("area"), "finding without area")
                    ok(f.get("severity") in ("high", "medium", "low", "good"),
                       f"bad severity {f.get('severity')}")
                found += 1
        ok(found > 0, "no audit artifacts to validate")
        return f"{found} audits, all with score/findings/areas"
    check("audit artifacts have the shape the UI relies on", audit_shape)

    def raw_artifact():
        st, b = call("GET", "/api/agency/projects")
        for p in b["projects"]:
            _, b2 = call("GET", f"/api/agency/artifacts?project_id={p['id']}")
            for a in b2["artifacts"]:
                if a["kind"] == "html":
                    st3, raw = call("GET", f"/api/agency/artifact/raw?id={a['id']}")
                    ok(st3 == 200, f"raw status {st3}")
                    ok(len(raw) > 100, "raw page too small")
                    return f"{len(raw)} bytes of real HTML"
        return "no built page to serve (not a failure)"
    check("built page serves through artifact/raw", raw_artifact)

    # --- pipeline run detail ----------------------------------------------
    def run_detail():
        st, b = call("GET", "/api/agency/pipeline/runs?limit=5")
        ok(b["runs"], "no runs recorded")
        r = b["runs"][0]
        st2, b2 = call("GET", f"/api/agency/pipeline/run?id={r['id']}")
        ok(st2 == 200 and b2.get("ok"), "run detail failed")
        run = b2["run"]
        ok("stages" in run, "run has no stages")
        return f"run {r['id'][:8]} -> {len(run['stages'])} stages"
    check("pipeline run detail loads with stages", run_detail)

    # --- client report ------------------------------------------------------
    def report():
        st, b = call("GET", "/api/agency/clients")
        if not b["clients"]:
            return "no clients (skipped)"
        cid = b["clients"][0]["id"]
        st2, html = call("GET", f"/api/agency/report.html?client_id={cid}")
        ok(st2 == 200, f"status {st2}")
        ok("<html" in html.lower() or "<!doctype" in html.lower(), "not HTML")
        return f"{len(html)} bytes"
    check("client report renders", report)

    # --- live event streams -------------------------------------------------
    def sse():
        req = urllib.request.Request(BASE + "/events")
        with urllib.request.urlopen(req, timeout=8) as r:
            ok(r.status == 200, f"status {r.status}")
            chunk = r.read(900).decode("utf-8", "replace")
        ok("event:" in chunk and "data:" in chunk, "not an SSE stream")
        return chunk.split("\n")[0][:60]
    check("SSE /events streams", sse)

    def recorder():
        st, b = call("GET", "/api/flight-recorder?limit=20")
        ok(st == 200 and b.get("ok"), "not ok")
        ok("events" in b and "summary" in b, "missing events/summary")
        for e in b["events"]:
            for k in ("id", "ts", "kind", "agent", "status", "title"):
                ok(k in e, f"event missing {k}")
        return f"{len(b['events'])} events, kinds={b['summary'].get('by_kind')}"
    check("flight recorder events have monitor-ready shape", recorder)

    # --- provider-error guard ----------------------------------------------
    # The gateway answers 200 OK with the provider's complaint in the message
    # body. Without this guard a billing 402 is stored as a client deliverable.
    def provider_guard():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_llm", PROJECT_DIR / "llm.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_llm"] = mod
        spec.loader.exec_module(mod)
        must_flag = [
            "Billing or credits exhausted: HTTP 402: This request requires more credits.",
            "OpenRouter reported that billing, credits, or account entitlement is exhausted.",
            "Rate limit exceeded for this key.",
            "No endpoints found for moonshotai/kimi-k3.",
        ]
        must_pass = [   # real agent output that merely contains similar words
            "## 2. Quotable Passage Library\n\nEach quotable block is 40-60 words.",
            "# Fix plan\n\nStep 3: check the billing page renders and credits display.",
            '{"keywords":[{"term":"time zone converter","intent":"informational"}]}',
            "The site has no rate limit issues and ample quota headroom.",
        ]
        for t in must_flag:
            ok(mod._provider_error(t) is not None, f"provider error not detected: {t[:60]}")
        for t in must_pass:
            ok(mod._provider_error(t) is None, f"false positive on real output: {t[:60]}")
        return f"{len(must_flag)} error shapes caught, {len(must_pass)} real outputs untouched"
    check("llm: provider errors never become agent output", provider_guard)

    def no_poisoned_artifacts():
        st, b = call("GET", "/api/agency/projects")
        bad = []
        for p in b["projects"]:
            _, b2 = call("GET", f"/api/agency/artifacts?project_id={p['id']}")
            for a in b2["artifacts"]:
                _, art = call("GET", f"/api/agency/artifact?id={a['id']}")
                head = (art["artifact"]["content"] or "").lstrip()[:200].lower()
                if head.startswith(("billing or credits exhausted", "openrouter reported that",
                                    "rate limit exceeded", "provider error:")):
                    bad.append(f"{p['name']}/{a['stage_id']} ({a['id'][:8]})")
        ok(not bad, f"artifacts holding provider errors instead of output: {bad}")
        return "no stored artifact starts with a provider error"
    check("no artifact contains a provider error as its output", no_poisoned_artifacts)

    # --- negative paths -----------------------------------------------------
    def bad_artifact():
        try:
            call("GET", "/api/agency/artifact?id=does-not-exist")
        except urllib.error.HTTPError as e:
            ok(400 <= e.code < 600, f"odd status {e.code}")
            return f"correctly refused with {e.code}"
        raise AssertionError("missing artifact returned 200")
    check("unknown artifact id is refused", bad_artifact)

    def bad_endpoint():
        try:
            call("GET", "/api/agency/not-a-real-endpoint")
        except urllib.error.HTTPError as e:
            ok(e.code == 404, f"expected 404, got {e.code}")
            return "404"
        raise AssertionError("unknown endpoint returned 200")
    check("unknown agency endpoint returns 404", bad_endpoint)


# ---------------------------------------------------------------- write tests
def test_writes():
    state: dict[str, str] = {}

    def client_crud():
        st, b = call("POST", "/api/agency/clients",
                     {"name": f"{MARK} client", "mrr_cents": 12345, "services": ["seo"]})
        ok(b.get("ok"), "create failed")
        cid = b["client"]["id"]
        state["client"] = cid
        _created.setdefault("clients", []).append(cid)
        st, b = call("POST", f"/api/agency/clients/update?id={cid}", {"mrr_cents": 54321})
        ok(b.get("ok"), "update failed")
        _, ov = call("GET", "/api/agency/clients")
        row = [c for c in ov["clients"] if c["id"] == cid][0]
        ok(row["mrr_cents"] == 54321, f"update not persisted: {row['mrr_cents']}")
        return f"created + updated {cid[:8]}"
    check("clients: create -> update -> read back", client_crud)

    def project_crud():
        st, b = call("POST", "/api/agency/projects", {
            "name": f"{MARK} project", "type": "seo_campaign",
            "url": "https://example.com", "brief": "self test",
            "client_id": state.get("client", ""),
        })
        ok(b.get("ok"), "create failed")
        pid = b["project"]["id"]
        state["project"] = pid
        _created.setdefault("projects", []).append(pid)
        st, b = call("POST", f"/api/agency/projects/update?id={pid}",
                     {"health": "at_risk", "due_date": "2026-12-31"})
        ok(b.get("ok"), "update failed")
        _, pl = call("GET", "/api/agency/projects")
        row = [p for p in pl["projects"] if p["id"] == pid][0]
        ok(row["health"] == "at_risk", f"health not persisted: {row['health']}")
        ok(row["due_date"] == "2026-12-31", "due date not persisted")
        return f"created + updated {pid[:8]}"
    check("projects: create -> update -> read back", project_crud)

    def task_crud():
        st, b = call("POST", "/api/agency/tasks", {
            "title": f"{MARK} task", "project_id": state.get("project", ""),
            "priority": "high", "assignee": "selftest"})
        ok(b.get("ok"), "create failed")
        tid = b["task"]["id"]
        _created.setdefault("tasks", []).append(tid)
        st, b = call("POST", f"/api/agency/tasks/update?id={tid}", {"status": "done"})
        ok(b.get("ok"), "update failed")
        _, tl = call("GET", "/api/agency/tasks")
        row = [t for t in tl["tasks"] if t["id"] == tid][0]
        ok(row["status"] == "done", f"status not persisted: {row['status']}")
        return f"created + closed {tid[:8]}"
    check("tasks: create -> complete -> read back", task_crud)

    def keyword_crud():
        st, b = call("POST", "/api/agency/keywords", {
            "keyword": f"{MARK} keyword", "intent": "informational",
            "provenance": "estimated", "source": "self test",
            "client_id": state.get("client", "")})
        ok(b.get("ok"), "create failed")
        kid = b["keyword"]["id"]
        _created.setdefault("keywords", []).append(kid)
        _, kl = call("GET", "/api/agency/keywords")
        row = [k for k in kl["keywords"] if k["id"] == kid][0]
        ok(row["provenance"] == "estimated",
           f"provenance not persisted: {row['provenance']!r}")
        return f"created {kid[:8]}, provenance={row['provenance']!r}"
    check("keywords: create requires and persists provenance", keyword_crud)

    def keyword_needs_provenance():
        try:
            call("POST", "/api/agency/keywords", {"keyword": f"{MARK} no-prov"})
        except urllib.error.HTTPError as e:
            ok(400 <= e.code < 600, f"odd status {e.code}")
            return f"unlabelled keyword refused ({e.code})"
        raise AssertionError("keyword accepted with no provenance label")
    check("keywords: unlabelled keyword is refused", keyword_needs_provenance)

    def approval_flow():
        st, b = call("POST", "/api/agency/approvals", {
            "title": f"{MARK} approval", "kind": "other",
            "detail": "raised by the self test", "project_id": state.get("project", "")})
        ok(b.get("ok"), "create failed")
        aid = b["approval"]["id"]
        _created.setdefault("approvals", []).append(aid)
        _, ov = call("GET", "/api/agency/overview")
        ok(any(a["id"] == aid for a in ov["approvals"]), "pending approval not in overview")
        st, b = call("POST", f"/api/agency/approvals/decide?id={aid}", {"decision": "approved"})
        ok(b.get("ok"), "decide failed")
        _, al = call("GET", "/api/agency/approvals")
        row = [a for a in al["approvals"] if a["id"] == aid][0]
        ok(row["status"] == "approved", f"status is {row['status']}")
        return "raised, surfaced in overview, approved"
    check("approvals: raise -> appears in overview -> decide", approval_flow)

    def approval_rejects_bad_decision():
        st, b = call("POST", "/api/agency/approvals", {"title": f"{MARK} approval 2", "kind": "other"})
        aid = b["approval"]["id"]
        _created.setdefault("approvals", []).append(aid)
        try:
            call("POST", f"/api/agency/approvals/decide?id={aid}", {"decision": "request_changes"})
        except urllib.error.HTTPError as e:
            ok(400 <= e.code < 600, f"odd status {e.code}")
            call("POST", f"/api/agency/approvals/decide?id={aid}", {"decision": "rejected"})
            return f"correctly refused unsupported decision ({e.code})"
        raise AssertionError("backend accepted an unsupported decision value")
    check("approvals: unsupported decision is refused", approval_rejects_bad_decision)

    def competitor_crud():
        st, b = call("POST", "/api/agency/competitors", {
            "domain": "selftest-example.com", "name": f"{MARK} competitor",
            "client_id": state.get("client", "")})
        ok(b.get("ok"), "create failed")
        cid = b["competitor"]["id"]
        _created.setdefault("competitors", []).append(cid)
        return f"created {cid[:8]}"
    check("competitors: create", competitor_crud)

    def backlink_crud():
        st, b = call("POST", "/api/agency/backlinks", {
            "domain": "selftest-prospect.com", "kind": "guest_post",
            "client_id": state.get("client", "")})
        ok(b.get("ok"), "create failed")
        bid = b["prospect"]["id"]
        _created.setdefault("backlinks", []).append(bid)
        st, b = call("POST", f"/api/agency/backlinks/update?id={bid}", {"status": "qualified"})
        ok(b.get("ok"), "update failed")
        return f"created + qualified {bid[:8]}"
    check("backlinks: create -> move stage", backlink_crud)

    def board_crud():
        st, b = call("POST", "/api/agency/boards", {"name": f"{MARK} board", "layout": []})
        ok(b.get("ok"), "create failed")
        bid = b["board"]["id"]
        _created.setdefault("boards", []).append(bid)
        return f"created {bid[:8]}"
    check("boards: create", board_crud)

    def pipeline_validation():
        try:
            call("POST", "/api/agency/pipeline/start", {"name": f"{MARK} run"})
        except urllib.error.HTTPError as e:
            ok(400 <= e.code < 600, f"odd status {e.code}")
            return f"missing playbook correctly refused ({e.code})"
        raise AssertionError("pipeline started without a playbook")
    check("pipeline: start without playbook is refused", pipeline_validation)

    def bridge_validation():
        try:
            call("POST", "/api/bridge/send", {"target": "@not-an-agent", "message": "hi"})
        except urllib.error.HTTPError as e:
            ok(400 <= e.code < 600, f"odd status {e.code}")
            return f"invalid agent target refused ({e.code})"
        raise AssertionError("bridge accepted an invalid target")
    check("bridge: invalid agent target is refused", bridge_validation)

    def auth_required():
        req = urllib.request.Request(
            BASE + "/api/agency/clients",
            data=json.dumps({"name": f"{MARK} unauthorised"}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            ok(e.code in (401, 403), f"expected 401/403, got {e.code}")
            return f"unauthenticated write refused ({e.code})"
        raise AssertionError("unauthenticated write was accepted")
    check("security: writes require the bearer token", auth_required)


# ---------------------------------------------------------------- teardown
def teardown():
    plan = [
        ("tasks", "/api/agency/tasks/delete?id="),
        ("keywords", "/api/agency/keywords/delete?id="),
        ("competitors", "/api/agency/competitors/delete?id="),
        ("backlinks", "/api/agency/backlinks/delete?id="),
        ("projects", "/api/agency/projects/delete?id="),
        ("clients", "/api/agency/clients/delete?id="),
        ("boards", "/api/agency/boards/delete?id="),
    ]
    removed, failed = 0, []
    for kind, path in plan:
        for _id in _created.get(kind, []):
            try:
                call("POST", path + _id, {})
                removed += 1
            except Exception as e:
                failed.append(f"{kind}:{_id[:8]} ({type(e).__name__})")
    # Approvals are an audit trail, so there is no delete endpoint. The scratch
    # ones are still fabricated history, so clear them at the source rather than
    # leaving invented rows in a system whose whole point is honest records.
    left = _created.get("approvals", [])
    if left:
        try:
            import sqlite3
            conn = sqlite3.connect(PROJECT_DIR / "agency.db")
            q = ",".join("?" * len(left))
            conn.execute(f"DELETE FROM approvals WHERE id IN ({q})", left)
            conn.execute(
                f"DELETE FROM activity_log WHERE entity_type='approval' AND entity_id IN ({q})", left)
            conn.commit()
            conn.close()
            removed += len(left)
            left = []
        except Exception as e:
            failed.append(f"approvals ({type(e).__name__}: {e})")
    detail = f"removed {removed} scratch records"
    if left:
        detail += f"; {len(left)} approval(s) still present"
    if failed:
        results.append(("FAIL", "teardown: scratch records removed", "; ".join(failed)))
    else:
        results.append(("PASS", "teardown: scratch records removed", detail))


# ---------------------------------------------------------------- main
def main() -> int:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--read-only", action="store_true", help="skip write round-trips")
    args = ap.parse_args()

    try:
        TOKEN = _token()
    except Exception as e:
        print(f"cannot reach Mission Control at {BASE}: {e}")
        return 2

    t0 = time.time()
    test_reads()
    if not args.read_only:
        test_writes()
        teardown()

    width = max(len(n) for _, n, _ in results) + 2
    print()
    for status, name, detail in results:
        mark = "PASS" if status == "PASS" else "FAIL"
        print(f"[{mark}] {name.ljust(width)} {detail}")

    passed = sum(1 for s, _, _ in results if s == "PASS")
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed, in {time.time() - t0:.1f}s")
    if failed:
        print("\nFAILURES")
        for s, n, d in results:
            if s == "FAIL":
                print(f"  - {n}: {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
