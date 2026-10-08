// ============================================================
// THE TEAM — talk to one person directly.
//
// openTeamChat("lumen", projectId) opens a conversation with Sofia about
// that project, in the side panel. Threads are kept per person and per
// project, so the conversation is still there tomorrow. Replies come in
// character from the brain, grounded in the project's real work.
//
// When you ask someone to change their work, they propose it as a card
// ("Redo: Two design directions · 13 steps run again"). Nothing runs, and no
// credits are spent, until you press Start.
// ============================================================
import { store, apiGet, apiPost, esc, icon, toast, drawer, closeDrawer, ago, refresh } from "./core.js";

/* the same colours as their shirts in the office, so a face reads the same everywhere */
export const TEAM_COLOR = {
  orchestrator: "#3aa58c", scout: "#5a8fd0", rank: "#4fa874", reach: "#d4864a", scribe: "#8a6fc0",
  lumen: "#c76a8a", stitch: "#6a7fd4", forge: "#c9a03a", dev: "#3f7f9f",
};
const STARTERS = {
  lumen: ["Walk me through the two designs", "Which design would you pick, and why?", "Both feel too safe. Push them further"],
  stitch: ["How did you build the samples?", "Make the mobile layout tighter"],
  scribe: ["Summarise the homepage copy", "Make the hero headline sharper"],
  scout: ["Who are the main competitors?", "What did the research miss?"],
  rank: ["What are the biggest SEO risks?", "Which keywords should we own first?"],
  reach: ["Who should we ask for links first?", "Draft a friendlier outreach email"],
  dev: ["Is the build ready to launch?", "What did the browser check find?"],
  forge: ["What could break at launch?"],
  orchestrator: ["Where is this project stuck?", "What do you need from me?"],
};

const GENERAL = ["What do you do on the team?", "What makes your best work stand out?", "What do you need from me to do great work?"];

export const initials = n => String(n || "?").split(/\s+/).map(w => w[0] || "").join("").slice(0, 2).toUpperCase();
export function face(id, name, cls = "") {
  return `<span class="tface ${cls}" style="--c:${TEAM_COLOR[id] || "#7d8f99"}" aria-hidden="true">${esc(initials(name))}</span>`;
}

let current = null;            // { agent, project, el }

