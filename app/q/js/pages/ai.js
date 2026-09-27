// ============================================================
// AI COMMAND CENTRE — the Agent Bridge, grown up.
// Same transport as the original bridge widget (/api/bridge/send, SSE),
// same target validation. Adds project context, conversation history
// and a command library. No parallel fake AI surface.
// ============================================================
import {
  store, apiGet, esc, icon, ago, tag, emptyState, errBox, bindGo, toast, skeleton, shapeProject,
} from "/app/q/js/core.js";

const TARGETS = ["@orchestrator", "@scout", "@scribe", "@reach", "@dev", "@lumen",
                 "@antigravity", "@chatgpt", "@forge", "@rank", "@stitch", "@all"];

const LIBRARY = [
  { g: "Build", items: [
    "Build a premium website for a landscaping company in Sacramento",
    "Redesign this homepage to feel more premium",
    "Make this page responsive",
  ]},
  { g: "Analyse", items: [
    "Analyze globalsync-ai.com and summarise the biggest problems",
    "Run a full SEO audit",
    "Research our competitors",
    "Show me failed agents",
  ]},
  { g: "Grow", items: [
    "Find backlink opportunities",
    "Generate 20 keyword ideas",
    "Optimize this website for AI search",
    "Fix the highest-impact SEO issues",
    "Create an SEO article",
  ]},
];

let convo = [];   // in-page history for this session

