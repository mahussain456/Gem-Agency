// ============================================================
// COMPUTER USE — Claude operates a real browser for the agency.
// It looks at the screen, then clicks, types, scrolls and navigates
// until the task is done, and reports what it saw. Runs in an isolated
// Chrome window: no logins, no access to this machine, no purchases,
// posts or sign-ins. Starting a session spends Claude credits, so it
// only ever starts from the Start button.
// ============================================================
import { apiGet, apiPost, esc, icon, tag, ago, toast, store, emptyState, errBox, skeleton } from "/app/q/js/core.js";

const PRESETS = [
  { t: "Who ranks on Bing", task: "Search Bing for \"emergency plumber austin\" and list the top 5 organic results in order: site name, URL and page title. Note any map pack or AI answer shown above them.", url: "https://www.bing.com" },
  { t: "Walk our navigation", task: "Click through every link in the main navigation menu. For each, report whether the page loaded, its heading, and anything broken or empty.", url: "" },
  { t: "Check an AI answer", task: "Search DuckDuckGo for \"best landscaping company in sacramento\" and report whether an AI-generated answer appears, what it says, and which sites it cites.", url: "https://duckduckgo.com" },
  { t: "Competitor FAQ & schema", task: "Find this site's FAQ or help content. Report the questions it answers and whether answers are visible without clicking. Note any on-page signs of FAQ structured data.", url: "" },
  { t: "Contact path", task: "Find how a visitor would contact this business. Report every contact method visible (phone, email, form, chat) and how many clicks from the homepage each takes. Do not submit any form.", url: "" },
];

let poll = 0;
const STATE_TAG = { starting: ["Starting", "t-blue"], running: ["Working", "t-blue"], done: ["Done", "t-ok"],
                    failed: ["Failed", "t-crit"], stopped: ["Stopped", "t-idle"] };
const KIND_ICON = { navigate: "globe", screenshot: "eye", zoom: "search", type: "doc", key: "key", scroll: "layers",
                    say: "ai", think: "ai", note: "alert", error: "alert" };

