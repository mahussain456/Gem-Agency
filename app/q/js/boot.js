// ============================================================
// BOOT — navigation, router, command bar, palette, global chrome.
// ============================================================
import {
  apiGet, refresh, store, subscribe, icon, esc, toast, bindGo, titleCase,
  approvalStats, daysUntil, modal, apiPost, closeDrawer, drawer, ago,
} from "/app/q/js/core.js";
import { openPalette } from "/app/q/js/palette.js";
import { ROUTES, monitorTeardown, computerTeardown, mediaTeardown, openseoTeardown, officeTeardown } from "/app/q/js/routes.js";
import { recoveryMessage } from '/app/q/js/workflow.js';
import { NAV, TITLES } from "/app/q/js/nav.js";
import "/app/q/js/prefs.js";
import { routeStart, routeEnd, pageEntered, moveNavIndicator, installPointerEffects, pop } from "/app/q/js/motion.js";
import { openPrefs, openShortcuts, installKeys } from "/app/q/js/customize.js";
import { initJarvis } from "/app/q/js/jarvis/jarvis.js";

/* The navigation model lives in nav.js: the sidebar, palette, shortcuts
   and Jarvis all read the same list. */

const app = document.getElementById("app");
const work = document.getElementById("work");
const navEl = document.getElementById("nav");

function renderNav() {
  navEl.innerHTML = NAV.map(g => `<div class="nav-h">${esc(g.group)}</div>` +
    g.items.map(i => `<a href="#${i.h}" data-h="${i.h}" title="${esc(i.t)}">
        ${icon(i.ic)}<span>${esc(i.t)}</span>
        ${i.badge ? `<span class="pillcount" data-badge="${i.badge}" hidden>0</span>` : ""}
      </a>`).join("")).join("");
  paintNavState();
}

function currentRoute() {
  const raw = location.hash.replace(/^#/, "");
  const [head, ...rest] = raw.split("/");
  return { key: head || "", id: rest.join("/") || "", raw };
}

function paintNavState() {
  const { key } = currentRoute();
  navEl.querySelectorAll("a").forEach(a => {
    const h = a.dataset.h;
    a.classList.toggle("on", h === key || (key === "agents" && h === "agency")
      || (["optimize", "backlinks"].includes(key) && h === "growth")
      || (key === "project" && h === "projects") || (key === "client" && h === "clients"));
  });
  const title = TITLES[key] || (!ROUTES[key] ? "Not found" : key ? titleCase(key) : "Overview");
  document.getElementById("crumb").innerHTML = `Gem Agency <span style="opacity:.5">/</span> <b>${esc(title)}</b>`;
  document.title = key ? `${title} · Gem Agency` : "Gem Agency";
  moveNavIndicator(navEl);
}

/* ---------------- router ---------------- */
let currentKey = null;
/* quiet: an auto-refresh repaint of the page already on screen. It skips the
   entrance animation so nothing moves under someone who is reading. */
async function renderRoute({ quiet = false } = {}) {
  const { key, id } = currentRoute();
  paintNavState();
  const page = ROUTES[key] || ROUTES._missing;
  // pages that own timers or sockets clean up before the next one paints
  if (currentKey === "monitor" && key !== "monitor") monitorTeardown();
  if (currentKey === "computer") computerTeardown();     // stop polling the old session
  if (currentKey === "media") mediaTeardown();
  if (currentKey === "seo") openseoTeardown();
  if (currentKey === "office") officeTeardown();   // frees the GPU and stops polling
  // Dismiss anything floating above the workspace. A modal or drawer left over
  // from the previous route would sit on top of the new page still bound to the
  // old record, and submitting it would write against the wrong context.
  document.querySelectorAll(".ovl, .pal, .bellwrap").forEach(n => n.remove());
  closeDrawer();
  work.classList.toggle("quiet", quiet && currentKey === key);
  if (!quiet) work.scrollTop = 0;
  currentKey = key;
  if (!quiet) routeStart();
  try {
    await page(work, id, { toast, modal, refresh, drawer, closeDrawer });
    pageEntered(work, { quiet });
  } catch (e) {
    work.innerHTML = `<div class="page"><div class="errbox">
      <b>This page failed to render</b>${esc(String(e && e.message || e))}</div></div>`;
    console.error("route render failed:", e);
  }
  if (!quiet) routeEnd();
  bindGo(work);
}
window.addEventListener("hashchange", renderRoute);

/* ---------------- sidebar collapse + mobile drawer ---------------- */
if (localStorage.getItem("q-collapsed") === "1") app.classList.add("collapsed");
export function setCollapsed(on) {
  app.classList.toggle("collapsed", on);
  try { localStorage.setItem("q-collapsed", on ? "1" : "0"); } catch { /* per session then */ }
  setTimeout(() => moveNavIndicator(navEl), 260);
}
document.getElementById("collapseBtn").addEventListener("click", () =>
  setCollapsed(!app.classList.contains("collapsed")));
const closeNav = () => app.classList.remove("drawer");
document.getElementById("menuBtn").addEventListener("click", () => app.classList.toggle("drawer"));
document.getElementById("navScrim").addEventListener("click", closeNav);
navEl.addEventListener("click", e => { if (e.target.closest("a")) closeNav(); });

/* ---------------- command bar + palette ---------------- */
document.getElementById("cmdField").addEventListener("click", () => openPalette());
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  if (e.key === "Escape") closeDrawer();
});

