// ============================================================
// PROJECTS — list + full project workspace.
// Tabs exist only where the project really has that data:
// Overview, Pipeline, Content (artifacts), SEO (audit findings),
// Preview (built HTML), Activity.
// ============================================================
import {
  store, apiGet, apiPost, esc, icon, ago, dur, plain, titleCase, tag, prog, ring,
  emptyState, errBox, skeleton, bindGo, drawer, toast, refresh, modal,
  shapeProject, loadAudits, areaHealth, AREAS, initials,
  agentName,
} from "/app/q/js/core.js";
import { openTeamChat, face } from "/app/q/js/team.js";
import { newProject } from "/app/q/js/boot.js";
import {PHASES, deliveryPhase, recoveryMessage, isTestProject, safeWebsiteUrl} from '/app/q/js/workflow.js';

/* ---------------- list ---------------- */
export default async function projects(el) {
  const ov = store.overview;
  const cards = ((ov && ov.projects) || []).map(shapeProject);

  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Your websites.</h1>
      <p>${cards.length} project${cards.length === 1 ? "" : "s"} · progress and health come from the pipeline engine, not estimates.</p></div>
      <div class="acts"><button class="btn" id="newP">${icon("plus")} Add existing project</button><button class="btn pri" data-go="builder">New website</button></div></div>
    <div class="project-filter"><input id="projectSearch" type="search" aria-label="Search websites" placeholder="Search websites or clients…"><label><input id="projectTests" type="checkbox"> Show test projects</label><span class="s" id="projectFilterCount"></span></div>
    ${cards.length ? `<div class="tblwrap"><table class="tbl"><thead><tr>
        <th>Project</th><th>Client</th><th>Domain</th><th>Stage</th><th style="width:150px">Progress</th>
        <th>Status</th><th class="num">SEO</th><th>Next action</th><th>Updated</th>
      </tr></thead><tbody id="pRows">${cards.map(rowHTML).join("")}</tbody></table></div>`
    : `<section class="panel"><div class="panel-b">${emptyState({ ic: "proj",
        title: "No projects yet",
        body: "Describe an outcome in the builder and Gem Agency creates the project and runs the pipeline against it.",
        cta: `<button class="btn pri" data-go="builder">${icon("build")} Open the builder</button>` })}</div></section>`}
  </div>`;

  document.getElementById("newP").addEventListener("click", newProject);
  const search=el.querySelector('#projectSearch'),tests=el.querySelector('#projectTests');
  function filterRows(){let visible=0;el.querySelectorAll('[data-p]').forEach(row=>{const card=cards.find(c=>c.p.id===row.dataset.p);const match=(tests.checked||!isTestProject(card.p))&&`${card.p.name} ${card.p.client_name||''}`.toLowerCase().includes(search.value.toLowerCase());row.hidden=!match;if(match)visible++;});el.querySelector('#projectFilterCount').textContent=`${visible} website${visible===1?'':'s'} shown`;}
  search.addEventListener('input',filterRows);tests.addEventListener('change',filterRows);filterRows();
  el.querySelectorAll("[data-p]").forEach(r =>
    r.addEventListener("click", () => { location.hash = `project/${r.dataset.p}`; }));

  // SEO column filled from verified audits once they load
  const withUrl = cards.filter(c => c.p.url);
  if (withUrl.length) {
    await loadAudits(withUrl.map(c => c.p.id));
    withUrl.forEach(c => {
      const a = store.audits.get(c.p.id);
      const cell = el.querySelector(`[data-seo="${c.p.id}"]`);
      if (!cell) return;
      cell.innerHTML = a
        ? `<b style="color:${a.score >= 80 ? "var(--ok)" : a.score >= 55 ? "var(--warn)" : "var(--crit)"}">${a.score}</b>`
        : `<span style="color:var(--tx-3)">—</span>`;
    });
  }
  bindGo(el);
}

function rowHTML(c) {
  return `<tr class="click" data-p="${esc(c.p.id)}">
    <td><span style="display:flex;align-items:center;gap:10px">
      <span class="thumb sq">${esc(initials(c.p.name || "?"))}</span>
      <a href="#project/${encodeURIComponent(c.p.id)}" style="font-weight:600">${esc(c.p.name)}</a></span></td>
    <td style="color:var(--tx-2)">${esc(c.p.client_name || "—")}</td>
    <td class="mono cell-host" style="font-size:12px;color:var(--tx-2)" title="${esc(c.p.url || "")}">${
      c.p.url ? esc(String(c.p.url).replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")) : "—"}</td>
    <td style="color:var(--tx-2);white-space:nowrap">${esc((PHASES.find(x => x.id === deliveryPhase(c)) || {}).label || "Research")}</td>
    <td><span style="display:flex;align-items:center;gap:9px">
      ${prog(c.pct, c.failed ? "crit" : c.finished ? "ok" : "")}
      <span class="mono" style="font-size:11.5px;color:var(--tx-3);white-space:nowrap">${c.done}/${c.total}</span></span></td>
    <td>${tag(c.status.k, c.status.c, true)}</td>
    <td class="num" data-seo="${esc(c.p.id)}">${c.p.url ? `<span class="skel" style="display:inline-block;width:22px;height:12px"></span>` : "—"}</td>
    <td style="color:var(--tx-2);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.next)}</td>
    <td class="s" style="margin:0;white-space:nowrap">${ago(c.p.updated_at)}</td>
  </tr>`;
}

/* ---------------- workspace ---------------- */
export async function projectPage(el, id) {
  const ov = store.overview;
  const p = ((ov && ov.projects) || []).find(x => x.id === id);
  if (!p) {
    el.innerHTML = `<div class="page"><div class="errbox"><b>Project not found</b>
      It may have been deleted. <button class="linkbtn" data-go="projects">Back to projects</button></div></div>`;
    bindGo(el); return;
  }
  const c = shapeProject(p);

  const [artsRes, tasksRes, apprRes] = await Promise.allSettled([
    apiGet(`/api/agency/artifacts?project_id=${encodeURIComponent(id)}`),
    apiGet(`/api/agency/tasks?project_id=${encodeURIComponent(id)}`),
    apiGet("/api/agency/approvals"),
  ]);
  const artifacts = artsRes.status === "fulfilled" ? (artsRes.value.artifacts || []) : [];
  const tasks = tasksRes.status === "fulfilled" ? (tasksRes.value.tasks || []) : [];
  const approvals = apprRes.status === "fulfilled"
    ? (apprRes.value.approvals || []).filter(a => a.project_id === id) : [];

  let run = null;
  if (c.run) { try { run = (await apiGet(`/api/agency/pipeline/run?id=${encodeURIComponent(c.run.id)}`)).run; } catch (e) {} }
  if (p.url) await loadAudits([id]);
  const audit = store.audits.get(id) || null;

  const TABS = [
    ["overview", "Overview"],
    ["pipeline", "Pipeline"],
    audit ? ["seo", "SEO / AEO / GEO"] : null,
    artifacts.filter(a => a.kind !== "html").length ? ["content", "Content"] : null,
    artifacts.some(a => a.kind === "html") ? ["preview", "Preview"] : null,
    ["activity", "Activity"],
  ].filter(Boolean);

  const recovery=recoveryMessage(run?.error);
  const phase=deliveryPhase(c);
  el.innerHTML = `<div class="page">
    <div class="phead">
      <div style="min-width:0">
        <button class="linkbtn" data-go="projects" style="margin-bottom:7px">${icon("chevL")} Projects</button>
        <h1>${esc(p.name)}</h1>
        <p>${esc(p.client_name || "No client")}${p.url ? " · " : ""}${p.url
          ? `<a href="${esc(safeWebsiteUrl(p.url)||'#')}" target="_blank" rel="noopener" class="linkbtn">${esc(String(p.url).replace(/^https?:\/\//, ""))} ${icon("ext")}</a>` : ""}</p>
      </div>
      <div class="acts">
        ${tag(c.status.k, c.status.c, true)}
        ${c.run && c.run.state === "failed" ? `<button class="btn" id="resumeBtn">${icon("play")} Resume saved step</button>` : ""}
        <button class="btn" id="editBtn">${icon("gear")} Edit</button>
        ${c.finished && p.status !== "live" && p.playbook !== "seo_campaign"
          ? `<button class="btn" id="requestChanges">${icon("doc")} Request changes</button>
             <button class="btn pri" id="markLive">${icon("rocket")} Mark as live</button>`
          : `<button class="btn pri" id="requestChanges">${icon("doc")} Request changes</button>`}
      </div>
    </div>

    <div class="journey" aria-label="Website lifecycle">${PHASES.map((s,i)=>`<span class="${s.id===phase?'current':i<PHASES.findIndex(x=>x.id===phase)?'complete':''}">${s.label}</span>`).join('')}</div>
    ${c.failed && run && run.error ? `<div class="recovery"><div><b>${esc(recovery.title)}</b><p>${esc(recovery.detail)}</p><details><summary>Technical details</summary>${esc(run.error)}</details></div><button class="btn" data-go="integrations">${icon('plug')} Check connections</button></div>` : ""}

    <div class="kpis">
      <div class="kpi" style="--kpi-ink:var(--blue);cursor:default"><div class="k">${icon("layers")}Progress</div>
        <div class="v">${c.pct}<em>%</em></div><div class="c">${c.done} of ${c.total} stages</div></div>
      <div class="kpi" style="--kpi-ink:var(--cyan);cursor:default"><div class="k">${icon("build")}Next action</div>
        <div style="font:500 15px/1.4 var(--sans);margin-top:9px">${esc(c.next)}</div>
        <div class="c">${approvals.some(a => a.status === "pending") ? "waiting on you"
          : c.agent ? "handled by " + esc(agentName(c.agent)) : c.finished ? "waiting on you" : "no agent assigned"}</div></div>
      <div class="kpi" style="--kpi-ink:var(--ok);cursor:default"><div class="k">${icon("seo")}Audit score</div>
        <div class="v">${audit ? audit.score : "—"}</div>
        <div class="c">${audit ? `verified ${ago(audit._at)}` : "no audit run"}</div></div>
      <div class="kpi" style="--kpi-ink:var(--warn);cursor:default"><div class="k">${icon("approve")}Approvals</div>
        <div class="v">${approvals.filter(a => a.status === "pending").length}</div>
        <div class="c">${approvals.length} total on this project</div></div>
      <div class="kpi" style="--kpi-ink:var(--lilac,var(--elec));cursor:default"><div class="k">${icon("doc")}Outputs</div>
        <div class="v">${artifacts.length}</div><div class="c">artifacts produced</div></div>
    </div>

    <div class="tabs" id="pTabs">${TABS.map(([k, l], i) =>
      `<button id="tab-${k}" data-t="${k}" role="tab" aria-controls="pTab" aria-selected="${i===0}" class="${i === 0 ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="pTab" role="tabpanel" tabindex="0"></div>
  </div>`;

  const slot = document.getElementById("pTab");
  const views = {
    overview: () => overviewTab(p, c, run, tasks, approvals, audit),
    pipeline: () => pipelineTab(c, run),
    seo: () => seoTab(audit, p),
    content: () => contentTab(artifacts.filter(a => a.kind !== "html")),
    preview: () => previewTab(artifacts.filter(a => a.kind === "html"), p),
    activity: () => activityTab(p),
  };
  function show(k) {
    slot.setAttribute('aria-labelledby',`tab-${k}`);
    el.querySelectorAll('[data-t]').forEach(x=>{x.tabIndex=x.dataset.t===k?0:-1;});
    slot.innerHTML = views[k] ? views[k]() : "";
    slot.querySelectorAll("[data-art]").forEach(b =>
      b.addEventListener("click", () => openArtifact(b.dataset.art)));
    slot.querySelectorAll('[data-device]').forEach(b=>b.addEventListener('click',()=>{slot.querySelector('.device-frame').classList.toggle('mobile',b.dataset.device==='mobile');slot.querySelectorAll('[data-device]').forEach(x=>{x.classList.toggle('pri',x===b);x.setAttribute('aria-pressed',String(x===b));});}));
    const version=slot.querySelector('#previewVersion');
    if(version)version.addEventListener('change',()=>{const src=`/api/agency/artifact/raw?id=${encodeURIComponent(version.value)}`;slot.querySelector('.device-frame iframe').src=src;slot.querySelector('#openPreview').href=src;});
    bindGo(slot);
    if (slot.querySelector("#apBody")) drawAutopilot(slot.querySelector("#apBody"), id);
    if (slot.querySelector("#dpPanel")) drawDesigns(slot.querySelector("#dpPanel"), id);
    slot.querySelectorAll("[data-chat]").forEach(b => b.addEventListener("click", () => openTeamChat(b.dataset.chat, id)));
  }
  el.querySelectorAll("[data-t]").forEach(b => b.addEventListener("click", () => {
    el.querySelectorAll("[data-t]").forEach(x => {x.classList.toggle("on", x === b);x.setAttribute('aria-selected',String(x===b));});
    show(b.dataset.t);
  }));
  const designWaiting=approvals.some(a=>a.status==='pending'&&a.kind==='design');
  const defaultTab=!designWaiting&&artifacts.some(a=>a.kind==='html')?'preview':'overview';
  el.querySelectorAll('[data-t]').forEach(x=>{x.classList.toggle('on',x.dataset.t===defaultTab);x.setAttribute('aria-selected',String(x.dataset.t===defaultTab));});
  el.querySelector('#pTabs').setAttribute('role','tablist');
  el.querySelector('#pTabs').addEventListener('keydown',e=>{
    const tabs=[...el.querySelectorAll('[data-t]')],index=tabs.indexOf(document.activeElement);
    if(index<0||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();
    const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    tabs[next].focus();tabs[next].click();
  });
  show(defaultTab);
  document.getElementById('requestChanges').addEventListener('click',()=>modal({title:`Request changes — ${p.name}`,fields:[{name:'title',label:'What should change?',required:true},{name:'notes',label:'Details and acceptance criteria',type:'textarea'}],submitLabel:'Save change request',onSubmit:async v=>{await apiPost('/api/agency/tasks',{...v,project_id:id,status:'todo',priority:'high'});toast('Change request saved to this project.');await refresh();projectPage(el,id);}}));

  const liveBtn = document.getElementById("markLive");
  if (liveBtn) liveBtn.addEventListener("click", () => modal({
    title: `Mark ${p.name} as live`,
    note: "Do this once the site is published. The live address is what search measurement and the overview count.",
    fields: [{ name: "url", label: "Live website address", value: p.url || "", required: true, placeholder: "https://www.example.com" }],
    submitLabel: "Mark as live",
    onSubmit: async v => {
      await apiPost(`/api/agency/projects/update?id=${encodeURIComponent(id)}`, { status: "live", url: v.url });
      toast(`${p.name} is live.`);
      await refresh();
      projectPage(el, id);
    },
  }));
  document.getElementById("editBtn").addEventListener("click", () => modal({
    title: "Edit project",
    fields: [
      { name: "name", label: "Name", value: p.name, required: true },
      { name: "url", label: "Website URL", value: p.url || "" },
      { name: "due_date", label: "Due date", type: "date", value: p.due_date || "" },
      { name: "health", label: "Health", type: "select", value: p.health, options:
        [["on_track", "On track"], ["at_risk", "At risk"], ["blocked", "Blocked"]].map(([v, l]) => ({ value: v, label: l })) },
      { name: "brief", label: "Brief", type: "textarea", value: p.brief || "" },
      { name: "budget_cents", label: "Project budget (cents; planning only)", type: "number", value: p.budget_cents || 0 },
    ],
    submitLabel: "Save",
    onSubmit: async v => {
      await apiPost(`/api/agency/projects/update?id=${encodeURIComponent(id)}`, v);
      await refresh(); toast("Project updated."); projectPage(el, id);
    },
  }));
  const rb = document.getElementById("resumeBtn");
  if (rb) rb.addEventListener("click", async () => {
    rb.disabled = true;
    try { await apiPost(`/api/agency/pipeline/resume?id=${encodeURIComponent(c.run.id)}`); toast("Run resumed."); await refresh(); projectPage(el, id); }
    catch (e) { rb.disabled = false; toast(String(e.message || e), true); }
  });
  bindGo(el);
  const again = ev => { if (ev.detail?.project_id === id && document.body.contains(el)) projectPage(el, id); };
  window.addEventListener("gem:project-changed", again, { once: true });
}

/* ---------------- the team on this project ---------------- */
function teamRows(run) {
  const people = new Map();
  for (const s of run.stages || []) {
    if (!s.agent || s.kind === "tool" && !["stitch"].includes(s.agent) || s.kind === "gate") continue;
    const who = (store.overview?.agents || []).find(a => a.id === s.agent);
    if (!who) continue;
    const row = people.get(who.id) || { who, steps: [] };
    row.steps.push(s);
    people.set(who.id, row);
  }
  if (!people.size) return emptyState({ ic: "users", title: "No one has worked on this yet" });
  return [...people.values()].map(({ who, steps }) => {
    const now = steps.find(s => s.state === "running");
    const done = steps.filter(s => s.state === "done");
    const line = now ? `Working on ${now.title}` : done.length === steps.length ? `Done: ${done.map(s => s.title).join(", ")}`
      : done.length ? `Done ${done.length} of ${steps.length}: next ${steps.find(s => s.state !== "done").title}` : `Up next: ${steps[0].title}`;
    return `<div class="row team-row">${face(who.id, who.name)}<span class="g"><span class="t">${esc(who.name)}</span>
      <span class="s">${esc(who.role || "")} · ${esc(line)}</span></span>
      <button class="btn sm" data-chat="${esc(who.id)}">${icon("send")} Message</button></div>`;
  }).join("");
}

/* ---------------- pick a design: Sofia's two directions, built by Kwame ---------------- */
async function drawDesigns(box, id) {
  let d;
  try { d = await apiGet(`/api/agency/design?project_id=${encodeURIComponent(id)}`); }
  catch (e) { return; }
  if (!box.isConnected) return;
  const opts = d.options || [];
  if (d.working) {
    box.hidden = false;
    box.innerHTML = `<div class="panel-h"><div><h2>New designs on the way</h2>
        <div class="sub">Sofia is drafting two directions, then Kwame builds each one as a real homepage. This takes a few minutes.</div></div></div>
      <div class="panel-b dpick-grid">${[0, 1].map(() => `<div class="dopt"><div class="dframe dframe-wait"></div>${skeleton(2)}</div>`).join("")}</div>`;
    setTimeout(() => { if (box.isConnected) drawDesigns(box, id); }, 15000);
    return;
  }
  if (!opts.length) { box.hidden = true; return; }
  box.hidden = false;
  const pending = d.pending;
  const src = o => `/api/agency/design/file?project_id=${encodeURIComponent(id)}&name=${encodeURIComponent(o.file_name)}`;
  const swatch = o => Object.entries(o.direction?.palette || {}).slice(0, 6).map(([k, v]) =>
    /^#[0-9a-f]{3,8}$/i.test(v) ? `<i style="background:${v}" title="${esc(k)} ${esc(v)}"></i>` : "").join("");
  const type = o => [o.direction?.type?.display, o.direction?.type?.text].filter(Boolean).join(" / ");
  box.innerHTML = `<div class="panel-h"><div>
      <h2>${pending ? "Pick a design" : d.chosen ? "The chosen design" : "Design samples"}</h2>
      <div class="sub">${pending ? "Two homepages, two different directions. The build follows the one you pick."
        : d.chosen ? `Design ${esc(d.chosen.toUpperCase())} was picked for the build.` : ""}${d.round > 1 ? ` Round ${d.round}.` : ""}</div></div>
      <span class="r dpick-dev" role="group" aria-label="Preview size">
        <button class="btn sm pri" data-dev="desktop" aria-pressed="true">Desktop</button>
        <button class="btn sm" data-dev="mobile" aria-pressed="false">Mobile</button></span></div>
    <div class="panel-b dpick-grid">${opts.map(o => `<article class="dopt ${d.chosen === o.key ? "chosen" : ""}">
        <div class="dframe"><iframe src="${src(o)}" title="Design ${esc(o.key.toUpperCase())}: ${esc(o.name)}" loading="lazy"
          sandbox="allow-scripts" tabindex="-1"></iframe></div>
        <div class="dopt-b">
          <div class="dopt-h"><span class="dopt-k">${esc(o.key.toUpperCase())}</span><h3>${esc(o.name)}</h3>
            ${d.chosen === o.key ? tag("Chosen", "t-ok") : ""}</div>
          <p>${esc(o.idea || "")}</p>
          <div class="dopt-m"><span class="dsw" aria-label="Palette">${swatch(o)}</span>${type(o) ? `<span class="s">${esc(type(o))}</span>` : ""}</div>
          <div class="dopt-a">${pending ? `<button class="btn pri" data-pick="${esc(o.key)}">${icon("check")} Use this design</button>` : ""}
            <a class="btn" href="${src(o)}" target="_blank" rel="noopener">${icon("ext")} Full size</a></div>
        </div></article>`).join("")}</div>
    ${pending ? `<div class="panel-f dpick-ask">
      <label for="dpFeedback">Not quite right? Tell Sofia what to change and she'll come back with two new directions.</label>
      <div class="dpick-ask-r"><textarea id="dpFeedback" rows="2" placeholder="For example: warmer and friendlier, less corporate, show families, keep the dark option but make it calmer"></textarea>
        <span class="dpick-ask-b"><button class="btn" id="dpRedo">${icon("refresh")} Ask Sofia for new designs</button>
        <button class="linkbtn" id="dpTalk">${icon("send")} Talk it through with Sofia</button></span></div></div>` : ""}`;
  // each sample renders at a real 1440px desktop width, scaled to fit its card
  const fit = () => box.querySelectorAll(".dframe").forEach(f => f.style.setProperty("--k", (f.clientWidth / 1440).toFixed(4)));
  fit(); new ResizeObserver(fit).observe(box);
  box.querySelectorAll("[data-dev]").forEach(b => b.addEventListener("click", () => {
    box.classList.toggle("mobile", b.dataset.dev === "mobile");
    box.querySelectorAll("[data-dev]").forEach(x => { x.classList.toggle("pri", x === b); x.setAttribute("aria-pressed", String(x === b)); });
  }));
  box.querySelectorAll("[data-pick]").forEach(b => b.addEventListener("click", async () => {
    box.querySelectorAll("[data-pick]").forEach(x => x.disabled = true);
    try {
      await apiPost("/api/agency/design/pick", { approval_id: pending.id, choice: b.dataset.pick });
      toast(`Design ${b.dataset.pick.toUpperCase()} it is. The build carries on.`);
      await refresh(); drawDesigns(box, id);
    } catch (e) { box.querySelectorAll("[data-pick]").forEach(x => x.disabled = false); toast(String(e.message || e), true); }
  }));
  const redo = box.querySelector("#dpRedo");
  if (redo) redo.addEventListener("click", async () => {
    const fb = box.querySelector("#dpFeedback").value.trim();
    if (!fb) { box.querySelector("#dpFeedback").focus(); toast("Tell Sofia what to change first.", true); return; }
    redo.disabled = true;
    try {
      await apiPost("/api/agency/runs/redo", { run_id: d.run_id, stage: "design_directions", feedback: fb });
      toast("Sofia is on it. Two new directions in a few minutes.");
      await refresh(); drawDesigns(box, id);
    } catch (e) { redo.disabled = false; toast(String(e.message || e), true); }
  });
  const talk = box.querySelector("#dpTalk");
  if (talk) talk.addEventListener("click", () => openTeamChat("lumen", id, { draft: box.querySelector("#dpFeedback").value.trim() }));
}

/* ---------------- autopilot: a scheduled SEO campaign for a live site ---------------- */
async function drawAutopilot(box, id) {
  let s;
  try { s = (await apiGet(`/api/agency/autopilot?project_id=${encodeURIComponent(id)}`)).autopilot; }
  catch (e) { box.innerHTML = errBox(e); return; }
  if (!box.isConnected) return;
  const every = { 7: "Every week", 14: "Every 2 weeks", 30: "Every month" };
  box.innerHTML = !s.has_url
    ? `<div class="s">Add the live website address (Edit) to run SEO campaigns on a schedule.</div>`
    : `<label class="ap-row"><input type="checkbox" id="apOn" ${s.enabled ? "checked" : ""}>
         <span><b>Run an SEO campaign automatically</b>
         <span class="s">Audits the live site, then plans fixes, content, AEO/GEO and outreach. Uses model credits each run.</span></span></label>
       <div class="ap-row2"><select id="apEvery" aria-label="How often">${[7, 14, 30].map(d =>
         `<option value="${d}" ${s.every_days === d ? "selected" : ""}>${every[d]}</option>`).join("")}</select>
         <span class="s">${s.enabled
           ? (s.next_at && new Date(s.next_at) > new Date() ? `Next run ${new Date(s.next_at).toLocaleDateString()}` : "Next run within 10 minutes")
           : "Off"}${s.last_run_at ? ` · last ${ago(s.last_run_at)}${s.last_note && s.last_note !== "started" ? ` (${esc(s.last_note)})` : ""}` : ""}</span></div>`;
  const save = async () => {
    try {
      await apiPost("/api/agency/autopilot", { project_id: id, enabled: box.querySelector("#apOn").checked,
        every_days: +box.querySelector("#apEvery").value });
      toast(box.querySelector("#apOn").checked ? "Autopilot on. Approvals still wait for you." : "Autopilot off.");
    } catch (e) { toast(String(e.message || e), true); }
    drawAutopilot(box, id);
  };
  const on = box.querySelector("#apOn"), ev = box.querySelector("#apEvery");
  if (on) on.addEventListener("change", save);
  if (ev) ev.addEventListener("change", save);
}

/* ---------------- tabs ---------------- */
function overviewTab(p, c, run, tasks, approvals, audit) {
  return `${p.playbook === "website_build" || (run && run.playbook === "website_build")
      ? `<section class="panel dpick" id="dpPanel" hidden></section>` : ""}
    <div class="grid">
    <section class="panel s7"><div class="panel-h"><div><h2>Brief</h2></div></div>
      <div class="panel-b"><div style="font:400 13.5px/1.65 var(--sans);color:var(--tx-2);white-space:pre-wrap">${
        esc(p.brief || "No brief recorded.")}</div>
        <dl class="kv" style="margin-top:16px">
          <dt>Type</dt><dd>${esc(titleCase(p.type))}</dd>
          <dt>Playbook</dt><dd>${esc(p.playbook || "none")}</dd>
          <dt>Health</dt><dd>${c.failed
            ? `<span style="color:var(--crit)">Blocked: pipeline stopped</span>`
            : esc(titleCase(p.health || "unknown"))}</dd>
          <dt>Due</dt><dd>${esc(p.due_date || "no date set")}</dd>
          <dt>Created</dt><dd>${ago(p.created_at)}</dd>
          <dt>Updated</dt><dd>${ago(p.updated_at)}</dd>
        </dl></div></section>
    <div class="s5">
      ${run ? `<section class="panel" style="margin-bottom:var(--gap)">
        <div class="panel-h"><div><h2>Your team on this project</h2><div class="sub">Ask anyone about their work, or for changes</div></div></div>
        <div class="panel-b flush">${teamRows(run)}</div></section>` : ""}
      <section class="panel" id="apPanel" style="margin-bottom:var(--gap)">
        <div class="panel-h"><div><h2>Autopilot</h2><div class="sub">A fresh SEO campaign on a schedule</div></div></div>
        <div class="panel-b" id="apBody">${skeleton(1)}</div></section>
      <section class="panel" style="margin-bottom:var(--gap)">
        <div class="panel-h"><div><h2>Approvals</h2></div></div>
        <div class="panel-b flush">${approvals.length ? approvals.slice(0, 5).map(a =>
          `<div class="row"><span class="g"><span class="t">${esc(a.title)}</span>
           <span class="s">${esc(titleCase(a.status))} · ${ago(a.created_at)}</span></span>
           ${tag(titleCase(a.status), a.status === "pending" ? "t-warn" : a.status === "approved" ? "t-ok" : "t-crit")}</div>`).join("")
          : emptyState({ ic: "approve", title: "No approvals raised" })}</div></section>
      <section class="panel">
        <div class="panel-h"><div><h2>Tasks</h2></div></div>
        <div class="panel-b flush">${tasks.length ? tasks.slice(0, 6).map(t =>
          `<div class="row"><span class="g"><span class="t">${esc(t.title)}</span>
           <span class="s">${esc(t.status)}${t.due_date ? " · due " + esc(t.due_date) : ""}</span></span></div>`).join("")
          : emptyState({ ic: "task", title: "No tasks on this project" })}</div></section>
    </div></div>`;
}

function pipelineTab(c, run) {
  const stages = (run && run.stages) || c.stages.map((s, i) => ({
    stage_id: s.id, title: s.title, kind: s.kind, agent: s.agent,
    state: i < c.done ? "done" : i === c.markIx ? "running" : "todo",
  }));
  if (!stages.length) return `<section class="panel"><div class="panel-b">${
    emptyState({ ic: "layers", title: "No pipeline has run for this project" })}</div></section>`;
  return `<section class="panel"><div class="panel-h">
      <div><h2>Pipeline</h2><div class="sub">${run ? `Run ${esc(String(run.id).slice(0, 8))} · ${esc(run.state)}` : "From the playbook"}</div></div></div>
    <div class="panel-b"><div class="tl">${stages.map((s, i) => {
      const k = s.state === "done" ? "done" : s.state === "running" ? "now"
        : (s.state === "failed" || s.state === "error") ? "fail" : "";
      return `<div class="tl-i ${k}"><span class="tl-m">${
        k === "done" ? "✓" : k === "fail" ? "✕" : k === "now" ? "●" : i + 1}</span>
        <div class="g"><div class="t">${esc(plain(s.stage_id, s.title))}</div>
        <div class="s">${k === "done" ? "Done" : k === "now" ? "Running now" : k === "fail" ? "Stopped here" : "Not started"}
          ${/* stage_runs.detail already reads "agent · duration · tokens", so printing
                agent and seconds beside it repeats every line. Prefer the detail. */ ""}
          ${s.detail ? " · " + esc(s.detail)
            : (s.agent ? " · " + esc(agentName(s.agent)) : s.kind === "gate" ? " · needs a human" : " · automatic check")
              + (s.seconds ? " · " + dur(s.seconds) : "")}</div>
        ${s.error ? `<div class="errbox" style="margin:7px 0 0">${esc(s.error)}</div>` : ""}</div></div>`;
    }).join("")}</div></div></section>`;
}

function seoTab(audit, p) {
  if (!audit) return "";
  const H = areaHealth([audit]);
  const open = (audit.findings || []).filter(f => f.severity !== "good");
  return `<div class="grid">
    <section class="panel s4"><div class="panel-h"><div><h2>Health</h2>
      <div class="sub">Verified fetch ${ago(audit._at)}</div></div></div>
      <div class="panel-b">
        <div style="display:flex;justify-content:center;padding:6px 0 16px">${ring(audit.score, "Overall", 92)}</div>
        ${AREAS.map(a => { const b = H[a.id]; return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <span class="s" style="margin:0;width:96px;color:var(--tx-2)">${a.name}</span>
          ${prog(b.score == null ? 0 : b.score, b.score == null ? "" : b.score >= 80 ? "ok" : b.score >= 55 ? "warn" : "crit")}
          <span class="mono" style="font-size:11.5px;width:28px;text-align:right;color:var(--tx-2)">${b.score == null ? "—" : b.score}</span>
        </div>`; }).join("")}
        <dl class="kv" style="margin-top:15px">
          <dt>Status</dt><dd>${audit.status}</dd>
          <dt>Load</dt><dd>${audit.load_ms}ms</dd>
          <dt>Title</dt><dd>${esc((audit.page && audit.page.title) || "missing")}</dd>
          <dt>Schema</dt><dd>${audit.page && audit.page.schema_types && audit.page.schema_types.length
            ? esc(audit.page.schema_types.join(", ")) : "none found"}</dd>
          ${/* self_audit grades a freshly built page and never fetches these,
                so "not checked" is the truthful reading, not "missing". */ ""}
          <dt>robots.txt</dt><dd>${!audit.site ? "not checked"
            : audit.site.robots && audit.site.robots.ok ? "found" : "missing"}</dd>
          <dt>sitemap.xml</dt><dd>${!audit.site ? "not checked"
            : audit.site.sitemap && audit.site.sitemap.ok ? `${audit.site.sitemap.urls} URLs` : "missing"}</dd>
          <dt>llms.txt</dt><dd>${!audit.site ? "not checked"
            : audit.site.llms && audit.site.llms.ok ? "published" : "missing"}</dd>
        </dl></div></section>
    <section class="panel s8"><div class="panel-h"><div><h2>Findings</h2>
      <div class="sub">${open.length} to fix · ${(audit.findings || []).length - open.length} passing</div></div>
      <span class="r"><button class="linkbtn" data-go="optimize">All projects ${icon("arrowR")}</button></span></div>
      <div class="panel-b flush">${open.length ? open.map(f => `<div class="find">
        <div class="hd">${tag(titleCase(f.severity), f.severity === "high" ? "t-crit" : f.severity === "medium" ? "t-warn" : "t-idle")}
          <span class="t">${esc(f.title)}</span>${tag(f.area, "t-idle")}</div>
        ${f.detail ? `<div class="d">${esc(f.detail)}</div>` : ""}
        ${f.fix ? `<div class="fx"><b>Fix:</b> ${esc(f.fix)}</div>` : ""}</div>`).join("")
        : emptyState({ ic: "check", title: "Nothing outstanding on this page" })}</div></section>
  </div>`;
}

function contentTab(arts) {
  if (!arts.length) return `<section class="panel"><div class="panel-b">${
    emptyState({ ic: "doc", title: "No content produced yet" })}</div></section>`;
  return `<section class="panel"><div class="panel-h"><div><h2>Generated content</h2>
      <div class="sub">${arts.length} artifacts written by the pipeline</div></div></div>
    <div class="panel-b flush">${arts.map(a => `<button class="row click" data-art="${esc(a.id)}">
      <span class="thumb sq">${icon("doc")}</span>
      <span class="g"><span class="t">${esc(plain(a.stage_id, a.title))}</span>
        <span class="s">${esc(a.kind)} · ${ago(a.created_at)}</span></span>
      ${tag(a.kind, "t-idle")}</button>`).join("")}</div></section>`;
}

function previewTab(htmls, p) {
  if (!htmls.length) return "";
  const a = htmls[0];
  const src = `/api/agency/artifact/raw?id=${encodeURIComponent(a.id)}`;
  return `<section class="panel"><div class="panel-h">
      <div><h2>Built page</h2><div class="sub">${esc(a.title)} · ${ago(a.created_at)}</div></div>
      <span class="r"><a class="btn sm" id="openPreview" href="${src}" target="_blank" rel="noopener">${icon("ext")} Open preview</a></span></div>
    <div class="panel-b"><div class="preview-tools"><button class="btn pri" data-device="desktop" aria-pressed="true">Desktop</button><button class="btn" data-device="mobile" aria-pressed="false">Mobile</button><select id="previewVersion" aria-label="Preview version">${htmls.map((art,i)=>`<option value="${esc(art.id)}">${i===0?'Latest · ':''}${esc(art.title)} · ${esc(art.created_at?.slice(0,16)||'')}</option>`).join('')}</select><button class="btn" data-go="approvals">Review approvals</button></div><p class="draft-note">Saved build preview. Publication and live-site checks are separate.</p><div class="pvw"><div class="pvw-bar">
      <span class="pvw-dots"><i></i><i></i><i></i></span>
      <span class="pvw-url">${esc(p.url || a.title)}</span></div>
      <div class="device-frame"><iframe src="${src}" title="Built page" sandbox=""></iframe></div></div></div></section>`;
}

function activityTab(p) {
  const acts = ((store.overview && store.overview.activity) || [])
    .filter(a => a.entity_id === p.id).slice(0, 30);
  if (!acts.length) return `<section class="panel"><div class="panel-b">${
    emptyState({ ic: "clock", title: "No activity recorded" })}</div></section>`;
  return `<section class="panel"><div class="panel-h"><div><h2>Activity log</h2>
      <div class="sub">This project's history, newest first</div></div></div>
    <div class="panel-b flush">${acts.map(a => `<div class="row">
      <span class="g"><span class="t">${esc(titleCase(a.action))} ${esc(a.entity_type)}</span>
      <span class="s">${esc(a.actor)} · ${ago(a.ts)}</span></span></div>`).join("")}</div></section>`;
}

/* ---------------- artifact viewer ---------------- */
export async function openArtifact(artId) {
  drawer({ title: "Loading…", sub: "", body: `<div class="dsec">${skeleton(5)}</div>` });
  try {
    const a = (await apiGet(`/api/agency/artifact?id=${encodeURIComponent(artId)}`)).artifact;
    const raw = `/api/agency/artifact/raw?id=${encodeURIComponent(artId)}`;
    let body;
    if (a.kind === "json") {
      let pretty = a.content;
      try { pretty = JSON.stringify(JSON.parse(a.content), null, 2); } catch (e) {}
      body = `<pre style="font:12px/1.6 var(--mono);color:var(--tx-2);white-space:pre-wrap;
        word-break:break-word;padding:0 22px">${esc(pretty)}</pre>`;
    } else if (a.kind === "html") {
      body = `<div style="padding:0 22px"><iframe src="${raw}" sandbox="allow-same-origin"
        style="width:100%;height:70vh;border:1px solid var(--line);border-radius:9px;background:#fff"></iframe></div>`;
    } else {
      body = `<div style="padding:0 22px;font:400 13.5px/1.7 var(--sans);color:var(--tx-2);
        white-space:pre-wrap;word-break:break-word">${esc(a.content)}</div>`;
    }
    drawer({
      title: esc(plain(a.stage_id, a.title)),
      sub: `${esc(a.kind)} · ${ago(a.created_at)}${a.file_path ? " · " + esc(a.file_path) : ""}`,
      body,
      footer: `<a class="btn" href="${raw}" target="_blank" rel="noopener">${icon("ext")} Open raw</a>`,
    });
  } catch (e) {
    drawer({ title: "Could not load artifact", sub: "", body: `<div class="dsec">${errBox(e)}</div>` });
  }
}
