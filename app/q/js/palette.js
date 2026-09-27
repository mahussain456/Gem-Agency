// ============================================================
// COMMAND PALETTE — Ctrl/Cmd+K. One field, three jobs:
//   1. jump to any route
//   2. search real records (clients, projects, keywords, prospects)
//   3. hand a natural-language command to the AI Command page
// ============================================================
import { apiGet, esc, icon, store } from "/app/q/js/core.js";
import { PAGES } from "/app/q/js/nav.js";

const NAV_CMDS = PAGES.map(p => ({ t: p.t, s: p.s, go: p.h, ic: p.ic }));

/* Things to do rather than places to go. */
const ACTIONS = [
  { t: "Talk to Jarvis", s: "Voice commands · Ctrl J", ic: "ai", k: "Ctrl J",
    run: () => import("/app/q/js/jarvis/jarvis.js").then(m => { m.jarvis.open("full"); m.jarvis.listen(); }) },
  { t: "Customise appearance", s: "Accent colour, density, motion, voice", ic: "gear",
    run: () => import("/app/q/js/customize.js").then(m => m.openPrefs()) },
  { t: "Keyboard shortcuts", s: "Every shortcut on one sheet", ic: "key", k: "?",
    run: () => import("/app/q/js/customize.js").then(m => m.openShortcuts()) },
];

/* Suggested AI commands. Each one is really executable: it opens the AI
   Command page with the text prefilled and the orchestrator selected. */
const AI_SUGGESTIONS = [
  "Build a premium website for a landscaping company in Sacramento",
  "Run a full SEO audit on globalsync-ai.com",
  "Research our competitors",
  "Find backlink opportunities",
  "Generate 20 keyword ideas",
  "Optimize this website for AI search",
  "Show me failed agents",
  "Fix the highest-impact SEO issues",
];

const KIND_ICON = { client: "users", project: "proj", keyword: "key", prospect: "link", competitor: "users" };

export function openPalette(prefill = "") {
  if (document.querySelector(".pal")) return;
  const pal = document.createElement("div");
  pal.className = "pal";
  pal.innerHTML = `<div class="pal-box" role="dialog" aria-modal="true" aria-label="Command palette">
      <div class="pal-in">${icon("search")}
        <input id="palIn" placeholder="Search, jump, or brief the agency…" autocomplete="off" spellcheck="false"></div>
      <div class="pal-res" id="palRes"></div>
      <div class="pal-f"><span><b>↑↓</b> navigate</span><span><b>↵</b> open</span>
        <span><b>esc</b> close</span><span style="margin-left:auto">Enter free text to send it to the agents</span></div>
    </div>`;
  document.body.appendChild(pal);

  const input = pal.querySelector("#palIn");
  const res = pal.querySelector("#palRes");
  let rows = [], sel = 0, timer = null;

  const close = () => { pal.remove(); document.removeEventListener("keydown", onKey); };
  pal.addEventListener("click", e => { if (e.target === pal) close(); });

  function paint(groups) {
    rows = [];
    res.innerHTML = groups.filter(g => g.items.length).map(g =>
      `<div class="pal-g">${esc(g.name)}</div>` + g.items.map(it => {
        rows.push(it);
        return `<button class="pal-i" data-i="${rows.length - 1}">
          <span class="pic">${icon(it.ic || "arrowR")}</span>
          <span class="pg"><span class="pt">${esc(it.t)}</span>
          ${it.s ? `<span class="ps">${esc(it.s)}</span>` : ""}</span>
          ${it.k ? `<span class="pk">${esc(it.k)}</span>` : ""}</button>`;
      }).join("")).join("") || `<div class="empty tight">Nothing matches that.</div>`;
    sel = 0; highlight();
    res.querySelectorAll(".pal-i").forEach(b =>
      b.addEventListener("click", () => run(rows[+b.dataset.i])));
  }
  function highlight() {
    res.querySelectorAll(".pal-i").forEach((b, i) => b.classList.toggle("sel", i === sel));
    const on = res.querySelector(".pal-i.sel");
    if (on) on.scrollIntoView({ block: "nearest" });
  }
  function run(item) {
    if (!item) return;
    close();
    if (item.run) return item.run();
    if (item.go != null) location.hash = item.go;
  }

  function baseGroups(q) {
    const ql = q.toLowerCase();
    const nav = NAV_CMDS.filter(n => !ql || n.t.toLowerCase().includes(ql) || n.s.toLowerCase().includes(ql))
      .map(n => ({ ...n, k: "Go" }));
    const ai = (ql ? AI_SUGGESTIONS.filter(s => s.toLowerCase().includes(ql)) : AI_SUGGESTIONS)
      .slice(0, ql ? 4 : 5)
      .map(s => ({ t: s, s: "Send to the orchestrator", ic: "ai", k: "AI",
                   run: () => { location.hash = "ai/" + encodeURIComponent(s); } }));
    const acts = ACTIONS.filter(a => !ql || a.t.toLowerCase().includes(ql) || a.s.toLowerCase().includes(ql));
    return [{ name: "Go to", items: nav.slice(0, ql ? 6 : 8) }, { name: "Do", items: acts },
            { name: "Brief the agency", items: ai }];
  }

  async function search(q) {
    const groups = baseGroups(q);
    if (q.trim().length >= 2) {
      try {
        const r = await apiGet("/api/agency/search?q=" + encodeURIComponent(q));
        const items = (r.results || []).slice(0, 8).map(x => ({
          t: x.title, s: x.detail || x.kind, ic: KIND_ICON[x.kind] || "doc", k: x.kind,
          go: x.kind === "client" ? `client/${x.id}` : x.kind === "project" ? `project/${x.id}`
            : x.client_id ? `client/${x.client_id}` : "",
        }));
        groups.splice(1, 0, { name: "Records", items });
      } catch (e) { /* search is best-effort; navigation still works */ }
      groups.push({ name: "Send as a command", items: [{
        t: `“${q}”`, s: "Hand this to the agent fleet", ic: "send", k: "AI",
        run: () => { location.hash = "ai/" + encodeURIComponent(q); },
      }]});
    }
    paint(groups);
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, rows.length - 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); highlight(); }
    else if (e.key === "Enter") { e.preventDefault(); run(rows[sel]); }
  }
  document.addEventListener("keydown", onKey);
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => search(input.value), 150);
  });

  paint(baseGroups(""));
  input.value = prefill;
  input.focus();
  if (prefill) search(prefill);
}
