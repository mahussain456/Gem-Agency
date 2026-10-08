// ============================================================
// THE AGENCY — every specialist and every runtime agent, in one place.
//
// Specialists come from agency/<division>/*.md via /api/agency/profiles:
// personality, skills, method, definition of done, and where each one is
// installed (read from disk, never assumed).
// Runtime agents are the gateway agents that execute pipeline stages; their
// status and history come from the agents and agent_runs tables.
// ============================================================
import {
  store, apiGet, apiPost, esc, icon, ago, dur, titleCase, tag, emptyState, errBox, skeleton,
  bindGo, toast, modal, plain, monoAv, loadProfiles,
} from "/app/q/js/core.js";
import { openTeamChat } from "/app/q/js/team.js";

const STATE_TAG = { running: "t-blue", planning: "t-blue", waiting: "t-warn", needs_approval: "t-warn",
                    failed: "t-crit", error: "t-crit", completed: "t-ok", idle: "t-idle" };
const WHERE = {
  claude: { label: "Claude Code", scope: "every project" },
  claude_project: { label: "Claude Code", scope: "this project only" },
  cursor: { label: "Cursor", scope: "this project" },
};
const STATUS_TAG = { synced: ["Synced", "t-ok"], outdated: ["Outdated", "t-warn"],
                     foreign: ["Your own file", "t-idle"], missing: ["Not installed", "t-idle"] };

let ui = { filter: "all", q: "", sel: null, tab: "profile" };

export default async function agency(el, id) {
  const division = (id || "").split("?")[0].split("/")[0];
  ui.filter = division || "all";
  el.innerHTML = `<div class="page">${skeleton(8)}</div>`;

  let data;
  try { data = await loadProfiles({ fresh: true }); }
  catch (e) { el.innerHTML = `<div class="page">${errBox(e)}</div>`; return; }

  let runs = [];
  try { runs = (await apiGet("/api/agency/runs?limit=200")).runs || []; } catch (e) { runs = []; }
  store.agentRuns = runs;

  // Live stage work: the most accurate "current task" the system knows.
  const liveWork = new Map();
  await Promise.all(store.runs.filter(r => r.state === "running").map(async r => {
    try {
      const full = (await apiGet(`/api/agency/pipeline/run?id=${encodeURIComponent(r.id)}`)).run;
      (full.stages || []).filter(s => s.state === "running").forEach(s => liveWork.set(s.agent, { stage: s, run: r }));
    } catch (e) { /* finished between ticks */ }
  }));

  const specialists = data.agents || [];
  const runtime = ((store.overview && store.overview.agents) || []).map(a => {
    const w = liveWork.get(a.id) || liveWork.get(String(a.name || "").toLowerCase());
    const history = runs.filter(r => r.agent_id === a.id);
    const done = history.filter(r => r.finished_at);
    return { ...a, work: w, history, status: w ? "running" : (a.status || "idle"),
             okRate: done.length ? Math.round(100 * done.filter(r => r.state === "completed").length / done.length) : null };
  });

  if (!ui.sel || !findSel(ui.sel, specialists, runtime)) {
    const first = specialists.find(s => ui.filter === "all" || s.division === ui.filter) || specialists[0];
    ui.sel = first ? { kind: "specialist", id: first.name } : runtime[0] ? { kind: "runtime", id: runtime[0].id } : null;
  }

  const installedClaude = specialists.filter(s => s.installed.claude === "synced").length;
  const busy = runtime.filter(a => a.status === "running").length;

  el.innerHTML = `<div class="agency">
    <div class="page">
      <div class="phead">
        <div><h1>The Agency</h1>
          <p>${specialists.length} specialists across ${(data.divisions || []).length} divisions · ${runtime.length} runtime agents${
            busy ? ` · <span style="color:var(--blue)">${busy} working</span>` : ""} · ${installedClaude}/${specialists.length} installed in Claude Code</p></div>
        <div class="acts">
          <label class="searchin">${icon("search")}
            <input id="agentSearch" placeholder="Find by name or skill" value="${esc(ui.q)}"></label>
          <button class="btn" id="installAll">${icon("plug")} Install to…</button>
          <button class="btn pri" data-go="ai">${icon("send")} Send a command</button>
        </div>
      </div>
      <div class="chips" id="chips" style="margin-bottom:12px"></div>
      <section class="panel agency-list" id="list"></section>
    </div>
    <aside class="insp" id="insp"></aside>
  </div>`;

  const paint = () => { paintChips(specialists, runtime, data); paintList(specialists, runtime); paintInspector(specialists, runtime); };
  paint();

  el.querySelector("#installAll").addEventListener("click", () => installModal(specialists.map(s => s.name), "all specialists", el, id));

  // Listeners live on this page's own root: #work is shared by every route and
  // is never replaced, so binding there would stack handlers across visits.
  const root = el.querySelector(".agency");
  root.addEventListener("click", ev => {
    const chip = ev.target.closest("[data-filter]");
    if (chip) {
      ui.filter = chip.dataset.filter;
      history.replaceState(null, "", "#agency" + (ui.filter === "all" ? "" : "/" + ui.filter));
      paintChips(specialists, runtime, data); paintList(specialists, runtime);
      return;
    }
    const row = ev.target.closest("[data-sel]");
    if (row) {
      const [kind, sid] = row.dataset.sel.split(":");
      ui.sel = { kind, id: sid };
      paintList(specialists, runtime); paintInspector(specialists, runtime);
      if (window.matchMedia("(max-width:1100px)").matches) document.getElementById("insp").scrollIntoView({ behavior: "smooth" });
      return;
    }
    const tab = ev.target.closest("[data-tab]");
    if (tab) { ui.tab = tab.dataset.tab; paintInspector(specialists, runtime); return; }

    const act = ev.target.closest("[data-install]");
    if (act) { installOne(act, el, id); return; }

    const copy = ev.target.closest("[data-copy]");
    if (copy) { copyPrompt(copy); return; }

    const chat = ev.target.closest("[data-chat]");
    if (chat) { openTeamChat(chat.dataset.chat, chat.dataset.chatProject || ""); return; }

    const cmd = ev.target.closest("[data-cmd]");
    if (cmd) { location.hash = "ai/?to=" + encodeURIComponent(cmd.dataset.cmd); }
  });
  root.addEventListener("input", ev => {
    if (ev.target.id !== "agentSearch") return;
    ui.q = ev.target.value.trim().toLowerCase();
    paintList(specialists, runtime);
  });
  bindGo(el);
}

