// ============================================================
// COMPUTER USE 2.0 — an AI operates a browser on this PC for the agency.
// It works two ways in one task: through the page's structure (read the
// page, click or type by element number, fetch a URL's raw response) and
// through the screen (screenshots, clicks at a point) for anything visual.
// Three brains: Claude and ChatGPT (paid API calls; they see and read), and
// Local on Ollama (free and private; it reads only). The browser is isolated:
// no logins, and it cannot reach this computer. A session only ever starts
// from the Start button, and ChatGPT's safety checks wait for the operator.
// ============================================================
import { apiGet, apiPost, esc, icon, tag, ago, toast, store, emptyState, errBox, skeleton } from "/app/q/js/core.js";

const PRESETS = [
  { t: "Who ranks on Bing", url: "https://www.bing.com",
    task: "Search Bing for \"emergency plumber austin\" and list the top 5 organic results in order: site name, URL and page title. Note any map pack or AI answer shown above them." },
  { t: "Check an AI answer", url: "https://duckduckgo.com",
    task: "Search DuckDuckGo for \"best landscaping company in sacramento\" and report whether an AI-generated answer appears, what it says, and which sites it cites." },
  { t: "Walk our navigation", url: "",
    task: "Click through every link in the main navigation menu. For each, report whether the page loaded, its heading, and anything broken or empty." },
  { t: "Crawl basics", url: "",
    task: "Fetch this site's robots.txt and sitemap.xml directly. Report what robots.txt allows and blocks, and how many URLs the sitemap lists. Then read the homepage and report its title, meta description, canonical and main heading." },
  { t: "Headers & redirects", url: "",
    task: "Fetch this site's homepage directly, starting from the http:// address. Report each redirect hop, the final status, and the cache-control, content-type, server and strict-transport-security headers." },
  { t: "Contact path", url: "",
    task: "Find how a visitor would contact this business. Report every contact method visible (phone, email, form, chat) and how many clicks from the homepage each takes. Do not submit any form." },
];

const BRAIN = { auto: "Auto", claude: "Claude", chatgpt: "ChatGPT", local: "Local" };
const STATE_TAG = { starting: ["Starting", "t-blue"], running: ["Working", "t-blue"], waiting: ["Needs you", "t-warn"],
                    done: ["Done", "t-ok"], failed: ["Failed", "t-crit"], stopped: ["Stopped", "t-idle"] };
const KIND_ICON = { navigate: "globe", screenshot: "eye", zoom: "search", type: "doc", key: "key", scroll: "layers",
                    left_click: "cursor", right_click: "cursor", double_click: "cursor", mouse_move: "cursor",
                    left_click_drag: "cursor", read_page: "doc", click_element: "cursor", type_into: "doc",
                    go_back: "chevL", fetch_url: "link", safety: "shield",
                    say: "ai", think: "ai", note: "alert", error: "alert" };
const SHOW_RESULT = { read_page: "Show what it read", fetch_url: "Show the response" };

let poll = 0;
const pick = { brain: "auto" };

/* Model reports use light markdown. Escape first, then allow only headings,
   bold, inline code and bullet lists, so page text quoted in a report can
   never become markup. */
function prose(text) {
  const lines = esc(text || "").split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    const item = line.match(/^\s*[-*•]\s+(.*)$/);
    if (item) { html += (inList ? "" : "<ul>") + `<li>${item[1]}</li>`; inList = true; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    const head = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (head) { html += `<p class="cu-h">${head[1]}</p>`; continue; }
    html += line.trim() ? `<p>${line}</p>` : "";
  }
  return html + (inList ? "</ul>" : "");
}

