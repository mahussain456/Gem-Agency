// ============================================================
// CORE — API layer, shared store, derivations, components, icons.
//
// Data honesty rule enforced here: every derived number traces to a
// row the backend actually stores. Where no source exists (MRR history,
// deployments, page thumbnails) the helpers return null and the UI is
// required to render an explicit "no source" state rather than a guess.
// ============================================================

/* ---------------- API ---------------- */
let _token = null;
export async function token() {
  if (_token) return _token;
  const r = await fetch("/api/agency/token");
  if (!r.ok) throw new Error("token fetch failed: " + r.status);
  _token = (await r.json()).token;
  return _token;
}
export async function apiGet(path) {
  const r = await fetch(path);
  const b = await r.json().catch(() => ({}));
  if (!r.ok || b.ok === false) throw new Error(b.error || `GET ${path} → ${r.status}`);
  return b;
}
export async function apiPost(path, data = {}) {
  const t = await token();
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` },
    body: JSON.stringify(data),
  });
  const b = await r.json().catch(() => ({}));
  if (!r.ok || b.ok === false) throw new Error(b.error || `POST ${path} → ${r.status}`);
  return b;
}

/* ---------------- text ---------------- */
/* Older rows were written UTF-8 and read back as cp1252, leaving "â€”"
   where an em dash belongs. Repair on the way to the screen. */
const MOJI = [["â€”", "—"], ["â€“", "–"], ["â€™", "’"], ["â€œ", "“"], ["â€", "”"],
              ["Â·", "·"], ["â€¦", "…"], ["Â ", " "]];
export function fix(s) {
  s = String(s == null ? "" : s);
  for (const p of MOJI) s = s.split(p[0]).join(p[1]);
  return s;
}
export function esc(s) {
  return fix(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
export const money = c => "$" + ((c || 0) / 100).toLocaleString(undefined, { maximumFractionDigits: 0 });
export function moneyShort(c) {
  const v = (c || 0) / 100;
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1e4) return "$" + (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
export function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return "unknown";
  if (s < 45) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : d + "d ago";
}
export function dur(seconds) {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
export function daysUntil(dateStr) {
  if (!dateStr) return null;
  const d = new Date(String(dateStr) + "T00:00:00");
  if (isNaN(d)) return null;
  return Math.round((d.getTime() - new Date().setHours(0, 0, 0, 0)) / 86400000);
}
export const titleCase = s => fix(s).replace(/[_-]/g, " ").replace(/\b\w/g, c => c.toUpperCase());

/* ---------------- icons (inline, no network) ---------------- */
const P = {
  deck: "M3 3h7v8H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 15h7v6H3z",
  ai: "M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1M12 9a3 3 0 100 6 3 3 0 000-6z",
  check: "M20 6L9 17l-5-5",
  approve: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11",
  task: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11",
  proj: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z",
  build: "M14.7 6.3a4 4 0 01-5 5L4 17v3h3l5.7-5.7a4 4 0 015-5z",
  design: "M12 19l7-7 3 3-7 7-3-3zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5zM2 2l7.6 7.6",
  eye: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zM12 15a3 3 0 100-6 3 3 0 000 6z",
  rocket: "M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2 0-2.8a2 2 0 00-3 0zM12 15l-3-3a22 22 0 015-8 9 9 0 016-2 9 9 0 01-2 6 22 22 0 01-8 5z",
  seo: "M11 3a8 8 0 100 16 8 8 0 000-16zM21 21l-4.3-4.3",
  key: "M21 2l-2 2m-7.6 7.6a5 5 0 11-7 7 5 5 0 017-7zm0 0L15 8m0 0l3 3 3-3-3-3",
  doc: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
  users: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM23 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8",
  link: "M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7l1.7-1.7",
  chart: "M18 20V10M12 20V4M6 20v-6",
  bot: "M12 8V4H8M4 8h16v12H4zM2 14h2M20 14h2M9 13v2M15 13v2",
  bolt: "M13 2L3 14h9l-1 8 10-12h-9z",
  gear: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H2a2 2 0 110-4h.09A1.65 1.65 0 003.6 8a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H8a1.65 1.65 0 001-1.51V2a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V8a1.65 1.65 0 001.51 1H22a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z",
  plug: "M9 2v6M15 2v6M6 8h12v4a6 6 0 01-12 0zM12 18v4",
  bell: "M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0",
  search: "M11 3a8 8 0 100 16 8 8 0 000-16zM21 21l-4.3-4.3",
  plus: "M12 5v14M5 12h14",
  alert: "M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01",
  clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2",
  cal: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z",
  arrowR: "M5 12h14M12 5l7 7-7 7",
  chevL: "M15 18l-6-6 6-6",
  ext: "M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0114.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0020.5 15",
  x: "M18 6L6 18M6 6l12 12",
  play: "M5 3l14 9-14 9z",
  pause: "M6 4h4v16H6zM14 4h4v16h-4z",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  db: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  money: "M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6",
  globe: "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3a14 14 0 010 18 14 14 0 010-18z",
  layers: "M12 2L2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2M5.5 5.5h13L22 12v6a2 2 0 01-2 2H4a2 2 0 01-2-2v-6z",
  filter: "M22 3H2l8 9.5V19l4 2v-8.5z",
  trend: "M23 6l-9.5 9.5-5-5L1 18M17 6h6v6",
  menu: "M3 12h18M3 6h18M3 18h18",
  panel: "M3 3h18v18H3zM15 3v18",
  cursor: "M4 3l6.5 17 2.4-7.1L20 10.5zM13 13l6 6",
  office: "M4 21V5l8-3 8 3v16M2 21h20M9 21v-4h6v4M8 8h2M14 8h2M8 12h2M14 12h2",
  image: "M5 3h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21",
};
export function icon(name, cls = "") {
  const d = P[name] || P.deck;
  return `<svg viewBox="0 0 24 24" class="${cls}" aria-hidden="true">${
    d.split("M").filter(Boolean).map(seg => `<path d="M${seg}"/>`).join("")}</svg>`;
}

/* ---------------- shared store ---------------- */
export const store = {
  overview: null, snapshot: null, runs: [], playbooks: {}, engine: null, profiles: null,
  audits: new Map(),           // project_id -> parsed audit artifact (verified data)
  artifactLists: new Map(),    // project_id -> artifact rows (see artifactsFor)
  agentRuns: [], loadedAt: null, error: null,
};

/** Artifact lists per project, cached. The deck re-renders every 30s and the
    preview + content views both need these; without a cache that is an N+1
    against the artifacts endpoint on every tick. */
export async function artifactsFor(projectId, { fresh = false } = {}) {
  if (!fresh && store.artifactLists.has(projectId)) return store.artifactLists.get(projectId);
  try {
    const rows = (await apiGet(`/api/agency/artifacts?project_id=${encodeURIComponent(projectId)}`)).artifacts || [];
    store.artifactLists.set(projectId, rows);
    return rows;
  } catch (e) {
    store.artifactLists.set(projectId, []);
    return [];
  }
}
/** Drop the caches that a completed pipeline run would invalidate. */
export function invalidateArtifacts(projectId) {
  if (projectId) { store.artifactLists.delete(projectId); store.audits.delete(projectId); }
  else { store.artifactLists.clear(); store.audits.clear(); }
}
const subs = new Set();
export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
function emit() { subs.forEach(f => { try { f(store); } catch (e) { console.error("subscriber failed", e); } }); }

export async function refresh() {
  const [ov, snap, runs, pb] = await Promise.allSettled([
    apiGet("/api/agency/overview"),
    apiGet("/api/snapshot"),
    apiGet("/api/agency/pipeline/runs?limit=60"),
    apiGet("/api/agency/playbooks"),
  ]);
  if (ov.status === "fulfilled") store.overview = ov.value;
  if (snap.status === "fulfilled") store.snapshot = snap.value;
  if (runs.status === "fulfilled") {
    const next = runs.value.runs || [];
    // A run that advanced has written new artifacts — drop that project's
    // cached lists so the preview, content and audit views pick them up.
    const before = new Map(store.runs.map(r => [r.id, `${r.state}:${r.stage_index}`]));
    next.forEach(r => {
      const was = before.get(r.id);
      if (was !== undefined && was !== `${r.state}:${r.stage_index}`) invalidateArtifacts(r.project_id);
    });
    store.runs = next;
  }
  if (pb.status === "fulfilled") {
    store.playbooks = {};
    (pb.value.playbooks || []).forEach(p => { store.playbooks[p.id] = p; });
    store.engine = pb.value.engine || null;
  }
  store.error = ov.status === "rejected" ? String(ov.reason && ov.reason.message || ov.reason) : null;
  store.loadedAt = new Date();
  emit();
  return store;
}

/* Audit artifacts carry the only verified quality data in the system.
   Loaded lazily and cached; callers await this before rendering health. */
export async function loadAudits(projectIds) {
  const want = (projectIds || []).filter(id => id && !store.audits.has(id));
  await Promise.all(want.map(async id => {
    try {
      const arts = await artifactsFor(id);
      /* Prefer a live-site "audit": it carries the site-level checks (robots,
         sitemap, llms.txt) that "self_audit" — which grades a page the agents
         just built — has no way to gather. Fall back to self_audit only. */
      const audit = arts.filter(a => a.stage_id === "audit").pop()
        || arts.filter(a => a.stage_id === "self_audit").pop();
      if (!audit) { store.audits.set(id, null); return; }
      const full = audit.content != null ? audit
        : (await apiGet(`/api/agency/artifact?id=${encodeURIComponent(audit.id)}`)).artifact;
      store.audits.set(id, { ...JSON.parse(full.content), _artifact_id: audit.id, _at: audit.created_at });
    } catch (e) { store.audits.set(id, null); }
  }));
  return store.audits;
}

/* ---------------- pipeline phase model ----------------
   Both shipped playbooks map onto one plain-language spine so a
   non-technical reader can place any job at a glance.            */
export const STAGE_PHASE = {
  audit: "discovery", fix_plan: "discovery", positioning: "discovery", research: "discovery",
  keywords: "strategy", content_plan: "strategy", aeo_geo: "strategy", sitemap: "strategy", seo_plan: "strategy",
  backlinks: "building", verify_prospects: "building", copy: "building", schema: "building", build: "building",
  repair: "building",
  outreach_gate: "review", review_gate: "review", self_audit: "review",
  visual_qa: "review", verify_repair: "review", render_check: "discovery",
  measurement: "live", launch_plan: "live", measure_keywords: "live",
};
export const PLAIN = {
  audit: "Health check of the live site",
  fix_plan: "Fixes worth making",
  keywords: "What people actually search for",
  content_plan: "What to write, and why",
  aeo_geo: "Getting quoted by AI answers",
  backlinks: "Sites worth a link from",
  verify_prospects: "Verify those sites are real",
  outreach_gate: "Your go-ahead to start outreach",
  measurement: "How we prove it worked",
  positioning: "Who this is for",
  research: "Who else is out there",
  sitemap: "Which pages the site needs",
  copy: "Homepage words",
  seo_plan: "Search setup for every page",
  schema: "Facts machines can read",
  build: "Build the pages",
  review_gate: "Your review before launch",
  self_audit: "Final check of what was built",
  launch_plan: "Launch and ranking plan",
  visual_qa: "Look at the built page in a real browser",
  repair: "Fix what the browser found",
  verify_repair: "Confirm the fix worked",
  render_check: "Check the site renders for real",
  measure_keywords: "Measure the keywords",
};
export const plain = (id, fallback) => PLAIN[id] || fix(fallback || id);

/* Spec status vocabulary, mapped from real run + project state. */
export function projectStatus(p, run, curStage) {
  if (p.status === "archived") return { k: "Archived", c: "t-idle" };
  if (run && run.state === "running") return { k: "Building", c: "t-blue" };
  if (run && run.state === "awaiting_approval") return { k: "Review", c: "t-warn" };
  if (run && (run.state === "failed" || run.state === "stopped")) return { k: "Blocked", c: "t-crit" };
  if (run && run.state === "cancelled") return { k: "Blocked", c: "t-crit" };
  if (run && run.state === "queued") return { k: "Queued", c: "t-blue" };
  if (p.status === "live" && p.url) return { k: "Live", c: "t-ok" };
  if (run && run.state === "completed") return { k: "Build complete", c: "t-ok" };
  if (p.health === "at_risk") return { k: "At risk", c: "t-warn" };
  if (p.health === "blocked") return { k: "Blocked", c: "t-crit" };
  if (p.stage_total && p.stage >= p.stage_total) return { k: "Build complete", c: "t-ok" };
  if (curStage && curStage.kind === "gate") return { k: "Review", c: "t-warn" };
  // status says how it is going; the stage column already says where it is,
  // so the old phase words ("Discovery", "Strategy") no longer repeat here
  if (!run) return { k: "Not started", c: "t-idle" };
  return { k: "In progress", c: "t-blue" };
}

/** Fold a project + its latest run + its playbook into one view model. */
export function shapeProject(p) {
  const book = store.playbooks[p.playbook || p.type];
  const stages = (book && book.stages) || [];
  const run = store.runs.filter(r => r.project_id === p.id)[0] || null;
  // Existing runs retain their recorded stage count when playbooks evolve.
  const total = run?.total || stages.length || p.stage_total || 0;
  // projects.stage is only written when a run completes, and a new project
  // starts at 1. While a run is in flight or has stopped, its stage_runs rows
  // are the truth: counting them keeps a fresh or failed build from claiming
  // "1 of 14 done" before any stage has finished.
  const measuredRun = run && typeof run.done === "number";
  const done = Math.min(measuredRun ? run.done : run?.state === 'completed' ? total :
    p.stage >= total && total > 0 ? total : Math.max(0, (p.stage || 1) - 1), total || 99);
  const failed = !!(run && ["failed", "stopped", "cancelled"].includes(run.state));
  const finished = total > 0 && done >= total;
  const markIx = failed && run && typeof run.stage_index === "number" ? run.stage_index : done;
  const curStage = !finished && stages[markIx] ? stages[markIx] : null;
  const phase = finished ? (p.status === 'live' ? 'live' : p.playbook === 'seo_campaign' ? 'growth' : 'launch review') : curStage ? (STAGE_PHASE[curStage.id] || "building") : "discovery";
  return {
    p, run, stages, total, done, markIx, curStage, phase, failed, finished,
    pct: total ? Math.round((done / total) * 100) : 0,
    status: projectStatus(p, run, curStage),
    // a finished pipeline still has a next step: launching, or measuring
    next: finished ? (p.status === "live" ? "Keep growing" : p.playbook === "seo_campaign" ? "Track results"
                      : "Review & launch") : curStage ? plain(curStage.id, curStage.title) : "Not started",
    agent: curStage ? curStage.agent : "",
  };
}

/* ---------------- derived health from REAL audit findings ----------------
   Areas come straight from audit.py: technical, on-page, aeo, geo, schema,
   performance, accessibility. Score = share of checks in that area that
   passed, so it is always explainable by the findings beneath it.       */
export const AREAS = [
  { id: "technical", name: "Technical" },
  { id: "on-page", name: "On-page" },
  { id: "aeo", name: "AEO" },
  { id: "geo", name: "GEO" },
  { id: "schema", name: "Schema" },
  { id: "performance", name: "Performance" },
  { id: "accessibility", name: "Accessibility" },
];
const WEIGHT = { high: 3, medium: 2, low: 1 };

export function areaHealth(audits) {
  const list = (audits || []).filter(Boolean);
  const out = {};
  AREAS.forEach(a => { out[a.id] = { good: 0, high: 0, medium: 0, low: 0, total: 0, score: null }; });
  list.forEach(a => (a.findings || []).forEach(f => {
    const b = out[f.area];
    if (!b) return;
    b[f.severity] = (b[f.severity] || 0) + 1;
    b.total++;
  }));
  Object.values(out).forEach(b => {
    if (!b.total) return;
    const lost = b.high * WEIGHT.high + b.medium * WEIGHT.medium + b.low * WEIGHT.low;
    const max = b.good * 1 + lost || 1;
    b.score = Math.max(0, Math.min(100, Math.round(100 * (b.good / (b.good + lost / 1.5 || 1)))));
    b.issues = b.high + b.medium + b.low;
    void max;
  });
  return out;
}

/** Every open finding across audits, newest audit first, ranked by severity. */
export function allFindings(auditsByProject, projects) {
  const rank = { high: 0, medium: 1, low: 2 };
  const out = [];
  (projects || []).forEach(p => {
    const a = auditsByProject.get(p.id);
    if (!a) return;
    (a.findings || []).forEach(f => {
      if (f.severity === "good") return;
      out.push({ ...f, project: p, url: a.final_url || a.url, at: a._at });
    });
  });
  return out.sort((x, y) => (rank[x.severity] - rank[y.severity]));
}

/* Agent fleet stats — derived from the agents table + agent_runs history. */
export function agentStats(overview, agentRuns) {
  const agents = (overview && overview.agents) || [];
  const busyStates = ["running", "planning", "waiting", "needs_approval"];
  const active = agents.filter(a => busyStates.includes(a.status));
  const failed = agents.filter(a => a.status === "failed" || a.status === "error");
  const runs = agentRuns || [];
  const done = runs.filter(r => r.finished_at);
  const okRuns = done.filter(r => r.state === "completed");
  const durations = done.map(r => (new Date(r.finished_at) - new Date(r.started_at)) / 1000)
                        .filter(n => isFinite(n) && n >= 0);
  return {
    total: agents.length,
    active: active.length,
    idle: agents.filter(a => a.status === "idle").length,
    failed: failed.length,
    activeList: active,
    runsLogged: runs.length,
    // null when there is no run history to average — never a placeholder number
    avgSeconds: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null,
    successRate: done.length ? Math.round((okRuns.length / done.length) * 100) : null,
  };
}

/* Keyword movement — schema supports it; returns nulls until rows exist. */
export function keywordStats(keywords) {
  const ks = keywords || [];
  const ranked = ks.filter(k => k.position != null && k.position > 0);
  const moved = ranked.filter(k => k.prev_position != null && k.prev_position > 0);
  const delta = k => k.prev_position - k.position;   // positive = improved
  return {
    tracked: ks.length,
    ranked: ranked.length,
    top3: ranked.filter(k => k.position <= 3).length,
    top10: ranked.filter(k => k.position <= 10).length,
    improved: moved.filter(k => delta(k) > 0).length,
    declined: moved.filter(k => delta(k) < 0).length,
    avgMove: moved.length ? +(moved.reduce((s, k) => s + delta(k), 0) / moved.length).toFixed(1) : null,
    movers: moved.sort((a, b) => Math.abs(delta(b)) - Math.abs(delta(a))).slice(0, 8),
  };
}

/* Approval stats for the KPI + Approval Centre. */
export function approvalStats(overview) {
  const pend = (overview && overview.approvals) || [];
  const oldest = pend.reduce((o, a) =>
    !o || new Date(a.created_at) < new Date(o.created_at) ? a : o, null);
  return {
    pending: pend.length,
    blocking: pend.filter(a => a.blocking).length,
    oldest,
    oldestAge: oldest ? ago(oldest.created_at) : null,
  };
}

/* ---------------- The Agency: profiles from agency/*.md ---------------- */
export async function loadProfiles({ fresh = false } = {}) {
  if (store.profiles && !fresh) return store.profiles;
  store.profiles = await apiGet("/api/agency/profiles");
  return store.profiles;
}

/** "scout" -> "Maya Collins", from the live team list; unknown ids pass through. */
export function agentName(id) {
  const a = (store.overview?.agents || []).find(x => x.id === String(id || "").replace(/^@/, ""));
  return a ? a.name : String(id || "");
}

/** Two-letter monogram: "frontend-developer" -> "FD", "Orchestrator" -> "OR". */
export function initials(name) {
  const parts = fix(name).replace(/[_-]+/g, " ").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (/^[A-Z]{2,3}$/.test(parts[0])) return parts[0].slice(0, 2);   // "UI Designer" -> "UI"
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
}

/** Agent monogram tile coloured by division. Runtime (gateway) agents are neutral. */
export function monoAv(name, division = "runtime", { size = "", running = null } = {}) {
  const st = running == null ? "" : `<span class="st ${running ? "run" : ""}"></span>`;
  return `<span class="mono-av ${size}" style="--dv:var(--d-${esc(division)})" title="${esc(name)}">${
    esc(initials(name))}${st}</span>`;
}

/** Pipeline stages as ticks: done / active / human gate / failed / to do. */
export function segs(total, done, { active = false, gateAt = -1, failed = false } = {}) {
  const n = Math.max(0, Math.min(total || 0, 40));
  if (!n) return "";
  return `<span class="segs">${Array.from({ length: n }, (_, i) => {
    const c = i < done ? "d" : i === done ? (failed ? "f" : i === gateAt ? "g" : active ? "a" : "") : "";
    return `<i class="${c}"></i>`;
  }).join("")}</span>`;
}

/* ---------------- components ---------------- */
export function metricCard({ key, value, sub, ink = "var(--blue)", ic = "chart", trend, spark, href, note }) {
  const tr = trend
    ? `<span class="trend ${trend.dir}">${icon("trend")}${esc(trend.text)}</span>` : "";
  const nt = note ? `<span class="prov">${esc(note)}</span>` : "";
  return `<button class="kpi" style="--kpi-ink:${ink}" ${href ? `data-go="${esc(href)}"` : ""}>
    <div class="k">${icon(ic)}${esc(key)}</div>
    <div class="v">${value}</div>
    <div class="c">${tr}${sub ? `<span>${esc(sub)}</span>` : ""}${nt}</div>
    ${spark || ""}
  </button>`;
}

export function ring(score, label, size = 68) {
  if (score == null) {
    return `<div class="ring" style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}"><circle class="tr" cx="${size / 2}" cy="${size / 2}"
        r="${size / 2 - 5}" fill="none" stroke-width="5"/></svg>
      <div class="lbl"><b style="color:var(--tx-3);font-size:13px">—</b><i>${esc(label)}</i></div></div>`;
  }
  const r = size / 2 - 5, C = 2 * Math.PI * r;
  const col = score >= 80 ? "var(--ok)" : score >= 55 ? "var(--warn)" : "var(--crit)";
  return `<div class="ring" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}">
      <circle class="tr" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="5"/>
      <circle class="vl" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}"
        stroke-width="5" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - score / 100)}"/>
    </svg>
    <div class="lbl"><b>${score}</b><i>${esc(label)}</i></div></div>`;
}

/** Sparkline from a real series. Returns "" when there is no series —
    the UI must not draw a shape that implies data it does not have. */
export function spark(values, ink = "var(--elec)") {
  const v = (values || []).filter(n => typeof n === "number" && isFinite(n));
  if (v.length < 2) return "";
  const w = 100, h = 26, min = Math.min(...v), max = Math.max(...v), rng = max - min || 1;
  const pts = v.map((n, i) => `${(i / (v.length - 1)) * w},${h - ((n - min) / rng) * (h - 4) - 2}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts.join(" ")}" fill="none" stroke="${ink}" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

