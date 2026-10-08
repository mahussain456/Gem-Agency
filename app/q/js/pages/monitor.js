// ============================================================
// MONITOR — what is being worked on, right now.
//
// Three live sources, merged:
//   1. pipeline_runs + stage_runs  -> the exact stage executing, and by whom
//   2. /api/flight-recorder        -> agent sessions and bridge commands
//   3. SSE /events                 -> gateway state pushed by the server
//
// Elapsed times tick client-side so a running stage visibly counts up.
// Nothing here is sampled or smoothed: every row is an event the backend
// recorded, and idle is reported as idle.
// ============================================================
import {
  store, apiGet, esc, icon, ago, dur, titleCase, tag, prog, emptyState, errBox,
  skeleton, bindGo, plain, fix, toast,
  agentName,
} from "/app/q/js/core.js";

/* A session must have started within this window to count as live work. */
const FRESH_SESSION_SECONDS = 10 * 60;

/* ---------------- lifecycle ----------------
   The router swaps innerHTML but cannot know about our timers or the SSE
   socket, so the page registers its own teardown. */
let live = null;

function teardown() {
  if (!live) return;
  clearInterval(live.tick);
  clearInterval(live.poll);
  if (live.es) { try { live.es.close(); } catch (e) { /* already closed */ } }
  live = null;
}
export { teardown as monitorTeardown };

/* ---------------- event normalisation ---------------- */
const KIND_META = {
  stage:    { ic: "layers", cls: "t-blue", label: "Pipeline stage" },
  command:  { ic: "send",   cls: "t-cyan", label: "Bridge command" },
  session:  { ic: "ai",     cls: "t-blue", label: "Agent session" },
  activity: { ic: "clock",  cls: "t-idle", label: "Ledger entry" },
  approval: { ic: "approve", cls: "t-warn", label: "Approval" },
};
const STATUS_DOT = {
  running: "working", active: "working", sent: "working", planning: "working",
  done: "done", completed: "done", approved: "done",
  waiting: "waiting", awaiting_approval: "waiting", needs_approval: "waiting",
  failed: "failed", error: "failed", stopped: "failed", cancelled: "failed",
  rejected: "failed",
};

/** Seconds since an ISO string or a float epoch, or null if unparseable. */
function since(ts) {
  if (ts == null) return null;
  const ms = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  if (!isFinite(ms)) return null;
  return Math.max(0, (Date.now() - ms) / 1000);
}
function tsOf(ts) {
  if (ts == null) return 0;
  return typeof ts === "number" ? ts * 1000 : Date.parse(ts) || 0;
}

/* ---------------- data gathering ---------------- */
async function gather() {
  const [runsRes, frRes, actRes, sessRes] = await Promise.allSettled([
    apiGet("/api/agency/pipeline/runs?limit=20"),
    apiGet("/api/flight-recorder?limit=60"),
    apiGet("/api/agency/activity?limit=30"),
    apiGet("/api/sessions"),
  ]);

  const runs = runsRes.status === "fulfilled" ? (runsRes.value.runs || []) : [];
  const fr = frRes.status === "fulfilled" ? frRes.value : { events: [], summary: {} };
  const activity = actRes.status === "fulfilled" ? (actRes.value.activity || []) : [];
  const sessions = sessRes.status === "fulfilled" ? (sessRes.value.sessions || {}) : {};

  /* A session row with no ended_at is not proof of live work: the gateway
     leaks records when a socket drops, so this workspace carries sessions
     that have been "open" for weeks. Only recent ones count as working now;
     the rest are reported separately as stale, which is real signal. */
  const openSessions = (sessions.recent || []).filter(s => !s.ended_at);
  const isFresh = s => { const age = since(s.started_at); return age != null && age < FRESH_SESSION_SECONDS; };
  const activeSessions = openSessions.filter(isFresh);
  const staleSessions = openSessions.filter(s => !isFresh(s));

  // Drill into every run that is mid-flight for its exact current stage.
  const inflight = runs.filter(r => ["running", "awaiting_approval", "queued"].includes(r.state));
  const nowStages = [];
  await Promise.all(inflight.map(async r => {
    try {
      const full = (await apiGet(`/api/agency/pipeline/run?id=${encodeURIComponent(r.id)}`)).run;
      (full.stages || []).forEach(s => {
        if (s.state === "running" || (r.state === "awaiting_approval" && s.state === "waiting")) {
          nowStages.push({ ...s, run: r });
        }
      });
    } catch (e) { /* run finished between calls */ }
  }));

  // Merge everything into one feed.
  const feed = [];
  (fr.events || []).forEach(e => feed.push({
    kind: e.kind, ts: tsOf(e.ts), agent: e.agent || "system",
    status: e.status, title: fix(e.title), detail: fix(e.detail || ""),
    meta: e.artifact ? `model ${e.artifact}` : "",
  }));
  activity.forEach(a => feed.push({
    kind: "activity", ts: tsOf(a.ts), agent: a.actor || "operator",
    status: a.action, title: `${titleCase(a.action)} ${a.entity_type}`,
    detail: "", meta: "",
  }));
  runs.forEach(r => feed.push({
    kind: "stage", ts: tsOf(r.updated_at), agent: r.playbook,
    status: r.state, title: `${r.project_name || "untitled"} — ${r.done}/${r.total} stages`,
    detail: r.error ? fix(r.error).slice(0, 160) : "", meta: "",
    go: `project/${r.project_id}`,
  }));
  feed.sort((a, b) => b.ts - a.ts);

  return { runs, nowStages, feed, fr, sessions, activity, activeSessions, staleSessions };
}

