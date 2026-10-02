// ============================================================
// OPENSEO — the open-source SEO suite (github.com/every-app/open-seo),
// running on this computer and shown inside the dashboard: keyword research,
// rank tracking, backlinks, domain overview, site audits, AI visibility.
// The dashboard installs, starts and stops it; opening this page starts it.
// Its SEO data comes from DataForSEO, using the key saved once on the
// Integrations page.
// ============================================================
import { apiGet, apiPost, esc, icon, tag, toast, errBox, skeleton } from "/app/q/js/core.js";

const STEPS = [
  ["installing", "Download", "Fetches OpenSEO and its packages (once, a few minutes)"],
  ["migrating", "Database", "Prepares its local database"],
  ["building", "Build", "Builds the app (only after an update, about a minute)"],
  ["starting", "Start", "Starts it on this computer"],
];
let poll = 0;

export default async function openseoPage(el) {
  clearInterval(poll);
  el.innerHTML = `<div class="page os-page">
    <div class="phead"><div><h1>OpenSEO</h1>
      <p>The open-source SEO suite, running on this computer: keyword research, rank tracking, backlinks, domain overview, site audits and AI search visibility.</p></div>
      <div class="os-actions" id="osActions"></div></div>
    <div id="osSlot">${skeleton(3)}</div></div>`;
  let st;
  try { st = (await apiGet("/api/agency/openseo/status")).openseo; }
  catch (e) { document.getElementById("osSlot").innerHTML = errBox(e); return; }
  // seamless: opening the page starts it; it is a local process, so this costs nothing
  if (st.installed && !st.running && st.phase === "stopped") {
    try { st = (await apiPost("/api/agency/openseo/start", {})).openseo; } catch (e) { toast(String(e.message || e), true); }
  }
  draw(st);
  poll = setInterval(async () => {
    if (!document.getElementById("osSlot")) { clearInterval(poll); return; }
    try {
      const next = (await apiGet("/api/agency/openseo/status")).openseo;
      // redraw only when the state changes: the embedded app must not reload every tick
      if (key(next) !== key(st)) { st = next; draw(st); }
      else if (busy(st)) { st = next; drawProgress(st); }
    } catch { /* the next tick retries */ }
  }, 2000);
}

const busy = s => ["installing", "migrating", "building", "starting"].includes(s.phase);
const key = s => [s.installed, s.running, s.phase, s.dataforseo, s.error].join("|");

function draw(st) {
  const slot = document.getElementById("osSlot"), actions = document.getElementById("osActions");
  if (!slot) return;
  actions.innerHTML = st.running
    ? `${tag("Running on this computer", "t-ok", true)}
       <a class="btn" href="${esc(st.url)}" target="_blank" rel="noopener">${icon("ext")} Open in a new tab</a>
       <button class="btn" id="osStop">${icon("pause")} Stop</button>`
    : st.installed && !busy(st) ? `<button class="btn pri" id="osStart">${icon("play")} Start OpenSEO</button>` : "";

  const keyNote = !st.dataforseo ? `<div class="os-note">${icon("key")}<div>
      <b>Add your DataForSEO key to fill OpenSEO with data</b>
      <p>Keyword research, rank tracking, backlinks, domain overview and AI visibility come from DataForSEO (pay as you go, billed by DataForSEO). Save it once on Integrations; OpenSEO restarts with it. Site audits and Search Console work without it.</p></div>
      <button class="btn" data-go="integrations">${icon("plug")} Add the key</button></div>` : "";

  if (st.running) {
    slot.innerHTML = `${keyNote}<section class="panel os-frame"><iframe src="${esc(st.url)}/" title="OpenSEO"
        referrerpolicy="no-referrer"></iframe></section>`;
  } else if (!st.installed && !busy(st)) {
    slot.innerHTML = `<section class="panel"><div class="panel-b os-empty">
        <div class="os-empty-ic">${icon("seo")}</div>
        <h2>Install OpenSEO on this computer</h2>
        <p>The dashboard downloads OpenSEO (MIT licence) at a tested version, installs its packages and keeps it running for you. It needs Node.js, which is installed, and about 1 GB of disk. It listens only on this computer.</p>
        ${st.phase === "failed" ? `<div class="errbox" style="margin:12px 0 0;text-align:left"><b>The last install failed</b>${esc(st.error)}</div>` : ""}
        <button class="btn pri" id="osInstall">${icon("plus")} Install OpenSEO</button></div></section>`;
  } else if (busy(st)) {
    slot.innerHTML = `<section class="panel"><div class="panel-h"><div><h2 id="osBusyTitle"></h2>
        <div class="sub" id="osBusySub"></div></div></div>
        <div class="panel-b"><ol class="os-steps" id="osSteps"></ol>
        <details class="cu-more"><summary>Show the log</summary><pre class="cu-out" id="osLog"></pre></details></div></section>`;
    drawProgress(st);
  } else {
    slot.innerHTML = `<section class="panel"><div class="panel-b">
        <div class="errbox" style="margin:0 0 12px"><b>${st.phase === "failed" ? "OpenSEO could not start" : "OpenSEO is stopped"}</b>${esc(st.error || "Start it to use it here.")}</div>
        ${st.log && st.log.length ? `<pre class="cu-out">${esc(st.log.join("\n"))}</pre>` : ""}
        <button class="btn pri" id="osRetry" style="margin-top:12px">${icon("refresh")} ${st.phase === "failed" ? "Try again" : "Start OpenSEO"}</button></div></section>`;
  }
  const act = (id, path, msg) => {
    const b = document.getElementById(id);
    if (b) b.addEventListener("click", async () => {
      b.disabled = true;
      try { draw((await apiPost(path, {})).openseo); if (msg) toast(msg); }
      catch (e) { toast(String(e.message || e), true); b.disabled = false; }
    });
  };
  act("osInstall", "/api/agency/openseo/install", "Installing OpenSEO. This takes a few minutes.");
  act("osStart", "/api/agency/openseo/start");
  act("osRetry", "/api/agency/openseo/start");
  act("osStop", "/api/agency/openseo/stop", "OpenSEO stopped.");
  slot.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { location.hash = b.dataset.go; }));
}

function drawProgress(st) {
  const list = document.getElementById("osSteps");
  if (!list) return;
  const at = STEPS.findIndex(s => s[0] === st.phase);
  const label = (STEPS[at] || STEPS[3])[1];
  document.getElementById("osBusyTitle").textContent = `${label}…`;
  document.getElementById("osBusySub").textContent = `Working for ${st.busy_for || 0} s. You can leave this page; it carries on.`;
  list.innerHTML = STEPS.map(([id, t, d], i) => {
    const cls = i < at ? "done" : i === at ? "now" : "";
    if (id === "installing" && st.installed && at > 0) return "";            // already installed: not part of this run
    return `<li class="${cls}"><span class="os-dot">${i < at ? icon("check") : ""}</span><div><b>${t}</b><span>${d}</span></div></li>`;
  }).join("");
  const log = document.getElementById("osLog");
  if (log) log.textContent = (st.log || []).join("\n");
}

export function openseoTeardown() { clearInterval(poll); }