function findSel(sel, specialists, runtime) {
  return sel.kind === "specialist" ? specialists.find(s => s.name === sel.id) : runtime.find(a => a.id === sel.id);
}

/* ---------------- filter chips ---------------- */
function paintChips(specialists, runtime, data) {
  const chips = [["all", "All", specialists.length + runtime.length, ""]]
    .concat((data.divisions || []).map(d => [d.id, d.label, d.count, `var(--d-${d.id})`]))
    .concat([["runtime", "Runtime agents", runtime.length, "var(--d-runtime)"]]);
  document.getElementById("chips").innerHTML = chips.map(([k, n, c, col]) =>
    `<button class="chip ${ui.filter === k ? "on" : ""}" data-filter="${esc(k)}">${
      col ? `<span class="dd" style="background:${col}"></span>` : ""}${esc(n)}<span class="n">${c}</span></button>`).join("");
}

/* ---------------- grouped list ---------------- */
function matches(q, ...fields) {
  return !q || fields.flat().some(f => String(f || "").toLowerCase().includes(q));
}

function paintList(specialists, runtime) {
  const list = document.getElementById("list");
  const groups = [];
  const divisions = [...new Set(specialists.map(s => s.division))];
  divisions.forEach(d => {
    if (ui.filter !== "all" && ui.filter !== d) return;
    const rows = specialists.filter(s => s.division === d && matches(ui.q, s.title, s.name, s.skills, s.tagline));
    if (!rows.length) return;
    groups.push(`<div class="grp"><span class="dd" style="width:8px;height:8px;border-radius:2px;background:var(--d-${esc(d)})"></span>
        ${esc(rows[0].division_label)}<span class="n">${rows.length}</span>
        <span class="cols"><span>Skills</span><span>Claude</span><span>Cursor</span></span></div>
      ${rows.map(s => `<div class="arow ${ui.sel && ui.sel.kind === "specialist" && ui.sel.id === s.name ? "sel" : ""}"
          data-sel="specialist:${esc(s.name)}" role="button" tabindex="0">
        ${monoAv(s.title, s.division, { size: "md" })}
        <span class="nm">${esc(s.title)}</span>
        <span class="tl">${esc(s.tagline)}</span>
        <span class="sk">${s.skills.slice(0, 4).map(k => `<span class="ctag">${esc(k)}</span>`).join("")}</span>
        <span class="num">${dotFor(s.installed.claude)}</span>
        <span class="num">${dotFor(s.installed.cursor)}</span>
      </div>`).join("")}`);
  });

  if (ui.filter === "all" || ui.filter === "runtime") {
    const rows = runtime.filter(a => matches(ui.q, a.name, a.id, a.role, a.purpose));
    if (rows.length) {
      groups.push(`<div class="grp"><span class="dd" style="width:8px;height:8px;border-radius:2px;background:var(--d-runtime)"></span>
          Runtime agents<span class="n">${rows.length}</span>
          <span class="cols"><span>Role</span><span>Runs</span><span>Last</span></span></div>
        ${rows.map(a => `<div class="arow ${ui.sel && ui.sel.kind === "runtime" && ui.sel.id === a.id ? "sel" : ""}"
            data-sel="runtime:${esc(a.id)}" role="button" tabindex="0">
          ${monoAv(a.name, "runtime", { size: "md", running: a.status === "running" })}
          <span class="nm">${esc(a.name)}</span>
          <span class="tl ${a.work ? "run" : ""}">${a.work ? "● " + esc(plain(a.work.stage.stage_id, a.work.stage.title))
            : esc(a.gateway_target || "@" + a.id)}</span>
          <span class="sk"><span class="ctag">${esc(a.role || a.purpose || "agent")}</span></span>
          <span class="num">${a.history.length}</span>
          <span class="num" style="font-size:11px">${a.last_run_at ? esc(ago(a.last_run_at)) : "never"}</span>
        </div>`).join("")}`);
    }
  }
  list.innerHTML = groups.join("") || emptyState({ ic: "search", title: "No one matches that",
    body: "Try a skill like “react”, “pricing” or “usability”." });
}