/* ---------------- rendering ---------------- */
export default async function monitor(el) {
  teardown();

  el.innerHTML = `<div class="page">
    <div class="phead">
      <div><h1>Monitor</h1><p>Exactly what is being worked on, as it happens.</p></div>
      <div class="acts">
        <span class="tag t-idle" id="monConn"><span class="d"></span>connecting…</span>
        <button class="btn" id="monPause">${icon("pause")} Pause</button>
      </div>
    </div>
    <div id="monNow"></div>
    <div class="kpis" id="monKpis"></div>
    <div class="grid">
      <section class="panel s8">
        <div class="panel-h">
          <div><h2>Live feed</h2><div class="sub" id="feedSub">merging pipeline, agent and ledger events</div></div>
          <span class="r"><span class="chips" id="feedFilter">
            <button class="chip on" data-f="all">All</button>
            <button class="chip" data-f="stage">Pipeline</button>
            <button class="chip" data-f="command">Commands</button>
            <button class="chip" data-f="session">Sessions</button>
            <button class="chip" data-f="activity">Ledger</button>
          </span></span>
        </div>
        <div class="panel-b flush" id="monFeed" style="max-height:56vh;overflow:auto">${skeleton(6)}</div>
      </section>
      <div class="s4">
        <section class="panel" style="margin-bottom:var(--gap)">
          <div class="panel-h"><div><h2>System</h2><div class="sub">Live health</div></div></div>
          <div class="panel-b flush" id="monSys">${skeleton(4)}</div>
        </section>
        <section class="panel">
          <div class="panel-h"><div><h2>Throughput</h2><div class="sub">From the session log</div></div></div>
          <div class="panel-b" id="monThru">${skeleton(3)}</div>
        </section>
      </div>
    </div>
  </div>`;

  live = { tick: null, poll: null, es: null, paused: false, filter: "all", data: null };

  // --- filters ---
  el.querySelectorAll("#feedFilter .chip").forEach(b => b.addEventListener("click", () => {
    el.querySelectorAll("#feedFilter .chip").forEach(x => x.classList.toggle("on", x === b));
    live.filter = b.dataset.f;
    if (live.data) paintFeed(live.data);
  }));

  // --- pause ---
  const pauseBtn = document.getElementById("monPause");
  pauseBtn.addEventListener("click", () => {
    live.paused = !live.paused;
    pauseBtn.innerHTML = live.paused ? `${icon("play")} Resume` : `${icon("pause")} Pause`;
    toast(live.paused ? "Monitor paused — nothing is lost, the feed just stops moving."
                      : "Monitor live again.");
    if (!live.paused) cycle();
  });

  // --- SSE: gateway pushes state changes without polling ---
  try {
    const es = new EventSource("/events");
    live.es = es;
    es.addEventListener("open", () => setConn(true));
    es.addEventListener("error", () => setConn(false));
    es.addEventListener("snapshot", ev => {
      try { store.snapshot = JSON.parse(ev.data); } catch (e) { /* partial frame */ }
      if (!live || live.paused) return;
      paintSystem(live.data || {});
    });
  } catch (e) { setConn(false); }

  await cycle();
  // Poll the agency side every 4s — finer than the 30s global refresh, because
  // a stage can start and finish inside one global tick.
  live.poll = setInterval(() => { if (!live.paused) cycle(); }, 4000);
  // Tick elapsed timers every second so running work visibly counts up.
  live.tick = setInterval(tickTimers, 1000);

  bindGo(el);
}