export default async function computerPage(el, id) {
  clearInterval(poll);
  const sid = id && /^[a-f0-9]{12}$/.test(id) ? id : "";
  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Computer use</h1>
      <p>An AI operates a browser on this computer for you: checks what search and AI answer engines show, walks a site like a visitor, fetches robots.txt, sitemaps and headers. It never signs in, buys, posts or solves bot checks, and it cannot reach this computer's own services.</p></div></div>
    <div id="cuSlot">${skeleton(3)}</div></div>`;
  const slot = document.getElementById("cuSlot");
  if (sid) return viewSession(slot, sid);
  let st;
  try { st = await apiGet("/api/agency/computer/status"); }
  catch (e) { slot.innerHTML = errBox(e); return; }
  drawComposer(slot, st);
}

function projectOptions() {
  const ps = ((store.overview && store.overview.projects) || []).filter(p => p.url);
  return ps.map(p => `<option value="${esc(p.url)}">${esc(p.name)} · ${esc(p.url)}</option>`).join("");
}

/* ---------------- which brain drives, in plain words ---------------- */
function brainState(st) {
  const by = Object.fromEntries((st.brains || []).map(b => [b.id, b]));
  const ok = b => b && b.connected && !b.failing;
  return { by, ok, any: (st.brains || []).some(ok) };
}

function brainLine(st) {
  const { by, ok } = brainState(st);
  const b = by[pick.brain];
  const local = by.local;
  const localWhy = local && !local.connected ? (local.why || "no tool-calling Ollama model is installed") : "";
  if (pick.brain === "local") return ok(local)
    ? `Runs ${esc(local.model)} on this computer: <b>free</b> and private. It reads pages instead of looking at them, so it is slower and weaker on visual checks.`
    : `Local isn't available: ${esc(localWhy || local.failing)}.`;
  if (b) return ok(b) ? `${BRAIN[pick.brain]} (${esc(b.model)}) drives every step. Paid API calls; it reads pages and looks at the screen.`
    : `${BRAIN[pick.brain]} is ${b.connected ? "failing right now" : "not connected"}. Pick Auto or another brain.`;
  const order = ["claude", "chatgpt"].filter(k => ok(by[k])).concat(ok(local) ? ["local"] : []);
  if (!order.length) return "Every brain failed recently. It will still try, in case one has recovered.";
  const names = order.map(k => k === "local" ? "Local (free)" : BRAIN[k]);
  const skipped = ["claude", "chatgpt"].filter(k => !ok(by[k])).map(k => `${BRAIN[k]} ${by[k] && by[k].connected ? "is failing" : "isn't connected"}`);
  return `${names[0]} drives` + (names.length > 1 ? `; ${names.slice(1).join(", then ")} take${names.length > 2 ? "" : "s"} over if it can't start` : "")
    + "." + (skipped.length ? ` (${skipped.join("; ")}.)` : "");
}

function brainDot(b) {
  const cls = !b || !b.connected ? "off" : b.failing ? "bad" : "ok";
  return `<i class="cu-dot ${cls}" aria-hidden="true"></i>`;
}