/* ---------------- global actions ---------------- */
document.getElementById("newBtn").addEventListener("click", () => { location.hash = 'builder'; });
document.getElementById("agentsBtn").addEventListener("click", () => { location.hash = "monitor"; });

export function newProject() {
  const clients = (store.overview && store.overview.clients) || [];
  modal({
    title: "New project",
    note: "This creates a real project row. Start a pipeline run for it from the AI Website Builder.",
    fields: [
      { name: "name", label: "Project name", required: true, placeholder: "e.g. Northgate Plumbing rebuild" },
      { name: "url", label: "Website URL", placeholder: "https://example.com",
        hint: "Required for SEO campaigns — the audit fetches this page for real." },
      { name: "client_id", label: "Client", type: "select",
        options: [{ value: "", label: "— no client —" }].concat(clients.map(c => ({ value: c.id, label: c.name }))) },
      { name: "type", label: "Type", type: "select", value: "seo_campaign", options: [
        { value: "seo_campaign", label: "SEO campaign" },
        { value: "website_build", label: "Website build" },
        { value: "marketing_website", label: "Marketing website" },
      ]},
      { name: "brief", label: "Brief", type: "textarea", placeholder: "What are we trying to achieve?" },
    ],
    submitLabel: "Create project",
    onSubmit: async v => {
      const res = await apiPost("/api/agency/projects", v);
      await refresh();
      toast("Project created.");
      if (res.project && res.project.id) location.hash = `project/${res.project.id}`;
      else renderRoute();
    },
  });
}