export async function openTeamChat(agentId, projectId = "", { draft = "" } = {}) {
  const team = (store.overview?.agents || []);
  const who = team.find(a => a.id === agentId) || { id: agentId, name: agentId, role: "" };
  const projects = (store.overview?.projects || []).filter(p => p.status !== "archived");
  const project = projects.find(p => p.id === projectId);
  const first = String(who.name).split(/\s+/)[0];
  const dr = drawer({
    title: `<span class="tchat-who">${face(who.id, who.name, "lg")}<span><b>${esc(who.name)}</b>
      <span class="s">${esc(who.role || "")}</span></span></span>`,
    sub: `<label class="tchat-proj"><span>About</span><select id="tcProject" aria-label="Which project">
      <option value="">General, no project</option>${projects.map(p =>
        `<option value="${esc(p.id)}" ${p.id === projectId ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>`,
    body: `<div class="tchat" id="tcLog" aria-live="polite"></div>`,
    footer: `<form class="tcomp" id="tcForm">
      <textarea id="tcText" rows="2" placeholder="Message ${esc(first)}…" aria-label="Message ${esc(first)}"></textarea>
      <button class="btn pri" id="tcSend" type="submit" aria-label="Send">${icon("send")}</button>
      <span class="tcomp-h">Enter to send · Shift Enter for a new line · hold Ctrl Space to talk</span></form>`,
  });
  dr.classList.add("tchat-dr");
  current = { agent: who, project: project ? project.id : "", el: dr };
  const text = dr.querySelector("#tcText");
  dr.querySelector("#tcProject").addEventListener("change", e => openTeamChat(agentId, e.target.value));
  dr.querySelector("#tcForm").addEventListener("submit", e => { e.preventDefault(); send(); });
  text.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  if (draft) text.value = draft;
  await load();
  text.focus();
}

async function load() {
  const c = current, log = c.el.querySelector("#tcLog");
  log.innerHTML = `<div class="tmsg event">Loading the conversation…</div>`;
  let t;
  try { t = await apiGet(`/api/agency/agents/chat?agent=${encodeURIComponent(c.agent.id)}&project_id=${encodeURIComponent(c.project)}`); }
  catch (e) { log.innerHTML = `<div class="tmsg event err">${esc(String(e.message || e))}</div>`; return; }
  if (current !== c) return;
  c.thread = t;
  paint();
}

function paint() {
  const c = current, log = c.el.querySelector("#tcLog"), t = c.thread, first = c.agent.name.split(/\s+/)[0];
  const msgs = t.messages || [];
  const steps = t.steps || [];
  log.innerHTML = (msgs.length ? "" : `<div class="tchat-empty">
      <p>${c.project ? `${esc(first)} knows this project's brief, where the pipeline is, and the work ${
        steps.length ? `they did: ${esc(steps.map(s => s.title).join(", "))}` : "the team has done"}.`
        : `No project picked, so this is a general chat. Pick a project above to talk about real work.`}</p>
      <div class="tchat-starts">${(c.project ? STARTERS[c.agent.id] || STARTERS.orchestrator : GENERAL).map(s =>
        `<button class="chip" type="button" data-start="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`)
    + msgs.map(m => bubble(m, first)).join("");
  log.querySelectorAll("[data-start]").forEach(b => b.addEventListener("click", () => { c.el.querySelector("#tcText").value = b.dataset.start; send(); }));
  log.querySelectorAll("[data-confirm]").forEach(b => b.addEventListener("click", () => confirm(b)));
  log.querySelectorAll("[data-dismiss]").forEach(b => b.addEventListener("click", () => b.closest(".tact").classList.add("dismissed")));
  log.scrollTop = log.scrollHeight;
  const dr = c.el.querySelector(".dr-b"); if (dr) dr.scrollTop = dr.scrollHeight;
}

function bubble(m, first) {
  if (m.role === "event") return `<div class="tmsg event">${icon("bolt")}<span>${esc(m.text)}</span></div>`;
  const a = m.action;
  const card = a && a.do === "redo" ? `<div class="tact ${a.state}">
      <div class="tact-t">${icon("refresh")}<b>Redo: ${esc(a.stage_title)}</b></div>
      <div class="tact-d">${esc(a.feedback)}</div>
      <div class="tact-s">${a.steps} step${a.steps === 1 ? "" : "s"} will run again, using model credits.</div>
      ${a.state === "proposed" ? `<div class="tact-a"><button class="btn sm pri" data-confirm="${esc(m.id)}">${icon("play")} Start</button>
        <button class="btn sm" data-dismiss>Not now</button></div>` : `<div class="tact-a s">${icon("check")} Started</div>`}</div>` : "";
  return `<div class="tmsg ${m.role === "user" ? "user" : "agent"}">
    <div class="tmsg-b">${esc(m.text).replace(/\n/g, "<br>")}</div>${card}
    <div class="tmsg-m">${m.role === "user" ? "You" : esc(first)} · ${ago(m.created_at)}</div></div>`;
}

async function send() {
  const c = current, box = c.el.querySelector("#tcText"), btn = c.el.querySelector("#tcSend");
  const text = box.value.trim();
  if (!text || btn.disabled) return;
  if (!c.thread) return;
  box.value = ""; btn.disabled = true;
  c.thread.messages.push({ role: "user", text, created_at: new Date().toISOString() });
  paint();
  const log = c.el.querySelector("#tcLog");
  log.insertAdjacentHTML("beforeend", `<div class="tmsg agent pending"><span class="jv-dots"><i></i><i></i><i></i></span>
    <span class="s">${esc(c.agent.name.split(/\s+/)[0])} is thinking…</span></div>`);
  log.scrollTop = log.scrollHeight;
  try {
    await apiPost("/api/agency/agents/chat", { agent: c.agent.id, project_id: c.project, text });
  } catch (e) {
    toast(String(e.message || e), true);
    box.value = text;
  }
  btn.disabled = false;
  if (current === c) { await load(); box.focus(); }
}

async function confirm(btn) {
  btn.disabled = true;
  try {
    const out = await apiPost("/api/agency/agents/confirm", { message_id: btn.dataset.confirm });
    toast(out.event.text);
    await refresh();
    await load();
    window.dispatchEvent(new CustomEvent("gem:project-changed", { detail: { project_id: current.project } }));
  } catch (e) { btn.disabled = false; toast(String(e.message || e), true); }
}

export { closeDrawer as closeTeamChat };
