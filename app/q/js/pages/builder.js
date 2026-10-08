// ============================================================
// AI WEBSITE BUILDER — "Build with Gem Agency".
// Wired to the real pipeline engine (/api/agency/pipeline/start).
// When a run is live this becomes a build-progress view driven by
// real stage_runs rows, not a scripted animation.
// ============================================================
import {
  store, apiGet, apiPost, esc, icon, ago, dur, plain, emptyState, errBox,
  tag, prog, toast, refresh, bindGo, skeleton,
  agentName,
} from "/app/q/js/core.js";

const MODES = [
  { id: "website_build", label: "Website", hint: "Idea to a built, audited site" },
  { id: "seo_campaign", label: "Growth campaign", hint: "Audit your website and plan measurable improvements" },
];

/** The deck card. Becomes Build Progress whenever a run is executing. */
export function buildCard(cards) {
  const running = store.runs.find(r => r.state === "running" || r.state === "awaiting_approval");
  if (running) {
    return `<section class="panel" id="buildPanel" data-run="${esc(running.id)}">
      <div class="panel-h"><div><h2>Build in progress</h2>
        <div class="sub">${esc(running.project_name || "untitled")} · ${esc(running.playbook)}</div></div>
        <span class="r"><span class="live"></span></span></div>
      <div class="panel-b" id="buildProgress">${skeleton(4)}</div>
      <div class="panel-f" style="display:flex;gap:9px;flex-wrap:wrap">
        <button class="btn" data-go="project/${esc(running.project_id)}">${icon("arrowR")} Open project</button>
        <button class="btn dang" id="cancelRun">${icon("x")} Stop run</button></div>
    </section>`;
  }
  const clients = (store.overview && store.overview.clients) || [];
  return `<section class="panel">
    <div class="panel-h"><div><h2>Build with the agency</h2>
      <div class="sub">What would you like to create?</div></div></div>
    <div class="panel-b">
      <form id="buildForm">
        <input type="hidden" name="playbook" value="website_build">
        <div class="chips" style="margin-bottom:12px">
          ${MODES.map((m, i) => `<button type="button" class="chip ${i === 0 ? "on" : ""}"
            data-pb="${m.id}" aria-pressed="${i === 0}" title="${esc(m.hint)}">${esc(m.label)}</button>`).join("")}
        </div>
        <div class="fld">
          <label for="buildBrief">Describe your website and the result you want</label>
          <textarea id="buildBrief" name="brief" rows="5" required
            placeholder="Create a premium website for a landscaping company in Sacramento."></textarea>
        </div>
        <p class="draft-note" id="draftStatus">Your draft is saved on this device as you type.</p>
        <div class="fld" id="urlFld" style="display:none">
          <label for="bUrl">Website URL</label>
          <input id="bUrl" name="url" type="url" placeholder="https://example.com">
          <div class="hint">The audit stage fetches this page for real — it must be reachable.</div>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
          <div class="fld" style="flex:1;min-width:170px;margin:0">
            <label for="bName">Project name</label>
            <input id="bName" name="name" placeholder="Optional — taken from the brief if blank">
          </div>
          ${clients.length ? `<div class="fld" style="width:180px;margin:0">
            <label for="bClient">Client</label>
            <select id="bClient" name="client_id">
              <option value="">— none —</option>
              ${clients.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}
            </select></div>` : ""}
          <button class="btn pri" type="submit" style="height:38px">${icon("build")} Build website</button>
        </div>
      </form>
    </div>
  </section>`;
}