function dotFor(state) {
  const color = { synced: "var(--ok)", outdated: "var(--warn)", foreign: "var(--tx-3)", missing: "var(--raised-2)" }[state];
  const label = (STATUS_TAG[state] || ["Unknown"])[0];
  return `<span title="${esc(label)}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};
    ${state === "missing" ? "box-shadow:inset 0 0 0 1px var(--line-3)" : ""}"></span>`;
}

/* ---------------- inspector ---------------- */
function paintInspector(specialists, runtime) {
  const insp = document.getElementById("insp");
  const item = ui.sel && findSel(ui.sel, specialists, runtime);
  if (!item) { insp.innerHTML = emptyState({ ic: "users", title: "Pick someone", body: "Select a specialist to see their profile." }); return; }
  insp.innerHTML = ui.sel.kind === "specialist" ? specialistView(item, specialists) : runtimeView(item);
}

const ticks = v => `<span class="ticks">${Array.from({ length: 10 }, (_, k) => `<i class="${v != null && k < v ? "on" : ""}"></i>`).join("")}</span>`;
const checks = (items, cls = "") => items.length
  ? `<ul class="checks ${cls}">${items.map(x => `<li>${cls.includes("num") ? "" : icon(cls.includes("no") ? "x" : "check")}<span>${esc(x)}</span></li>`).join("")}</ul>`
  : `<div class="prov">Not written in the profile yet.</div>`;