async function drawComposer(slot, st) {
  let incoming = "", incomingUrl = "";
  try {
    incoming = sessionStorage.getItem("gem-computer-task") || "";
    incomingUrl = sessionStorage.getItem("gem-computer-url") || "";
    sessionStorage.removeItem("gem-computer-task"); sessionStorage.removeItem("gem-computer-url");
  } catch { /* none */ }
  const { by } = brainState(st);

  slot.innerHTML = `
    ${!st.ready ? `<div class="errbox" style="margin-bottom:16px"><b>Not ready</b>${esc(st.problems.join(". "))}.</div>` : ""}
    <div class="grid">
      <section class="panel s8"><div class="panel-h"><div><h2>New task</h2>
          <div class="sub">Computer use 2.0: it reads the page's structure and fetches URLs directly, and looks at the screen when a task needs eyes</div></div></div>
        <div class="panel-b"><form id="cuForm" autocomplete="off">
          <div class="fld"><label id="cuBrainL">Brain</label>
            <div class="seg-ctl" role="radiogroup" aria-labelledby="cuBrainL">${Object.entries(BRAIN).map(([k, v]) =>
              `<button type="button" role="radio" data-brain="${k}" class="${pick.brain === k ? "on" : ""}" aria-checked="${pick.brain === k}">
                ${k === "auto" ? "" : brainDot(by[k])}${v}${k === "local" ? ` <span class="cu-free">free</span>` : ""}</button>`).join("")}</div>
            <div class="hint" id="cuBrainLine">${brainLine(st)}</div></div>
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
  slot.querySelectorAll("[data-brain]").forEach(b => b.addEventListener("click", () => {
    pick.brain = b.dataset.brain;
    slot.querySelectorAll("[data-brain]").forEach(x => {
      x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b);
    });
    slot.querySelector("#cuBrainLine").innerHTML = brainLine(st);
  }));
  slot.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => {
    const p = PRESETS[+b.dataset.preset];
    f.elements.task.value = p.task;
    if (p.url) f.elements.url.value = p.url;
    (p.url || f.elements.url.value ? f.elements.task : f.elements.url).focus();
  }));
  slot.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { location.hash = b.dataset.go; }));
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
      const r = await apiPost("/api/agency/computer/start", { task, url, brain: pick.brain,
        max_steps: +f.elements.max_steps.value || 20, visible: f.elements.visible.checked });
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
      <div class="s">${r.brain_used ? esc(BRAIN[r.brain_used] || r.brain_used) + " · " : ""}${esc(r.url)} · ${r.turns} step${r.turns === 1 ? "" : "s"} · ${ago(r.created_at)}</div></div>
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
        <div class="cu-safety" id="cuSafety" hidden role="alert"></div>
        <div class="panel-f" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn" id="cuStop" style="display:none">${icon("pause")} Stop</button>
          <button class="btn" data-go="computer">${icon("plus")} New task</button>
          <span class="s" id="cuMeta" style="margin-left:auto"></span></div></section>
      <section class="panel cu-log"><div class="panel-h"><div><h2>Steps</h2><div class="sub" id="cuLogSub">What it did and why</div></div></div>
        <ol class="panel-b cu-steps" id="cuSteps"></ol></section>
      <section class="panel cu-report" id="cuReport" hidden><div class="panel-h"><div><h2>Report</h2>
          <div class="sub">Findings from what it read and saw</div></div></div>
        <div class="panel-b"><div class="cu-report-body" id="cuReportBody"></div></div></section>
    </div>`;
  slot.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { location.hash = b.dataset.go; }));
  let since = 0, lastShot = "", safetyShown = "";
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
  const safety = slot.querySelector("#cuSafety");
  const drawSafety = s => {
    const key = s.status === "waiting" ? JSON.stringify(s.pending) : "";
    if (key === safetyShown) return;
    safetyShown = key;
    safety.hidden = !key;
    if (!key) { safety.innerHTML = ""; return; }
    safety.innerHTML = `<div class="cu-safety-h">${icon("shield")}<b>ChatGPT paused for your decision</b></div>
      <ul>${s.pending.map(p => `<li>${esc(p.message)}${p.code ? ` <span class="s">(${esc(p.code.replace(/_/g, " "))})</span>` : ""}</li>`).join("")}</ul>
      <p class="s">Approve only if this is what you asked for. Declining stops the session. It waits up to 10 minutes.</p>
      <div class="cu-safety-a"><button class="btn pri" data-decide="1">${icon("check")} Approve and continue</button>
        <button class="btn" data-decide="0">${icon("x")} Decline and stop</button></div>`;
    safety.querySelectorAll("[data-decide]").forEach(b => b.addEventListener("click", async () => {
      safety.querySelectorAll("button").forEach(x => { x.disabled = true; });
      try { await apiPost("/api/agency/computer/decide", { id, approve: b.dataset.decide === "1" }); }
      catch (e) { toast(String(e.message || e), true); safety.querySelectorAll("button").forEach(x => { x.disabled = false; }); }
    }));
  };

  async function tick() {
    if (!document.getElementById("cuSteps")) { clearInterval(poll); return; }
    let s;
    try { s = (await apiGet(`/api/agency/computer/session?id=${id}&since=${since}`)).session; }
    catch (e) { clearInterval(poll); slot.innerHTML = errBox(e); return; }
    const live = ["starting", "running", "waiting"].includes(s.status);
    const [t, c] = STATE_TAG[s.status] || [s.status, "t-idle"];
    const brain = s.brain_used ? (BRAIN[s.brain_used] || s.brain_used) + (s.model ? ` (${s.model})` : "") : "Choosing a brain";
    const cost = s.brain_used === "local" ? "free, on this computer" : `${(s.tokens_in + s.tokens_out).toLocaleString()} tokens`;
    document.getElementById("cuTitle").textContent = s.task.length > 90 ? s.task.slice(0, 90) + "…" : s.task;
    document.getElementById("cuState").innerHTML = tag(t, c);
    document.getElementById("cuMeta").textContent = `${brain} · ${s.turns}/${s.max_steps} steps · ${cost}`;
    document.getElementById("cuLogSub").textContent = s.brain_used ? `What ${BRAIN[s.brain_used] || "it"} did and why` : "What it did and why";
    stopBtn.style.display = live ? "" : "none";   // .btn sets display, which beats the hidden attribute
    slot.querySelector(".cu-frame").classList.toggle("is-live", live);
    drawSafety(s);
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
        <div style="min-width:0"><div class="cu-t">${step.kind === "say" ? prose(step.text) : esc(step.text)}</div>
        ${SHOW_RESULT[step.kind] && step.result ? `<details class="cu-more"><summary>${SHOW_RESULT[step.kind]}</summary><pre class="cu-out">${esc(step.result)}</pre></details>` : ""}
        ${step.shot ? `<button class="linkbtn" data-shot="${esc(step.shot)}">View this screen</button>` : ""}</div>`;
      list.appendChild(li);
      li.scrollIntoView({ block: "nearest" });
    }
    list.querySelectorAll("[data-shot]").forEach(b => b.onclick = () => showShot(b.dataset.shot));
    since = s.step_count;
    if (!live) {
      clearInterval(poll);
      if (!lastShot && document.getElementById("cuWait")) document.getElementById("cuWait").textContent = "No screen was captured.";
      const rep = document.getElementById("cuReport");
      rep.hidden = false;
      document.getElementById("cuReportBody").innerHTML = s.report
        ? prose(s.report)
        : `<div class="errbox" style="margin:0"><b>No report</b>${esc(s.error || "The session ended before a report was written.")}</div>`;
    }
  }
  await tick();
  clearInterval(poll);
  poll = setInterval(tick, 1500);
}

export function computerTeardown() { clearInterval(poll); }