export function emptyState({ ic = "inbox", title, body, cta }) {
  return `<div class="empty"><div class="ei">${icon(ic)}</div><b>${esc(title)}</b>${
    body ? `<div>${esc(body)}</div>` : ""}${cta ? `<div class="cta">${cta}</div>` : ""}</div>`;
}
export const skeleton = (n = 4) =>
  Array.from({ length: n }, (_, i) => `<div class="skel skel-row" style="width:${92 - i * 11}%"></div>`).join("");
export const errBox = e =>
  `<div class="errbox"><b>Could not load this panel</b>${esc(String(e && e.message || e))}</div>`;
export const tag = (text, cls = "t-idle", dot = false) =>
  `<span class="tag ${cls}">${dot ? '<span class="d"></span>' : ""}${esc(text)}</span>`;
export const prog = (pct, cls = "") =>
  `<span class="prog ${cls}"><i style="transform:scaleX(${Math.max(0, Math.min(100, pct || 0)) / 100})"></i></span>`;

/* ---------------- chrome helpers ---------------- */
let toastTimer = null;
export function toast(msg, bad = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast on" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3400);
}

export function modal({ title, fields = [], submitLabel = "Save", note = "", onSubmit }) {
  const root = document.getElementById("modalRoot");
  const ovl = document.createElement("div");
  ovl.className = "ovl";
  ovl.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h3>${esc(title)}</h3><div class="mb">
    ${note ? `<p style="font:400 12.5px/1.5 var(--sans);color:var(--tx-3);margin-bottom:14px">${esc(note)}</p>` : ""}
    ${fields.map(f => `<div class="fld"><label for="f_${f.name}">${esc(f.label)}</label>${
      f.type === "textarea" ? `<textarea id="f_${f.name}" name="${f.name}" placeholder="${esc(f.placeholder || "")}">${esc(f.value || "")}</textarea>`
      : f.type === "select" ? `<select id="f_${f.name}" name="${f.name}">${(f.options || []).map(o =>
          `<option value="${esc(o.value)}" ${o.value === f.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`
      : `<input id="f_${f.name}" name="${f.name}" type="${f.type || "text"}" placeholder="${esc(f.placeholder || "")}" value="${esc(f.value || "")}">`}
      ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ""}</div>`).join("")}
    <div class="merr"></div></div>
    <div class="mf"><button class="btn" data-x>Cancel</button>
    <button class="btn pri" data-ok>${esc(submitLabel)}</button></div></div>`;
  root.appendChild(ovl);
  const close = () => ovl.remove();
  ovl.addEventListener("click", e => { if (e.target === ovl) close(); });
  ovl.querySelector("[data-x]").addEventListener("click", close);
  const first = ovl.querySelector("input,textarea,select");
  if (first) first.focus();
  ovl.querySelector("[data-ok]").addEventListener("click", async () => {
    const btn = ovl.querySelector("[data-ok]");
    const vals = {};
    fields.forEach(f => { vals[f.name] = ovl.querySelector(`[name="${f.name}"]`).value.trim(); });
    const missing = fields.filter(f => f.required && !vals[f.name]);
    if (missing.length) {
      ovl.querySelector(".merr").innerHTML =
        `<div class="errbox"><b>Missing</b>${esc(missing.map(m => m.label).join(", "))}</div>`;
      return;
    }
    btn.disabled = true; btn.textContent = "Working…";
    try { await onSubmit(vals); close(); }
    catch (e) {
      btn.disabled = false; btn.textContent = submitLabel;
      ovl.querySelector(".merr").innerHTML = errBox(e);
    }
  });
  document.addEventListener("keydown", function esckey(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esckey); }
  });
  return ovl;
}

export function drawer({ title, sub, body, footer }) {
  const scrim = document.getElementById("scrim");
  const dr = document.getElementById("drawer");
  dr.innerHTML = `<div class="dr-h"><button class="iconbtn dr-x" data-x aria-label="Close">${icon("x")}</button>
      <h3>${title}</h3>${sub ? `<div class="s">${sub}</div>` : ""}</div>
    <div class="dr-b">${body}</div>
    ${footer ? `<div class="dr-f">${footer}</div>` : ""}`;
  scrim.classList.add("on"); dr.classList.add("on");
  dr.querySelector("[data-x]").addEventListener("click", closeDrawer);
  return dr;
}
export function closeDrawer() {
  document.getElementById("scrim").classList.remove("on");
  document.getElementById("drawer").classList.remove("on");
}

/** Wire [data-go="hash"] anywhere inside a root to the router. */
export function bindGo(root) {
  root.querySelectorAll("[data-go]").forEach(el => {
    if (el._go) return;
    el._go = true;
    el.addEventListener("click", ev => {
      ev.preventDefault();
      location.hash = el.dataset.go;
    });
  });
}