function specialistView(s, specialists) {
  const tabs = [["profile", "Profile"], ["method", "How they work"], ["install", "Install"]];
  const tab = tabs.some(t => t[0] === ui.tab) ? ui.tab : "profile";
  const partners = s.works_with.map(n => specialists.find(x => x.name === n)).filter(Boolean);
  let body = "";
  if (tab === "profile") {
    body = `
      <div class="lbl-sm">Voice</div>
      <div class="dial"><span>Diplomatic</span>${ticks(s.voice.directness)}<span>Direct</span></div>
      <div class="dial"><span>Lean</span>${ticks(s.voice.depth)}<span>Thorough</span></div>
      <div class="dial"><span>Safe</span>${ticks(s.voice.risk)}<span>Bold</span></div>
      <div class="lbl-sm">Definition of done</div>${checks(s.done)}
      <div class="lbl-sm">Skills</div>
      <div style="display:flex;gap:5px;flex-wrap:wrap">${s.skills.map(k => `<span class="ctag">${esc(k)}</span>`).join("")}</div>
      <div class="lbl-sm">Never</div>${checks(s.never, "no")}
      ${partners.length ? `<div class="lbl-sm">Works with</div><div class="handoff">${partners.map(p =>
        `<button data-sel="specialist:${esc(p.name)}">${monoAv(p.title, p.division)}${esc(p.title)}</button>`).join("")}</div>` : ""}`;
  } else if (tab === "method") {
    body = `
      <div class="lbl-sm">Mission</div><p class="desc">${esc(s.mission)}</p>
      <div class="lbl-sm">Use them for</div>${checks(s.use_for)}
      ${s.not_for.length ? `<div class="lbl-sm">Hand elsewhere</div><ul class="checks">${s.not_for.map(x =>
        `<li>${icon("arrowR")}<span>${esc(x)}</span></li>`).join("")}</ul>` : ""}
      <div class="lbl-sm">How they work</div>${checks(s.how, "num")}
      <div class="lbl-sm">Deliverables</div>${checks(s.deliverables)}
      <div class="lbl-sm">Handoffs</div>${checks(s.handoffs)}
      <div class="lbl-sm">Personality</div>${checks(s.personality)}`;
  } else {
    body = `
      <div class="lbl-sm">Installed in</div>
      ${Object.entries(WHERE).map(([w, meta]) => {
        const st = s.installed[w] || "missing";
        const [label, cls] = STATUS_TAG[st];
        const act = st === "synced" ? ["Remove", "uninstall"] : st === "outdated" ? ["Update", "install"]
          : st === "foreign" ? null : ["Install", "install"];
        return `<div class="inst"><span class="g">${esc(meta.label)} <span style="color:var(--tx-3);font-weight:400">· ${esc(meta.scope)}</span>
            <code title="${esc((store.profiles.destinations || {})[w] || "")}">${esc(shortPath((store.profiles.destinations || {})[w] || ""))}</code></span>
          ${tag(label, cls)}
          ${act ? `<button class="btn sm ${act[1] === "uninstall" ? "ghost" : ""}" data-install="${esc(w)}" data-name="${esc(s.name)}"
              data-mode="${act[1]}">${esc(act[0])}</button>` : ""}</div>`;
      }).join("")}
      <p class="prov" style="margin-top:6px;display:block">Files you wrote yourself are never overwritten or removed.
        Status is read from disk each time this page loads.</p>
      <div class="lbl-sm">Use anywhere else</div>
      <button class="btn sm" data-copy="${esc(s.name)}">${icon("doc")} Copy system prompt</button>
      <span class="prov" style="margin-left:6px">for ChatGPT, Gemini or an API call</span>
      <div class="lbl-sm">Source</div><code class="mono" style="color:var(--tx-3);font-size:11.5px">${esc(s.source)}</code>`;
  }
  const claude = s.installed.claude;
  return `
    <div class="hero">${monoAv(s.title, s.division, { size: "lg" })}
      <div style="min-width:0"><h2>${esc(s.title)}</h2><div class="handle">@${esc(s.name)} · ${esc(s.division_label)}</div>
        <div style="margin-top:8px">${claude === "synced" ? tag("In Claude Code", "t-ok", true) : tag("Not installed", "t-idle")}</div></div></div>
    <div class="quote">“${esc(s.tagline)}”</div>
    <p class="desc">${esc(s.description)}</p>
    <div class="tabs">${tabs.map(([k, n]) => `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${n}</button>`).join("")}</div>
    ${body}
    <div style="display:flex;gap:6px;margin-top:18px">
      ${claude === "synced"
        ? `<button class="btn" style="flex:1;justify-content:center" data-tab="install">${icon("check")} Installed in Claude Code</button>`
        : `<button class="btn pri" style="flex:1;justify-content:center" data-install="claude" data-name="${esc(s.name)}" data-mode="install">${icon("plug")} Install in Claude Code</button>`}
      <button class="btn" data-copy="${esc(s.name)}" title="Copy system prompt">${icon("doc")}</button>
    </div>`;
}