/* ---------------- attention bell ---------------- */
export function attentionItems() {
  const ov = store.overview;
  if (!ov) return [];
  const out = [];
  (ov.approvals || []).forEach(a => out.push({
    sev: a.blocking ? "hi" : "md", ic: "approve",
    t: fixTitle(a.title, "A decision is waiting"),
    s: `${a.source_agent ? "Raised by " + a.source_agent : "Raised"} ${ago(a.created_at)}${
        a.blocking ? " · downstream work is stopped" : ""}`,
    go: "approvals",
  }));
  (ov.deadlines || []).forEach(d => {
    const left = daysUntil(d.due_date);
    if (left == null || left > 7) return;
    out.push({
      sev: left < 0 ? "hi" : left <= 1 ? "md" : "lo", ic: "clock",
      t: left < 0 ? `${d.title} is ${Math.abs(left)} day${Math.abs(left) === 1 ? "" : "s"} overdue`
        : left === 0 ? `${d.title} is due today` : `${d.title} is due in ${left} days`,
      s: `${d.kind} · ${d.due_date}`,
      go: d.kind === "project" ? `project/${d.id}` : "projects",
    });
  });
  const NAMES = { gsc: "Google Search Console", ga4: "Google Analytics 4", openseo: "OpenSEO",
                  stripe: "Stripe", hermes_gateway: "Hermes gateway" };
  const brain = (ov.integrations || {}).brain;
  if (brain && brain.state === "error") out.push({ sev: "hi", ic: "ai",
    t: "No thinking engine is connected",
    s: "Builds, SEO stages, Jarvis and Ask the agency cannot run until Claude, ChatGPT or Ollama is connected.",
    go: "models" });
  else if (brain && brain.state === "degraded") out.push({ sev: "md", ic: "ai",
    t: `Running on a fallback: ${BRAIN_NAMES[brain.active] || brain.active}`,
    s: `${BRAIN_NAMES[(brain.order || [])[0]] || "Your first choice"} is not answering${
      brain.reason ? " (" + brain.reason.replace(/^\w+:\s*/, "").slice(0, 140) + ")" : ""}, so ${BRAIN_NAMES[brain.active] || brain.active} is doing the thinking.`,
    go: "models" });
  Object.entries(ov.integrations || {}).forEach(([k, v]) => {
    if (!v || v.state !== "error" || k === "brain") return;
    out.push({ sev: "md", ic: "plug",
      t: `${NAMES[k] || k} authentication failed`,
      s: "Data from this source is left blank rather than estimated. Reconnect to restore it.",
      go: "integrations" });
  });
  store.runs.filter(r => r.state === "failed" || r.state === "stopped").slice(0, 4).forEach(r => out.push({
    sev: "hi", ic: "alert",
    t: `Pipeline stopped on ${r.project_name || "a project"}`,
    s: `${recoveryMessage(r.error).title}. ${recoveryMessage(r.error).detail}`,
    go: `project/${r.project_id}`,
  }));
  const order = { hi: 0, md: 1, lo: 2 };
  return out.sort((a, b) => order[a.sev] - order[b.sev]);
}
function fixTitle(t, fallback) { return String(t || fallback); }
const BRAIN_NAMES = { claude: "Claude", chatgpt: "ChatGPT", ollama: "Ollama (local)", hermes: "Hermes gateway" };

let bellOpen = null;
document.getElementById("bellBtn").addEventListener("click", e => {
  e.stopPropagation();
  if (bellOpen) { bellOpen.remove(); bellOpen = null; return; }
  const items = attentionItems();
  const wrap = document.createElement("div");
  wrap.className = "panel";
  wrap.style.cssText = "position:fixed;top:56px;right:22px;width:min(400px,92vw);z-index:150;max-height:70vh;overflow:auto";
  wrap.innerHTML = `<div class="panel-h"><h2>Needs your attention</h2>
      <span class="r">${items.length ? `<span class="tag t-crit">${items.length}</span>` : ""}</span></div>
    ${items.length ? items.slice(0, 8).map(i => `<div class="att ${i.sev}" data-go="${esc(i.go)}" style="cursor:pointer">
        <span class="ic">${icon(i.ic)}</span><div class="g"><div class="t">${esc(i.t)}</div>
        <div class="s">${esc(i.s)}</div></div></div>`).join("")
      : `<div class="allclear"><span class="ic">${icon("check")}</span><div>
         <b>You're all caught up.</b><span>No approvals, overdue work or broken connections.</span></div></div>`}`;
  document.body.appendChild(wrap);
  bellOpen = wrap;
  bindGo(wrap);
  wrap.addEventListener("click", ev => { if (ev.target.closest("[data-go]")) { wrap.remove(); bellOpen = null; } });
  setTimeout(() => document.addEventListener("click", function off(ev) {
    if (!wrap.contains(ev.target)) { wrap.remove(); bellOpen = null; document.removeEventListener("click", off); }
  }), 0);
});