function setConn(up) {
  const c = document.getElementById("monConn");
  if (!c) return;
  c.className = "tag " + (up ? "t-ok" : "t-warn");
  c.innerHTML = `<span class="d"></span>${up ? "live" : "reconnecting…"}`;
}

async function cycle() {
  if (!live) return;
  let data;
  try { data = await gather(); }
  catch (e) {
    const f = document.getElementById("monFeed");
    if (f) f.innerHTML = errBox(e);
    return;
  }
  if (!live) return;                      // navigated away mid-request
  live.data = data;
  paintNow(data);
  paintKpis(data);
  paintFeed(data);
  paintSystem(data);
  paintThroughput(data);
  bindGo(document.getElementById("work"));
}

/* ---------------- "right now" ---------------- */
function paintNow(d) {
  const slot = document.getElementById("monNow");
  if (!slot) return;

  const sessionsLive = d.activeSessions || [];

  if (!d.nowStages.length && !sessionsLive.length) {
    slot.innerHTML = `<section class="panel" style="margin-bottom:var(--gap)">
      <div class="panel-b"><div class="allclear" style="padding:20px 18px">
        <span class="ic" style="background:var(--idle-w);color:var(--tx-3)">${icon("clock")}</span>
        <div><b style="color:var(--tx)">Nothing is executing right now.</b>
        <span>No pipeline stage is running and no agent session is open. The moment work starts,
        it appears here with a live timer.</span></div></div></div></section>`;
    return;
  }

  slot.innerHTML = `<section class="panel" style="margin-bottom:var(--gap)">
    <div class="panel-h"><div><h2>Working now</h2>
      <div class="sub">${d.nowStages.length} pipeline stage${d.nowStages.length === 1 ? "" : "s"}
        · ${sessionsLive.length} live agent session${sessionsLive.length === 1 ? "" : "s"}</div></div>
      <span class="r"><span class="live"></span></span></div>
    <div class="panel-b flush">
      ${d.nowStages.map(s => `<div class="agrow" data-go="project/${esc(s.run.project_id)}" style="cursor:pointer">
        <span class="agdot working"></span>
        <span class="agname">${esc(s.agent ? agentName(s.agent) : s.kind || "system")}</span>
        <span class="g" style="flex:1;min-width:0">
          <span class="t">${esc(plain(s.stage_id, s.title))}</span>
          <span class="s">${esc(s.run.project_name || "untitled")} · stage ${(s.position || 0) + 1} of ${s.run.total}</span>
          <span style="display:block;margin-top:6px;max-width:280px">${
            prog(s.run.total ? Math.round(100 * s.run.done / s.run.total) : 0)}</span>
        </span>
        <span class="mono" style="font-size:13px;color:var(--elec);white-space:nowrap"
          data-elapsed="${esc(s.started_at || s.run.updated_at || "")}">—</span>
      </div>`).join("")}
      ${sessionsLive.map(s2 => `<div class="agrow">
        <span class="agdot working"></span>
        <span class="agname">${esc(s2.source || "agent")}</span>
        <span class="g" style="flex:1;min-width:0">
          <span class="t">Agent session${s2.model ? ` · ${esc(s2.model)}` : ""}</span>
          <span class="s">${s2.message_count || 0} messages · ${s2.tool_call_count || 0} tool calls</span></span>
        <span class="mono" style="font-size:13px;color:var(--elec);white-space:nowrap"
          data-elapsed="${esc(String(s2.started_at))}">—</span>
      </div>`).join("")}
    </div></section>`;
  tickTimers();
}