function runtimeView(a) {
  const done = a.history.filter(r => r.finished_at);
  const secs = done.map(r => (new Date(r.finished_at) - new Date(r.started_at)) / 1000).filter(n => isFinite(n) && n >= 0);
  const avg = secs.length ? secs.reduce((x, y) => x + y, 0) / secs.length : null;
  return `
    <div class="hero">${monoAv(a.name, "runtime", { size: "lg", running: a.status === "running" })}
      <div style="min-width:0"><h2>${esc(a.name)}</h2><div class="handle">${esc(a.gateway_target || "@" + a.id)} · runtime agent</div>
        <div style="margin-top:8px">${tag(titleCase(a.status), STATE_TAG[a.status] || "t-idle", true)}</div></div></div>
    <div class="quote">${esc(a.role || a.purpose || "Gateway agent")}</div>
    <div class="kv3">
      <div><b>${a.history.length}</b>runs logged</div>
      <div><b>${a.okRate == null ? "—" : a.okRate + "%"}</b>completed</div>
      <div><b>${avg == null ? "—" : dur(avg)}</b>avg run</div></div>
    <div class="lbl-sm">Right now</div>
    <p class="desc">${a.work ? `${esc(plain(a.work.stage.stage_id, a.work.stage.title))} · ${esc(a.work.run.project_name || "")}` : "Nothing assigned. Wakes when a pipeline stage or a command reaches it."}</p>
    <div class="lbl-sm">Guardrails</div>
    <p class="desc">${a.approval_required ? "Output waits for your sign-off before it takes effect." : "Runs without an approval step."}
      ${(a.permissions || []).length ? `<br>Permissions: ${esc(a.permissions.join(", "))}` : ""}</p>
    <div class="lbl-sm">Run history</div>
    ${a.history.length ? a.history.slice(0, 10).map(r => `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid var(--line)">
        <span class="agdot ${r.state === "completed" ? "done" : r.state === "failed" ? "failed" : r.state === "running" ? "working" : ""}" style="margin-top:6px"></span>
        <span style="flex:1;min-width:0"><span style="display:block;font-size:12.5px">${esc(r.summary || titleCase(r.state))}</span>
          <span style="font-size:11.5px;color:var(--tx-3)">${ago(r.started_at)}${r.error ? " · " + esc(String(r.error).slice(0, 70)) : ""}</span></span></div>`).join("")
      : `<div class="prov">No runs recorded for this agent yet.</div>`}
    <div style="display:flex;gap:6px;margin-top:18px">
      <button class="btn pri" style="flex:1;justify-content:center" data-chat="${esc(a.id)}" data-chat-project="${esc(a.work?.run?.project_id || "")}">${icon("send")} Message ${esc(String(a.name).split(/\s+/)[0])}</button>
      <button class="btn" data-cmd="${esc(a.gateway_target || "@" + a.id)}" title="Send a one-off command">${icon("ai")}</button>
      <button class="btn" data-go="monitor">${icon("bolt")}</button>
    </div>`;
}

function shortPath(p) {
  return String(p).replace(/^[A-Za-z]:\\Users\\[^\\]+/, "~").replace(/\\/g, "/");
}

/* ---------------- actions ---------------- */
async function installOne(btn, el, id) {
  const where = btn.dataset.install, name = btn.dataset.name, uninstall = btn.dataset.mode === "uninstall";
  btn.disabled = true;
  try {
    const { result } = await apiPost("/api/agency/profiles/install", { where, names: [name], uninstall });
    const label = WHERE[where].label;
    toast(result.skipped.length ? `Skipped: a file you wrote already exists in ${label}.`
      : uninstall ? `Removed from ${label}.` : result.changed.length ? `Installed in ${label}. Restart it to pick up the agent.` : "Already up to date.",
      !!result.skipped.length);
    await agency(el, id);
  } catch (e) { btn.disabled = false; toast(String(e.message || e), true); }
}

function installModal(names, what, el, id) {
  modal({
    title: "Install specialists",
    note: `Writes ${what} (${names.length}) as agent files. Unchanged files are skipped, and files you wrote yourself are never touched.`,
    fields: [{ name: "where", label: "Install into", type: "select", value: "claude", options: Object.entries(WHERE)
      .map(([k, v]) => ({ value: k, label: `${v.label} (${v.scope})` })) }],
    submitLabel: "Install",
    onSubmit: async v => {
      const { result } = await apiPost("/api/agency/profiles/install", { where: v.where, names });
      toast(`${result.changed.length} installed or updated, ${result.unchanged.length} unchanged${
        result.skipped.length ? `, ${result.skipped.length} skipped (your own files)` : ""}.`);
      await agency(el, id);
    },
  });
}

async function copyPrompt(btn) {
  try {
    const { text } = await apiGet(`/api/agency/profiles/render?target=prompt&name=${encodeURIComponent(btn.dataset.copy)}`);
    await navigator.clipboard.writeText(text);
    toast("System prompt copied.");
  } catch (e) { toast("Could not copy: " + (e.message || e), true); }
}