export default async function ai(el, id) {
  const prefill = id ? decodeURIComponent(id.split("?")[0]) : "";
  const cards = ((store.overview && store.overview.projects) || []).map(shapeProject);

  let history = [];
  try { history = (await apiGet("/api/bridge/history?limit=30")).history || []; } catch (e) { history = []; }

  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Ask the agency</h1>
      <p>Send work to the fleet. Each agent thinks with the agency's brain (Claude, then ChatGPT, then local Ollama) and replies stream back live.</p></div>
      <div class="acts"><span class="prov">${icon("shield")} same brain the pipeline uses</span></div></div>

    <div class="grid">
      <section class="panel s8" style="min-height:520px">
        <div class="panel-h">
          <div><h2>Conversation</h2><div class="sub" id="aiSub">Pick an agent, describe the outcome</div></div>
          <span class="r"><button class="btn sm" id="clearBtn">${icon("x")} Clear</button></span>
        </div>
        <div class="panel-b" id="aiLog" style="flex:1;overflow:auto;min-height:300px;max-height:52vh"></div>
        <div class="panel-f">
          <form id="aiForm" style="display:flex;gap:9px;align-items:flex-end;flex-wrap:wrap;width:100%">
            <div class="fld" style="width:158px;margin:0">
              <label for="aiTarget">Agent</label>
              <select id="aiTarget">${TARGETS.map(t => `<option>${t}</option>`).join("")}</select>
            </div>
            <div class="fld" style="width:190px;margin:0">
              <label for="aiProject">Project context</label>
              <select id="aiProject"><option value="">— none —</option>
                ${cards.map(c => `<option value="${esc(c.p.id)}">${esc(c.p.name)}</option>`).join("")}</select>
            </div>
            <div class="fld" style="flex:1;min-width:220px;margin:0">
              <label for="aiMsg">Command</label>
              <input id="aiMsg" placeholder="Describe what you want done…" autocomplete="off">
            </div>
            <button class="btn pri" type="submit" style="height:38px" id="aiSend">${icon("send")} Send</button>
          </form>
        </div>
      </section>

      <div class="s4">
        <section class="panel" style="margin-bottom:var(--gap)">
          <div class="panel-h"><div><h2>Command library</h2>
            <div class="sub">Click to load into the field</div></div></div>
          <div class="panel-b">
            ${LIBRARY.map(sec => `
              <div style="font:600 10.5px/1 var(--sans);letter-spacing:.13em;text-transform:uppercase;
                color:var(--tx-3);margin:11px 0 7px">${esc(sec.g)}</div>
              ${sec.items.map(t => `<button class="chip" data-lib="${esc(t)}"
                style="width:100%;justify-content:flex-start;height:auto;padding:8px 11px;margin-bottom:6px;
                text-align:left;font-size:12.5px;line-height:1.4">${esc(t)}</button>`).join("")}`).join("")}
          </div>
        </section>
        <section class="panel">
          <div class="panel-h"><div><h2>Recent bridge traffic</h2>
            <div class="sub">Logged server-side</div></div></div>
          <div class="panel-b flush" style="max-height:260px;overflow:auto">
            ${history.length ? history.slice(0, 14).map(h => `<div class="agrow">
              <span class="agdot ${h.status === "failed" ? "failed" : "done"}"></span>
              <span class="agname">${esc(String(h.target || "").replace("@", ""))}</span>
              <span class="g"><span class="s" style="margin:0">${esc(h.status || "sent")} · ${ago(h.ts || h.created_at)}</span></span>
            </div>`).join("")
            : emptyState({ ic: "send", title: "No bridge traffic yet" })}
          </div>
        </section>
      </div>
    </div>
  </div>`;

  const log = document.getElementById("aiLog");
  const form = document.getElementById("aiForm");
  const msg = document.getElementById("aiMsg");
  const target = document.getElementById("aiTarget");
  const projSel = document.getElementById("aiProject");

  // deep-link: #ai/<urlencoded prompt>?to=@agent
  const to = id && id.includes("?to=") ? decodeURIComponent(id.split("?to=")[1]) : "";
  if (to && TARGETS.includes(to)) target.value = to;
  if (prefill) msg.value = prefill;

  paintLog();
  el.querySelectorAll("[data-lib]").forEach(b =>
    b.addEventListener("click", () => { msg.value = b.dataset.lib; msg.focus(); }));
  document.getElementById("clearBtn").addEventListener("click", () => { convo = []; paintLog(); });

  function paintLog() {
    if (!convo.length) {
      log.innerHTML = emptyState({ ic: "ai", title: "Ready when you are",
        body: "Pick an agent, add project context if it helps, and describe the outcome you want. The reply streams in as the agent produces it." });
      return;
    }
    log.innerHTML = convo.map(m => m.role === "you"
      ? `<div style="display:flex;justify-content:flex-end;margin:11px 0">
          <div style="max-width:78%;background:var(--ac-w);border:1px solid var(--line-2);
            border-radius:12px 12px 3px 12px;padding:9px 13px;font:400 13.5px/1.5 var(--sans)">${esc(m.text)}</div></div>`
      : `<div style="margin:11px 0">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
            <span class="agdot ${m.done ? "done" : "working"}"></span>
            <span class="agname">${esc(m.agent)}</span>${m.via ? `<span class="s" style="margin:0">via ${esc(m.via)}</span>` : ""}
            ${m.error ? tag("failed", "t-crit") : m.done ? tag("complete", "t-ok") : tag("streaming", "t-blue")}</div>
          <div style="background:var(--raised);border:1px solid var(--line);border-radius:3px 12px 12px 12px;
            padding:11px 14px;font:400 13px/1.6 var(--sans);white-space:pre-wrap;word-break:break-word;
            color:${m.error ? "var(--crit)" : "var(--tx-2)"}">${esc(m.text || "…")}</div></div>`).join("");
    log.scrollTop = log.scrollHeight;
  }

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const text = msg.value.trim();
    if (!text) return;
    const tgt = target.value;
    const proj = projSel.value ? cards.find(c => c.p.id === projSel.value) : null;

    // Project context is prepended as plain text — the bridge takes a message,
    // so this is the honest way to give the agent the same facts we hold.
    const full = proj
      ? `[project: ${proj.p.name}${proj.p.url ? " — " + proj.p.url : ""}; stage ${proj.done}/${proj.total}]\n${text}`
      : text;

    convo.push({ role: "you", text });
    const reply = { role: "agent", agent: tgt.replace("@", ""), text: "", done: false, error: false };
    convo.push(reply);
    msg.value = "";
    paintLog();

    const btn = document.getElementById("aiSend");
    btn.disabled = true;
    try {
      const r = await fetch("/api/bridge/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: tgt, message: full }),
      });
      if (!r.ok || !r.body) throw new Error("bridge returned " + r.status);
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop() || "";
        for (const ev of events) {
          const lines = ev.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5).trim());
          if (!lines.length) continue;
          try {
            const p = JSON.parse(lines.join(""));
            if (p.type === "token" && p.data) reply.text += p.data;
            else if (p.type === "meta" && p.data) {
              const d = p.data;
              reply.via = `${d.label || d.provider}${d.model ? " · " + d.model : ""}${
                (d.fell_back_from || []).length ? " (after " + d.fell_back_from.map(a => a.provider).join(", ") + " failed)" : ""}`;
            }
            else if (p.type === "done" && p.data) {
              // the stream ends with the reason when nothing could answer
              reply.error = true;
              reply.text += `${reply.text ? "\n\n" : ""}[error] ${typeof p.data === "string" ? p.data : JSON.stringify(p.data)}`;
            }
            // status events (started / complete) drive the tag beside the agent's
            // name; pasting them into the reply echoed the question back twice
            else if (p.type === "status") { /* shown by the tag */ }
          } catch { reply.text += lines.join(""); }
          paintLog();
        }
      }
      reply.done = true;
    } catch (err) {
      reply.error = true; reply.done = true;
      reply.text += `\n\n[error] ${String(err.message || err)}\nCheck the Models page: is Claude, ChatGPT or Ollama connected?`;
    }
    btn.disabled = false;
    paintLog();
  });

  bindGo(el);
}