/** Count up every [data-elapsed] element once a second. */
function tickTimers() {
  document.querySelectorAll("[data-elapsed]").forEach(el => {
    const raw = el.dataset.elapsed;
    const n = Number(raw);
    const s = since(isFinite(n) && raw !== "" ? n : raw);
    el.textContent = s == null ? "—" : dur(s);
  });
}

/* ---------------- KPIs ---------------- */
function paintKpis(d) {
  const slot = document.getElementById("monKpis");
  if (!slot) return;
  const runs = d.runs;
  const running = runs.filter(r => r.state === "running").length;
  const waiting = runs.filter(r => r.state === "awaiting_approval").length;
  const failed = runs.filter(r => ["failed", "stopped"].includes(r.state)).length;
  const byKind = (d.fr.summary && d.fr.summary.by_kind) || {};
  const recent = (d.fr.events || []).filter(e => { const s = since(e.ts); return s != null && s < 3600; }).length;

  const tiles = [
    ["Stages running", d.nowStages.length, "var(--elec)", "layers"],
    ["Runs in flight", running + waiting, "var(--blue)", "build"],
    ["Blocked runs", failed, failed ? "var(--crit)" : "var(--tx-3)", "alert"],
    ["Events last hour", recent, "var(--cyan)", "bolt"],
    ["Live sessions", (d.activeSessions || []).length, "var(--ok)", "ai"],
    ["Bridge commands", byKind.command || 0, "var(--warn)", "send"],
  ];
  slot.innerHTML = tiles.map(([k, v, ink, ic]) =>
    `<div class="kpi" style="--kpi-ink:${ink};cursor:default">
      <div class="k">${icon(ic)}${k}</div><div class="v">${v}</div></div>`).join("");
}

/* ---------------- feed ---------------- */
function paintFeed(d) {
  const slot = document.getElementById("monFeed");
  if (!slot) return;
  const keep = live && live.filter !== "all" ? d.feed.filter(e => e.kind === live.filter) : d.feed;
  const sub = document.getElementById("feedSub");
  if (sub) sub.textContent = `${keep.length} events${live && live.paused ? " · paused" : ""}`;

  if (!keep.length) {
    slot.innerHTML = emptyState({ ic: "bolt", title: "No events of this kind yet" });
    return;
  }
  const prevTop = slot.scrollTop;
  slot.innerHTML = keep.slice(0, 80).map(e => {
    const m = KIND_META[e.kind] || KIND_META.activity;
    const dot = STATUS_DOT[e.status] || "";
    return `<div class="agrow" ${e.go ? `data-go="${esc(e.go)}" style="cursor:pointer"` : ""}>
      <span class="agdot ${dot}"></span>
      <span class="agname" title="${esc(m.label)}">${esc(String(e.agent).slice(0, 12))}</span>
      <span class="g" style="flex:1;min-width:0">
        <span class="t">${esc(e.title)}</span>
        ${e.detail ? `<span class="s">${esc(e.detail)}</span>` : ""}
      </span>
      ${tag(titleCase(String(e.status || "logged")), statusCls(e.status))}
      <span class="s mono" style="margin:0;white-space:nowrap;width:66px;text-align:right">${
        e.ts ? ago(new Date(e.ts).toISOString()) : ""}</span>
    </div>`;
  }).join("");
  slot.scrollTop = prevTop;   // keep the reader's place across refreshes
}
function statusCls(s) {
  const d = STATUS_DOT[s];
  return d === "failed" ? "t-crit" : d === "waiting" ? "t-warn"
    : d === "working" ? "t-blue" : d === "done" ? "t-ok" : "t-idle";
}