/** Poll the live run and paint real stage progress. */
export async function paintProgress(root) {
  const panel = root.querySelector("#buildPanel");
  if (!panel) return;
  const slot = panel.querySelector("#buildProgress");
  const runId = panel.dataset.run;
  let run;
  try { run = (await apiGet(`/api/agency/pipeline/run?id=${encodeURIComponent(runId)}`)).run; }
  catch (e) { slot.innerHTML = errBox(e); return; }

  const stages = run.stages || [];
  const done = stages.filter(s => s.state === "done").length;
  slot.innerHTML = `
    <div style="display:flex;align-items:center;gap:11px;margin-bottom:13px">
      ${prog(stages.length ? Math.round((done / stages.length) * 100) : 0)}
      <span class="mono s" style="margin:0;white-space:nowrap">${done}/${stages.length}</span>
    </div>
    <div style="max-height:250px;overflow:auto;margin:0 -18px;padding:0 18px">
    ${stages.map(s => {
      const k = s.state === "done" ? "done" : s.state === "running" ? "now"
        : (s.state === "failed" || s.state === "error") ? "fail" : "";
      const mark = k === "done" ? "✓" : k === "fail" ? "✕" : k === "now" ? "" : "";
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0">
        <span style="width:17px;height:17px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;
          font:600 10px/1 var(--mono);
          background:${k === "done" ? "var(--ok)" : k === "fail" ? "var(--crit)" : k === "now" ? "var(--blue)" : "transparent"};
          border:1.5px solid ${k ? "transparent" : "var(--line-2)"};
          color:${k ? "var(--bg)" : "var(--tx-3)"}">${mark}</span>
        <span style="flex:1;min-width:0;font:400 12.5px/1.4 var(--sans);
          color:${k === "done" ? "var(--tx-2)" : k === "now" ? "var(--tx)" : "var(--tx-3)"};
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(plain(s.stage_id, s.title))}</span>
        ${k === "now" ? `<span class="live"></span>` : ""}
        <span class="s mono" style="margin:0;flex-shrink:0">${s.seconds ? dur(s.seconds) : ""}</span>
      </div>`;
    }).join("")}</div>
    ${run.error ? `<div class="errbox" style="margin-top:11px"><b>Stopped</b>${esc(run.error)}</div>` : ""}`;

  const cancel = panel.querySelector("#cancelRun");
  if (cancel && !cancel._w) {
    cancel._w = true;
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try { await apiPost(`/api/agency/pipeline/cancel?id=${encodeURIComponent(runId)}`); toast("Run stopped."); await refresh(); }
      catch (e) { cancel.disabled = false; toast(String(e.message || e), true); }
    });
  }
}

/* ---------------- full page ---------------- */
export default async function builderPage(el, _id, ctx) {
  const cards = (store.overview && store.overview.projects) || [];
  const books = Object.values(store.playbooks);
  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>What should we build?</h1>
      <p>One brief. A connected path from your idea to a website ready for review.</p></div></div>
    <div class="journey" aria-label="Website lifecycle"><span class="current">Brief</span><span>Research</span><span>Design & content</span><span>Build & QA</span><span>Launch review</span><span>Grow</span></div>
    <div class="builder-layout">
      <div id="bSlot"></div>
      <div class="builder-help"><h2>From idea to a working website</h2>
        <div class="builder-step"><span class="step-num">01</span><div><b>Plan the right website</b><p>Define your audience, page structure, copy and search topics from your brief.</p></div></div>
        <div class="builder-step"><span class="step-num">02</span><div><b>Build and check</b><p>Create the website, inspect it in a browser and save the outputs with your project.</p></div></div>
        <div class="builder-step"><span class="step-num">03</span><div><b>Review, launch and improve</b><p>Review the result, follow the launch plan, then connect search data to measure progress.</p></div></div>
      <details class="builder-details"><summary>Explore the detailed workflows</summary><section class="panel">
        <div class="panel-h"><div><h2>What each pipeline does</h2>
          <div class="sub">Completed steps and outputs stay with your project</div></div></div>
        <div class="panel-b">
          ${books.map(b => `<div style="margin-bottom:17px">
            <div style="font:600 13px/1.3 var(--sans);margin-bottom:3px">${esc(b.name)}</div>
            <div class="s" style="margin-bottom:8px">${esc(b.summary || "")}</div>
            ${(b.stages || []).map((s, i) => `<div style="display:flex;gap:9px;align-items:center;padding:3px 0">
              <span class="mono s" style="margin:0;width:17px;flex-shrink:0">${i + 1}</span>
              <span style="flex:1;min-width:0;font:400 12.5px/1.4 var(--sans);color:var(--tx-2)">${esc(plain(s.id, s.title))}</span>
              ${s.search ? `<span class="tag t-idle" title="Search work runs only on Claude or ChatGPT">Claude / ChatGPT</span>` : ""}
              ${tag(s.kind === "gate" ? "you" : s.kind === "tool" ? "check" : (s.agent ? agentName(s.agent) : "agent"),
                    s.kind === "gate" ? "t-warn" : s.kind === "tool" ? "t-ok" : "t-blue")}
            </div>`).join("")}
          </div>`).join("") || emptyState({ title: "No playbooks registered" })}
        </div>
      </section></details></div>
    </div>
    <div class="grid"><section class="panel s12">
      <div class="panel-h"><div><h2>Recent runs</h2><div class="sub">Every pipeline this workspace has executed</div></div></div>
      <div class="panel-b flush">${runRows()}</div>
    </section></div>
  </div>`;

  const slot = document.getElementById("bSlot");
  slot.innerHTML = buildCard(cards);
  wireBuild(slot, _id);
  await paintProgress(slot);
  bindGo(el);
}