export default async function computerPage(el, id) {
  clearInterval(poll);
  const sid = id && /^[a-f0-9]{12}$/.test(id) ? id : "";
  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Computer use</h1>
      <p>Claude operates a real browser for you: checks what search and AI answer engines show, walks a site like a visitor, verifies pages. It works in an isolated window with no logins, can't reach this computer, and never signs in, buys, posts or solves bot checks.</p></div></div>
    <div id="cuSlot">${skeleton(3)}</div></div>`;
  const slot = document.getElementById("cuSlot");
  let st;
  try { st = await apiGet("/api/agency/computer/status"); }
  catch (e) { slot.innerHTML = errBox(e); return; }
  if (sid) return viewSession(slot, sid);
  drawComposer(slot, st);
}

function projectOptions() {
  const ps = ((store.overview && store.overview.projects) || []).filter(p => p.url);
  return ps.map(p => `<option value="${esc(p.url)}">${esc(p.name)} · ${esc(p.url)}</option>`).join("");
}

async function drawComposer(slot, st) {
  const failing = st.claude_failing;
  let incoming = "";
  let incomingUrl = "";
  try {
    incoming = sessionStorage.getItem("gem-computer-task") || "";
    incomingUrl = sessionStorage.getItem("gem-computer-url") || "";
    sessionStorage.removeItem("gem-computer-task"); sessionStorage.removeItem("gem-computer-url");
  } catch { /* none */ }
  slot.innerHTML = `
    ${!st.ready ? `<div class="errbox" style="margin-bottom:16px"><b>Not ready</b>${esc(st.problems.join(". "))}.</div>` : ""}
    ${st.ready && failing ? `<div class="recovery"><div><b>Claude's last call failed</b><p>${esc(failing)}</p>
        <p>Computer use runs only on Claude. It will fail the same way until that is fixed.</p></div>
        <button class="btn" data-go="models">${icon("bot")} Open Models</button></div>` : ""}
    <div class="grid">
      <section class="panel s8"><div class="panel-h"><div><h2>New task</h2>
          <div class="sub">Runs on ${esc(st.model || "Claude")} · each step is a model turn with a screenshot, so a long task costs more</div></div></div>
        <div class="panel-b"><form id="cuForm" autocomplete="off">
          <div class="fld"><label for="cuTask">What should it do?</label>
            <textarea id="cuTask" name="task" rows="4" placeholder="e.g. Search Bing for 'roof repair denver' and list the top 5 results">${esc(incoming)}</textarea></div>
          <div class="fld"><label for="cuUrl">Start at</label>
            <input id="cuUrl" name="url" type="url" placeholder="https://www.bing.com" spellcheck="false" value="${esc(incomingUrl)}">
            ${projectOptions() ? `<select id="cuProj" style="margin-top:7px" aria-label="Start at one of your websites">
                <option value="">…or start at one of your websites</option>${projectOptions()}</select>` : ""}
            <div class="hint">Public web pages only. Addresses on this machine or a private network are blocked.</div></div>
          <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end">
            <div class="fld" style="width:140px;margin:0"><label for="cuSteps">Step limit</label>
              <input id="cuSteps" name="max_steps" type="number" min="3" max="60" value="20"></div>
            <label class="cz-row" style="padding:0 0 8px;gap:8px"><input type="checkbox" name="visible"> Show the browser window on this computer</label>
          </div>
          <div class="errbox" data-err hidden style="margin:12px 0 0"></div>
          <div style="display:flex;gap:8px;margin-top:14px">
            <button class="btn pri" type="submit" ${st.ready ? "" : "disabled"}>${icon("play")} Start</button>
          </div>
        </form></div></section>
      <section class="panel s4"><div class="panel-h"><div><h2>Ideas</h2><div class="sub">Load one, then adjust</div></div></div>
        <div class="panel-b" style="display:grid;gap:8px">
          ${PRESETS.map((p, i) => `<button class="decision-card" type="button" data-preset="${i}" style="text-align:left;margin:0;cursor:pointer">
            <h3 style="margin:0 0 4px">${esc(p.t)}</h3><p style="margin:0">${esc(p.task.slice(0, 110))}…</p></button>`).join("")}
        </div></section>
      <section class="panel s12"><div class="panel-h"><div><h2>Recent sessions</h2></div></div>
        <div class="panel-b flush" id="cuList">${skeleton(2)}</div></section>
    </div>`;

  const f = slot.querySelector("#cuForm");
  slot.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => {
    const p = PRESETS[+b.dataset.preset];
    f.elements.task.value = p.task;
    if (p.url) f.elements.url.value = p.url;
    f.elements.task.focus();
  }));
  const proj = slot.querySelector("#cuProj");
  if (proj) proj.addEventListener("change", () => { if (proj.value) f.elements.url.value = proj.value; });
  f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const err = f.querySelector("[data-err]");
    const task = f.elements.task.value.trim(), url = f.elements.url.value.trim();
    const say = m => { err.textContent = m; err.hidden = false; };
    if (task.length < 5) return say("Describe the task in a sentence.");
    if (!/^https?:\/\//i.test(url)) return say("Give a starting address that begins with http:// or https://.");
    err.hidden = true;
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const r = await apiPost("/api/agency/computer/start",
        { task, url, max_steps: +f.elements.max_steps.value || 20, visible: f.elements.visible.checked });
      location.hash = `computer/${r.session.id}`;
    } catch (e) { say(String(e.message || e)); btn.disabled = false; }
  });
  drawList(slot.querySelector("#cuList"));
}

async function drawList(box) {
  let rows = [];
  try { rows = (await apiGet("/api/agency/computer/sessions")).sessions || []; }
  catch (e) { box.innerHTML = errBox(e); return; }
  if (!rows.length) { box.innerHTML = emptyState({ ic: "eye", title: "No sessions yet", body: "Start a task above. Every step, screenshot and the final report are kept here." }); return; }
  box.innerHTML = rows.map(r => {
    const [t, c] = STATE_TAG[r.status] || [r.status, "t-idle"];
    return `<button class="row click" data-go="computer/${esc(r.id)}" style="width:100%;text-align:left">
      <div class="g" style="min-width:0"><div class="t">${esc(r.task.slice(0, 120))}</div>
      <div class="s">${esc(r.url)} · ${r.turns} step${r.turns === 1 ? "" : "s"} · ${ago(r.created_at)}</div></div>
      <span class="r">${tag(t, c)}</span></button>`;
  }).join("");
  box.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { location.hash = b.dataset.go; }));
}

/* ---------------- a live or finished session ---------------- */
async function viewSession(slot, id) {
  slot.innerHTML = `<div class="cu-live">
      <section class="panel cu-screen"><div class="panel-h"><div style="min-width:0"><h2 id="cuTitle">Session</h2>
          <div class="sub" id="cuUrlNow"></div></div>
        <span class="r" id="cuState"></span></div>
        <div class="cu-frame"><div class="cu-wait" id="cuWait">Opening the browser…</div>
          <div class="cu-scan" aria-hidden="true"></div></div>
        <div class="panel-f" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn" id="cuStop" style="display:none">${icon("pause")} Stop</button>
          <button class="btn" data-go="computer">${icon("plus")} New task</button>
          <span class="s" id="cuMeta" style="margin-left:auto"></span></div></section>
      <section class="panel cu-log"><div class="panel-h"><div><h2>Steps</h2><div class="sub">What Claude did and why</div></div></div>
        <ol class="panel-b cu-steps" id="cuSteps"></ol></section>
      <section class="panel cu-report" id="cuReport" hidden><div class="panel-h"><div><h2>Report</h2>
          <div class="sub">Claude's findings, from what it saw on screen</div></div></div>
        <div class="panel-b"><div class="cu-report-body" id="cuReportBody"></div></div></section>
    </div>`;
  slot.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { location.hash = b.dataset.go; }));
  let since = 0, lastShot = "";
  const showShot = path => {
    // the image exists only once there is a real screenshot to show
    let img = document.getElementById("cuShot");
    if (!img) {
      img = document.createElement("img");
      img.id = "cuShot";
      img.alt = "What the browser shows right now";
      document.getElementById("cuWait").replaceWith(img);
    }
    img.src = `/api/agency/screenshot?path=${encodeURIComponent(path)}`;
  };
  const stopBtn = slot.querySelector("#cuStop");
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try { await apiPost("/api/agency/computer/stop", { id }); toast("Stopping after the current action."); }
    catch (e) { toast(String(e.message || e), true); stopBtn.disabled = false; }
  });

  async function tick() {
    if (!document.getElementById("cuSteps")) { clearInterval(poll); return; }
    let s;
    try { s = (await apiGet(`/api/agency/computer/session?id=${id}&since=${since}`)).session; }
    catch (e) { clearInterval(poll); slot.innerHTML = errBox(e); return; }
    const live = s.status === "starting" || s.status === "running";
    const [t, c] = STATE_TAG[s.status] || [s.status, "t-idle"];
    document.getElementById("cuTitle").textContent = s.task.length > 90 ? s.task.slice(0, 90) + "…" : s.task;
    document.getElementById("cuState").innerHTML = tag(t, c);
    document.getElementById("cuMeta").textContent =
      `${s.model || "Claude"} · ${s.turns}/${s.max_steps} steps · ${(s.tokens_in + s.tokens_out).toLocaleString()} tokens`;
    stopBtn.style.display = live ? "" : "none";   // .btn sets display, which beats the hidden attribute
    slot.querySelector(".cu-frame").classList.toggle("live", live);
    if (s.latest_shot && s.latest_shot !== lastShot) {
      lastShot = s.latest_shot;
      showShot(s.latest_shot);
    }
    const list = document.getElementById("cuSteps");
    for (const step of s.steps || []) {
      if (step.url) document.getElementById("cuUrlNow").textContent = step.url;
      const li = document.createElement("li");
      li.className = `cu-step k-${step.kind}`;
      li.innerHTML = `<span class="cu-ic">${icon(KIND_ICON[step.kind] || "arrowR")}</span>
        <div><div class="cu-t">${esc(step.text)}</div>${step.shot ? `<button class="linkbtn" data-shot="${esc(step.shot)}">View this screen</button>` : ""}</div>`;
      list.appendChild(li);
      li.scrollIntoView({ block: "nearest" });
    }
    list.querySelectorAll("[data-shot]").forEach(b => b.onclick = () => showShot(b.dataset.shot));
    since = s.step_count;
    if (!live) {
      clearInterval(poll);
      if (!lastShot) document.getElementById("cuWait").textContent = "No screen was captured.";
      const rep = document.getElementById("cuReport");
      rep.hidden = false;
      document.getElementById("cuReportBody").innerHTML = s.report
        ? esc(s.report).replace(/\n/g, "<br>")
        : `<div class="errbox" style="margin:0"><b>No report</b>${esc(s.error || "The session ended before Claude wrote one.")}</div>`;
    }
  }
  await tick();
  clearInterval(poll);
  poll = setInterval(tick, 1500);
}

export function computerTeardown() { clearInterval(poll); }