/* ---------------- system health ---------------- */
function paintSystem(d) {
  const slot = document.getElementById("monSys");
  if (!slot) return;
  const snap = store.snapshot || {};
  const gw = snap.gateway || {};
  const ov = store.overview || {};
  const ints = ov.integrations || {};
  const plat = (gw.platforms && gw.platforms.api_server) || {};

  const rows = [
    ["Hermes gateway", gw.state || "unknown",
     gw.uptime && gw.uptime !== "0s" ? `up ${gw.uptime}${gw.pid ? ` · pid ${gw.pid}` : ""}`
       : gw.pid ? `pid ${gw.pid}` : "no uptime reported"],
    ["API server", plat.state || "unknown",
     plat.error_message ? String(plat.error_message).slice(0, 60)
       : plat.updated_at ? `checked ${ago(plat.updated_at)}` : ""],
    ["Pipeline engine", d.runs && d.runs.length ? "ready" : "idle",
     `${(d.runs || []).length} runs on record`],
    ["Database", "ok", `${(ov.projects || []).length} projects · ${(ov.clients || []).length} clients`],
  ];
  const stale = (d.staleSessions || []).length;
  if (stale) {
    rows.push(["Stale sessions", "warn",
      `${stale} session${stale === 1 ? "" : "s"} never closed — the gateway leaks records when a socket drops`]);
  }
  Object.entries(ints).forEach(([k, v]) => {
    if (!v || v.state !== "error") return;
    rows.push([k === "gsc" ? "Search Console" : titleCase(k), "error",
               String(v.error || "").slice(0, 60)]);
  });

  const good = s => ["running", "connected", "configured", "ok", "ready"].includes(s);
  slot.innerHTML = rows.map(([name, state, note]) => `<div class="agrow">
    <span class="agdot ${good(state) ? "done" : state === "idle" ? "" : state === "warn" ? "waiting" : "failed"}"></span>
    <span class="g"><span class="t">${esc(name)}</span>
      ${note ? `<span class="s">${esc(note)}</span>` : ""}</span>
    ${tag(state === "warn" ? "Attention" : titleCase(state),
        good(state) ? "t-ok" : state === "idle" ? "t-idle" : state === "warn" ? "t-warn" : "t-crit")}
  </div>`).join("");
}

/* ---------------- throughput ---------------- */
function paintThroughput(d) {
  const slot = document.getElementById("monThru");
  if (!slot) return;
  const recent = (d.sessions && d.sessions.recent) || [];
  if (!recent.length) {
    slot.innerHTML = emptyState({ ic: "chart", title: "No sessions logged yet",
      body: "Token and duration figures appear once agents have run." });
    return;
  }
  const done = recent.filter(s => s.ended_at);
  const durs = done.map(s => s.ended_at - s.started_at).filter(n => isFinite(n) && n >= 0);
  const sum = (arr, k) => arr.reduce((a, s) => a + (Number(s[k]) || 0), 0);
  const models = {};
  recent.forEach(s => { if (s.model) models[s.model] = (models[s.model] || 0) + 1; });

  const stat = (k, v, note) => `<div style="display:flex;justify-content:space-between;
    align-items:baseline;padding:8px 0;border-bottom:1px solid var(--line)">
    <span class="s" style="margin:0;color:var(--tx-2)">${esc(k)}</span>
    <span style="font:600 14px/1 var(--sans);font-variant-numeric:tabular-nums">${v}</span>
    ${note ? `<span class="s" style="margin:0 0 0 8px">${esc(note)}</span>` : ""}</div>`;

  slot.innerHTML =
      stat("Sessions in window", recent.length, "")
    + stat("Never closed", (d.staleSessions || []).length, "stale")
    + stat("Avg session length", durs.length ? dur(durs.reduce((a, b) => a + b, 0) / durs.length) : "—",
           durs.length ? "" : "none finished")
    + stat("Messages", sum(recent, "message_count"), "")
    + stat("Tool calls", sum(recent, "tool_call_count"), "")
    + (Object.keys(models).length
        ? `<div style="margin-top:11px"><div class="s" style="margin:0 0 6px">Models in use</div>
           ${Object.entries(models).sort((a, b) => b[1] - a[1]).slice(0, 4)
             .map(([m, n]) => `<div style="display:flex;justify-content:space-between;padding:3px 0">
               <span class="mono" style="font-size:11.5px;color:var(--tx-2);overflow:hidden;
                 text-overflow:ellipsis;white-space:nowrap">${esc(m)}</span>
               <span class="mono" style="font-size:11.5px;color:var(--tx-3)">${n}</span></div>`).join("")}</div>`
        : "");
}