/* ---------------- live chrome updates ---------------- */
subscribe(() => {
  const ov = store.overview, snap = store.snapshot;
  // which engine is thinking for the agency right now
  const brain = ov && ov.integrations && ov.integrations.brain;
  const chip = document.getElementById("gwChip");
  if (brain) {
    const name = BRAIN_NAMES[brain.active] || "offline";
    chip.className = "gw " + (brain.state === "configured" ? "ok" : brain.state === "degraded" ? "warn" : "bad");
    chip.innerHTML = `<span class="led"></span><span>Brain · ${esc(brain.state === "error" ? "offline" : name)}</span>`;
    chip.title = brain.state === "error" ? "No thinking engine is connected. Open Models."
      : `Thinking with ${name}${brain.model ? " (" + brain.model + ")" : ""}. Chain: ${(brain.live || []).map(k => BRAIN_NAMES[k] || k).join(" → ")}`;
    chip.style.cursor = "pointer";
    chip.onclick = () => { location.hash = "models"; };
  }

  // attention badge
  const n = attentionItems().length;
  const badge = document.getElementById("bellCount");
  if (n > (+badge.dataset.n || 0)) pop(badge);
  badge.dataset.n = n;
  badge.textContent = n > 99 ? "99+" : n;
  badge.hidden = !n;

  // nav badges
  const ap = approvalStats(ov);
  const inFlight = store.runs.filter(r => ["running", "awaiting_approval"].includes(r.state)).length;
  navEl.querySelectorAll("[data-badge]").forEach(b => {
    let v = 0, calm = false;
    if (b.dataset.badge === "approvals") v = ap.pending;
    if (b.dataset.badge === "integrations")
      v = Object.values((ov && ov.integrations) || {}).filter(i => i && i.state === "error").length;
    if (b.dataset.badge === "working") { v = inFlight; calm = true; }
    b.textContent = v;
    b.hidden = !v;
    b.classList.toggle("calm", calm);
  });

  // the top-bar live chip says, in words, whether the agents are actually busy
  const busyAgents = ((ov && ov.agents) || [])
    .filter(a => ["running", "planning", "waiting", "needs_approval"].includes(a.status)).length;
  const working = Math.max(inFlight, busyAgents);
  const mon = document.getElementById("agentsBtn");
  mon.classList.toggle("on", working > 0);
  document.getElementById("liveTxt").textContent = working
    ? `${working} ${inFlight ? "run" : "agent"}${working === 1 ? "" : "s"} working` : "Agents idle";
  mon.title = working ? "Open the monitor: what is being worked on" : "Nothing in flight. Open the monitor";
});

/* ---------------- boot ---------------- */
async function boot() {
  renderNav();
  installPointerEffects();
  installKeys();
  document.getElementById("prefsBtn").addEventListener("click", openPrefs);
  initJarvis({ attentionItems, renderRoute, setCollapsed, openPrefs, openShortcuts });
  window.addEventListener("resize", () => moveNavIndicator(navEl));
  try {
    await refresh();
  } catch (e) {
    work.innerHTML = `<div class="page"><div class="errbox"><b>Could not reach the server</b>
      ${esc(String(e && e.message || e))}</div></div>`;
    return;
  }
  await renderRoute();
  setInterval(async () => {
    try {
      await refresh();
      // only auto-repaint list-style pages; forms and drawers keep their state
      if (["", "approvals", "projects"].includes(currentKey)
          && !work.contains(document.activeElement)
          && !document.querySelector(".ovl, .drawer.on, .pal")) await renderRoute({ quiet: true });
    } catch (e) { /* transient — the next tick retries */ }
  }, 30000);
}
boot();