/* Start a pipeline from the build form: creates the project and opens it. */
function wireBuild(root, requestedMode = '') {
  const form = root.querySelector("#buildForm");
  if (!form) return;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('gem-build-draft') || '{}'); } catch {}
  for (const [name,value] of Object.entries(saved)) { const field = form.elements.namedItem(name); if(field) field.value=value; }
  const incoming = sessionStorage.getItem('gem-builder-brief');
  if (incoming) { form.elements.brief.value=incoming; sessionStorage.removeItem('gem-builder-brief'); }
  const saveDraft = () => { localStorage.setItem('gem-build-draft', JSON.stringify(Object.fromEntries(new FormData(form).entries()))); };
  form.addEventListener('input',saveDraft);
  form.addEventListener('change',saveDraft);
  function mode(pb) {
    root.querySelectorAll('[data-pb]').forEach(x=>{x.classList.toggle('on',x.dataset.pb===pb);x.setAttribute('aria-pressed',String(x.dataset.pb===pb));});
    form.elements.playbook.value=pb;
    root.querySelector('#urlFld').style.display=pb==='seo_campaign'?'':'none';
    form.elements.url.required=pb==='seo_campaign';
    form.querySelector('button[type=submit]').textContent=pb==='seo_campaign'?'Start growth campaign':'Build website';
    saveDraft();
  }
  mode(MODES.some(m=>m.id===requestedMode)?requestedMode:incoming?'website_build':saved.playbook==='seo_campaign'?'seo_campaign':'website_build');
  root.querySelectorAll("[data-pb]").forEach(b => b.addEventListener("click", () => {
    mode(b.dataset.pb);
  }));
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const v = Object.fromEntries(new FormData(form).entries());
    const btn = form.querySelector("button[type=submit]");
    const err = form.querySelector(".errbox");
    btn.disabled = true; btn.textContent = "Starting…";
    if (err) err.remove();
    try {
      const res = await apiPost("/api/agency/pipeline/start", v);
      localStorage.removeItem('gem-build-draft');
      sessionStorage.removeItem('gem-overview-idea');
      toast("Pipeline started. Agents are working.");
      await refresh();
      location.hash = `project/${res.run.project_id}`;
    } catch (ex) {
      btn.disabled = false; btn.textContent = v.playbook==='seo_campaign'?'Start growth campaign':'Build website';
      form.insertAdjacentHTML("beforeend", errBox(ex));
    }
  });
}

function runRows() {
  if (!store.runs.length) return emptyState({ ic: "build", title: "No pipeline has run yet" });
  const CLS = { completed: "t-ok", running: "t-blue", awaiting_approval: "t-warn",
                failed: "t-crit", stopped: "t-crit", cancelled: "t-idle", queued: "t-idle" };
  const NAMES = { website_build: "Website build", seo_campaign: "SEO campaign" };
  const test = n => /(?:^zz[\s_-]|\btest\b|\bselftest\b|\be2e\b)/i.test(n || "");
  const runs = store.runs.filter(r => !test(r.project_name));
  if (!runs.length) return emptyState({ ic: "build", title: "No pipeline has run yet" });
  return runs.slice(0, 12).map(r => `<button class="row click" data-go="project/${esc(r.project_id)}">
    <span class="thumb sq">${icon("build")}</span>
    <span class="g"><span class="t">${esc(r.project_name || "untitled")}</span>
      <span class="s">${esc(NAMES[r.playbook] || r.playbook)} · ${r.done}/${r.total} stages · started ${ago(r.created_at)}</span></span>
    <span style="width:110px;flex-shrink:0">${prog(r.total ? Math.round(100 * r.done / r.total) : 0,
      r.state === "completed" ? "ok" : ["failed", "stopped", "cancelled"].includes(r.state) ? "crit" : "")}</span>
    ${tag((s => s.charAt(0).toUpperCase() + s.slice(1))(String(r.state).replace(/_/g, " ")), CLS[r.state] || "t-idle")}</button>`).join("");
}
