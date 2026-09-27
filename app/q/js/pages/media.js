// ============================================================
// IMAGE & VIDEO — generation studio, integrated from Open Generative AI
// (github.com/Anil-matcha/Open-Generative-AI, MIT). Same model catalogue
// and the same Muapi.ai protocol, inside the dashboard: the key stays on
// the server, results are kept on disk next to the website they are for.
// Generation runs on muapi.ai and bills the operator's Muapi account.
// ============================================================
import { apiGet, apiPost, esc, icon, tag, ago, toast, store, emptyState, errBox, skeleton } from "/app/q/js/core.js";

const MODES = [
  { k: "text-to-image", t: "Image", s: "From a prompt" },
  { k: "image-to-image", t: "Edit image", s: "From a photo or image" },
  { k: "text-to-video", t: "Video", s: "From a prompt" },
  { k: "image-to-video", t: "Animate image", s: "From a start frame" },
];
// inputs the studio sets itself, or that need tooling it does not have yet
const HANDLED = new Set(["prompt", "negative_prompt"]);
const SKIP = new Set(["loras", "lora_list", "lora_url", "lora_weight", "model_url", "character_ids", "audio_ids",
                      "request_id", "make_input", "descriptionKey"]);
const MEDIA = new Set(["image_url", "images_list", "image_urls", "last_image", "last_image_url", "first_image",
                       "end_image_url", "first_frame_url", "last_frame_url", "image", "reference_images",
                       "video_url", "videos_list", "video_files", "reference_videos", "reference_video_urls",
                       "audio_url", "audios_list", "audio_files", "reference_audios"]);

let catalog = null, poll = 0;
const ui = { mode: "text-to-image", model: {}, refs: {} };

export default async function mediaPage(el) {
  clearInterval(poll);
  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Image &amp; Video</h1>
      <p>Hero images, product shots, social clips and ads for client sites and campaigns, from 400+ models: Flux, Seedream, Nano Banana, Kling, Veo, Wan, Seedance and more.
      Integrated from <a href="https://github.com/Anil-matcha/Open-Generative-AI" target="_blank" rel="noopener" style="color:var(--ac)">Open Generative AI</a>; generation runs on muapi.ai and bills your Muapi account.</p></div></div>
    <div id="mdSlot">${skeleton(3)}</div></div>`;
  const slot = document.getElementById("mdSlot");
  let st;
  try { st = await apiGet("/api/agency/media/status"); }
  catch (e) { slot.innerHTML = errBox(e); return; }
  if (st.error) { slot.innerHTML = errBox(new Error(st.error)); return; }
  if (!st.connected) return drawConnect(slot, st);
  if (!catalog) {
    try { catalog = await apiGet("/api/agency/media/catalog"); }
    catch (e) { slot.innerHTML = errBox(e); return; }
  }
  drawStudio(slot, st);
}

export function mediaTeardown() { clearInterval(poll); }

/* ---------------- connect ---------------- */
function drawConnect(slot, st) {
  slot.innerHTML = `<div class="grid"><section class="panel s6"><div class="panel-h"><div><h2>Connect Muapi</h2>
      <div class="sub">The engine behind Open Generative AI's ${st.catalog.count} models</div></div>
      <span class="r">${tag("Not connected", "t-idle")}</span></div>
    <div class="panel-b"><form id="mkForm" style="display:grid;gap:8px" autocomplete="off">
      <input name="api_key" type="password" placeholder="Muapi API key" spellcheck="false"
        style="padding:9px 11px;border:1px solid var(--line-2);border-radius:9px;background:var(--bg);color:var(--tx)">
      <div class="errbox" data-err hidden style="margin:0"></div>
      <div class="s">Get a key at muapi.ai (Dashboard → API keys). It is stored on this computer in a gitignored file and never sent to the browser again.</div>
      <button class="btn pri sm" type="submit">Connect</button></form></div></section>
    <section class="panel s6"><div class="panel-h"><div><h2>What you get</h2></div></div>
      <div class="panel-b s" style="line-height:1.8">
        ${MODES.map(m => `<div><b style="color:var(--tx)">${esc(m.t)}</b> · ${esc(m.s)} · ${st.catalog.counts?.[m.k] ?? 0} models</div>`).join("")}
        <div style="margin-top:10px">Results are saved in the dashboard (they outlive Muapi's links) and can be tagged to a website.
        Alt text and file names for search are written by Claude or ChatGPT.</div></div></section></div>`;
  const f = slot.querySelector("#mkForm");
  f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const err = f.querySelector("[data-err]"), v = f.elements.api_key.value.trim();
    if (!v) { err.textContent = "Paste your Muapi API key first."; err.hidden = false; return; }
    try { await apiPost("/api/agency/media/key", { api_key: v }); toast("Muapi connected."); mediaPage(document.getElementById("work")); }
    catch (e) { err.textContent = String(e.message || e); err.hidden = false; }
  });
}

/* ---------------- studio ---------------- */
function modelsFor(kind) { return catalog.models.filter(m => m.kind === kind); }

/* Many image-based models name their start image in a model-level imageField
   rather than in inputs (upstream reads it the same way); media.py mirrors this. */
function inputsOf(m) {
  const inputs = { ...(m.inputs || {}) };
  if (m.kind.startsWith("image-")) {
    const field = m.imageField || (Object.keys(inputs).some(k => MEDIA.has(k)) ? null : "image_url");
    if (field && !inputs[field]) {
      const many = /_list$|s$/.test(field);
      inputs[field] = { name: field, type: many ? "array" : "string", title: many ? "Images" : "Image",
                        maxItems: m.maxImages || (many ? 4 : 1), description: "The picture to start from" };
    }
  }
  return inputs;
}
const isMedia = (k, m) => MEDIA.has(k) || k === m.imageField;

function drawStudio(slot, st) {
  let brief = "";
  try {
    brief = sessionStorage.getItem("gem-media-brief") || "";
    const mode = sessionStorage.getItem("gem-media-mode");
    if (mode && MODES.some(m => m.k === mode)) ui.mode = mode;
    sessionStorage.removeItem("gem-media-brief"); sessionStorage.removeItem("gem-media-mode");
  } catch { /* none */ }
  const projects = ((store.overview && store.overview.projects) || []).filter(p => p.status !== "archived");
  slot.innerHTML = `<div class="md-layout">
    <section class="panel md-create"><div class="panel-h" style="flex-wrap:wrap;gap:10px">
        <div class="seg-ctl md-modes" role="tablist" aria-label="What to make">${MODES.map(m =>
          `<button type="button" role="tab" data-mode="${m.k}" aria-selected="${m.k === ui.mode}" class="${m.k === ui.mode ? "on" : ""}">${esc(m.t)}</button>`).join("")}</div>
        <span class="r"><button class="linkbtn" id="mdDisconnect" type="button">Disconnect Muapi</button></span></div>
      <form class="panel-b" id="mdForm" autocomplete="off">
        <div class="fld"><label for="mdSearch">Model</label>
          <div style="display:flex;gap:8px"><input id="mdSearch" placeholder="Filter models…" style="flex:0 0 38%;min-width:0">
          <select id="mdModel" style="flex:1;min-width:0"></select></div>
          <div class="hint" id="mdModelNote"></div></div>
        <div class="fld" id="mdPromptFld"><label for="mdPrompt">Prompt</label>
          <textarea id="mdPrompt" rows="4" placeholder="What should it show? Subject, setting, light, style…">${esc(brief)}</textarea>
          <div style="display:flex;gap:8px;margin-top:7px;align-items:center">
            <button class="btn sm" type="button" id="mdImprove">${icon("ai")} Improve with AI</button>
            <span class="s" id="mdImproveNote"></span></div></div>
        <div id="mdMedia"></div>
        <div class="md-controls" id="mdControls"></div>
        <div class="fld"><label for="mdProject">For website (optional)</label>
          <select id="mdProject"><option value="">Not tied to a website</option>
            ${projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></div>
        <div class="errbox" data-err hidden style="margin:4px 0 10px"></div>
        <button class="btn pri" type="submit" id="mdGo">${icon("play")} Generate</button>
        <span class="s" style="margin-left:10px">Runs on muapi.ai · billed to your Muapi account</span>
      </form></section>
    <section class="panel md-results"><div class="panel-h"><div><h2>Results</h2><div class="sub">Kept on this computer</div></div>
        <span class="r"><select id="mdFilter" aria-label="Filter results"><option value="">All</option>${MODES.map(m => `<option value="${m.k}">${esc(m.t)}</option>`).join("")}</select></span></div>
      <div class="panel-b"><div class="md-grid" id="mdGrid">${skeleton(2)}</div></div></section>
  </div>`;

  const $ = s => slot.querySelector(s);
  slot.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => {
    ui.mode = b.dataset.mode;
    slot.querySelectorAll("[data-mode]").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", String(x === b)); });
    fillModels(); drawModel();
  }));
  $("#mdDisconnect").addEventListener("click", async () => {
    await apiPost("/api/agency/media/disconnect", {}); catalog = null; mediaPage(document.getElementById("work"));
  });
  $("#mdSearch").addEventListener("input", () => fillModels($("#mdSearch").value));
  $("#mdModel").addEventListener("change", () => { ui.model[ui.mode] = $("#mdModel").value; drawModel(); });
  $("#mdImprove").addEventListener("click", async () => {
    const note = $("#mdImproveNote"), p = $("#mdPrompt");
    if (p.value.trim().length < 3) { note.textContent = "Write a few words first."; return; }
    $("#mdImprove").disabled = true; note.textContent = "Thinking…";
    try {
      const r = await apiPost("/api/agency/media/improve", { prompt: p.value, kind: ui.mode });
      p.value = r.prompt; note.textContent = `Rewritten by ${r.provider}${r.model ? " · " + r.model : ""}`;
    } catch (e) { note.textContent = String(e.message || e); }
    $("#mdImprove").disabled = false;
  });
  $("#mdFilter").addEventListener("change", drawResults);
  $("#mdForm").addEventListener("submit", submit);

  function fillModels(q = "") {
    const ql = q.trim().toLowerCase();
    const list = modelsFor(ui.mode).filter(m => !ql || `${m.name} ${m.provider_name || ""} ${m.id}`.toLowerCase().includes(ql));
    const groups = {};
    list.forEach(m => (groups[m.provider_name || "Other"] ||= []).push(m));
    const current = ui.model[ui.mode] && list.some(m => m.id === ui.model[ui.mode]) ? ui.model[ui.mode] : (list[0] || {}).id;
    $("#mdModel").innerHTML = Object.keys(groups).sort().map(g => `<optgroup label="${esc(g)}">${
      groups[g].map(m => `<option value="${esc(m.id)}" ${m.id === current ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</optgroup>`).join("")
      || `<option value="">No model matches</option>`;
    ui.model[ui.mode] = current || "";
    $("#mdModelNote").textContent = `${list.length} of ${modelsFor(ui.mode).length} ${MODES.find(m => m.k === ui.mode).t.toLowerCase()} models`;
  }

  function currentModel() { return catalog.models.find(m => m.id === ui.model[ui.mode]); }

  function drawModel() {
    const m = currentModel();
    const box = $("#mdControls"), mediaBox = $("#mdMedia");
    ui.refs = {};
    if (!m) { box.innerHTML = ""; mediaBox.innerHTML = ""; return; }
    $("#mdPromptFld").hidden = !m.inputs.prompt;
    const all = inputsOf(m);
    const mediaInputs = Object.entries(all).filter(([k]) => isMedia(k, m));
    mediaBox.innerHTML = mediaInputs.map(([k, spec]) => `<div class="fld"><label>${esc(spec.title || k)}${
        spec.maxItems > 1 ? ` <span class="s">(up to ${spec.maxItems})</span>` : ""}</label>
      <div class="md-drop" data-media="${esc(k)}">
        <input type="file" accept="${/audio/.test(k) ? "audio/*" : /video/.test(k) ? "video/*" : "image/*"}" ${spec.maxItems > 1 ? "multiple" : ""} aria-label="${esc(spec.title || k)}">
        <div class="md-drop-t">${icon("plus")} Choose or drop a file</div><div class="md-thumbs"></div></div></div>`).join("");
    mediaBox.querySelectorAll("[data-media]").forEach(wireDrop);
    const controls = Object.entries(all).filter(([k, v]) => !HANDLED.has(k) && !isMedia(k, m) && !SKIP.has(k) && v && v.type);
    box.innerHTML = controls.map(([k, spec]) => control(k, spec)).join("");
  }

  function control(k, spec) {
    const id = `mdc_${k}`, label = esc(spec.title || k), def = spec.default;
    const hint = spec.description ? `<div class="hint">${esc(spec.description.slice(0, 120))}</div>` : "";
    if (Array.isArray(spec.enum) && spec.enum.length) {
      return `<div class="fld"><label for="${id}">${label}</label><select id="${id}" data-k="${esc(k)}" data-t="${esc(spec.type)}">
        ${spec.enum.map(o => `<option value="${esc(o)}" ${String(o) === String(def) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></div>`;
    }
    if (/^(bool|boolean)$/.test(spec.type)) {
      return `<label class="cz-row md-bool"><span>${label}</span><input type="checkbox" id="${id}" data-k="${esc(k)}" data-t="boolean" ${def ? "checked" : ""}></label>`;
    }
    if (/^(int|integer|number|float)$/.test(spec.type)) {
      return `<div class="fld"><label for="${id}">${label}</label><input id="${id}" type="number" data-k="${esc(k)}" data-t="${esc(spec.type)}"
        ${spec.minValue != null ? `min="${spec.minValue}"` : ""} ${spec.maxValue != null ? `max="${spec.maxValue}"` : ""}
        step="${spec.step || (/int/.test(spec.type) ? 1 : "any")}" value="${def != null ? esc(def) : ""}" placeholder="${k === "seed" ? "random" : ""}">${hint}</div>`;
    }
    return `<div class="fld"><label for="${id}">${label}</label><input id="${id}" data-k="${esc(k)}" data-t="string" value="${def != null ? esc(def) : ""}">${hint}</div>`;
  }

  function wireDrop(drop) {
    const k = drop.dataset.media, input = drop.querySelector("input[type=file]"), thumbs = drop.querySelector(".md-thumbs");
    const add = async files => {
      for (const file of files) {
        if (file.size > 12 * 1024 * 1024) { toast(`${file.name} is over 12 MB.`, true); continue; }
        const chip = document.createElement("span");
        chip.className = "md-thumb loading";
        chip.textContent = "Uploading…";
        thumbs.appendChild(chip);
        try {
          const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
          const { url } = await apiPost("/api/agency/media/upload", { filename: file.name, data });
          (ui.refs[k] ||= []).push(url);
          chip.className = "md-thumb";
          chip.innerHTML = file.type.startsWith("image/") ? `<img src="${esc(data)}" alt="">` : esc(file.name);
        } catch (e) { chip.remove(); toast(String(e.message || e), true); }
      }
    };
    input.addEventListener("change", () => add([...input.files]));
    drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); add([...e.dataTransfer.files]); });
  }

  async function submit(ev) {
    ev.preventDefault();
    const m = currentModel(), err = $("[data-err]");
    const say = t => { err.textContent = t; err.hidden = false; };
    if (!m) return say("Choose a model.");
    const params = {};
    if (m.inputs.prompt) {
      const p = $("#mdPrompt").value.trim();
      if (!p && ui.mode.startsWith("text-")) return say("Write a prompt first.");
      if (p) params.prompt = p;
    }
    const all = inputsOf(m);
    for (const [k, urls] of Object.entries(ui.refs)) {
      if (!urls.length || !all[k]) continue;
      params[k] = all[k].type === "array" ? urls.slice(0, all[k].maxItems || urls.length) : urls[0];
    }
    if (ui.mode.startsWith("image-") && !Object.keys(ui.refs).some(k => ui.refs[k].length)) return say("Upload a starting image first.");
    slot.querySelectorAll("[data-k]").forEach(inp => {
      const k = inp.dataset.k, t = inp.dataset.t;
      if (t === "boolean") params[k] = inp.checked;
      else if (inp.value !== "") params[k] = /int|number|float/.test(t) ? Number(inp.value) : inp.value;
    });
    err.hidden = true;
    const btn = $("#mdGo");
    btn.disabled = true;
    try {
      await apiPost("/api/agency/media/generate", { model: m.id, params, project_id: $("#mdProject").value });
      toast(`${m.name} is working on it.`);
      await drawResults();
    } catch (e) { say(String(e.message || e)); }
    btn.disabled = false;
  }

  async function drawResults() {
    const grid = $("#mdGrid");
    if (!grid) return;
    let rows;
    try { rows = (await apiGet("/api/agency/media/jobs?limit=60")).jobs || []; }
    catch (e) { grid.innerHTML = errBox(e); return; }
    const f = $("#mdFilter").value;
    if (f) rows = rows.filter(r => r.kind === f);
    grid.innerHTML = rows.length ? rows.map(card).join("")
      : emptyState({ ic: "image", title: "Nothing generated yet", body: "Pick a model, describe the shot and press Generate. Results land here and are kept on this computer." });
    grid.querySelectorAll("[data-seo]").forEach(b => b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "Writing…";
      try { await apiPost("/api/agency/media/seo", { id: b.dataset.seo }); await drawResults(); }
      catch (e) { toast(String(e.message || e), true); b.disabled = false; b.textContent = "Alt text & file name"; }
    }));
    grid.querySelectorAll("[data-reuse]").forEach(b => b.addEventListener("click", () => {
      const job = rows.find(r => r.id === b.dataset.reuse);
      if (!job) return;
      $("#mdPrompt").value = job.prompt || "";
      $("#mdPrompt").focus();
      toast("Prompt loaded. Adjust it and generate again.");
    }));
    grid.querySelectorAll("[data-cancel]").forEach(b => b.addEventListener("click", async () => {
      try { await apiPost("/api/agency/media/cancel", { id: b.dataset.cancel }); } catch (e) { toast(String(e.message || e), true); }
    }));
    const live = rows.some(r => r.status === "queued" || r.status === "running");
    clearInterval(poll);
    if (live) poll = setInterval(() => { if (document.getElementById("mdGrid")) drawResults(); else clearInterval(poll); }, 3000);
  }

  fillModels();
  drawModel();
  drawResults();
}

function fileUrl(path) { return `/api/agency/media/file?path=${encodeURIComponent(path)}`; }

function card(j) {
  const busy = j.status === "queued" || j.status === "running";
  const file = (j.files || [])[0];
  const isVideo = file && /\.(mp4|webm|mov)$/i.test(file);
  const proj = ((store.overview && store.overview.projects) || []).find(p => p.id === j.project_id);
  const preview = busy ? `<div class="md-busy"><span class="jv-dots"><i></i><i></i><i></i></span><span>${esc(j.progress || "Queued")}…</span></div>`
    : file ? (isVideo ? `<video src="${fileUrl(file)}" controls preload="metadata" playsinline></video>`
                      : `<img src="${fileUrl(file)}" alt="${esc(j.seo?.alt || j.prompt || "")}" loading="lazy">`)
    : `<div class="md-busy bad">${icon("alert")}<span>${esc(j.error || "No file")}</span></div>`;
  return `<article class="md-card ${busy ? "busy" : ""}">
    <div class="md-preview">${preview}</div>
    <div class="md-meta">
      <div class="md-top"><b>${esc(j.model_name)}</b>${tag(busy ? "Working" : j.status === "done" ? "Done" : "Failed",
        busy ? "t-blue" : j.status === "done" ? "t-ok" : "t-crit")}</div>
      ${j.prompt ? `<p title="${esc(j.prompt)}">${esc(j.prompt.slice(0, 140))}${j.prompt.length > 140 ? "…" : ""}</p>` : ""}
      <div class="s">${esc(j.provider || "")}${proj ? " · " + esc(proj.name) : ""} · ${ago(j.created_at)}</div>
      ${j.seo ? `<div class="md-seo"><div><b>Alt</b> ${esc(j.seo.alt)}</div><div><b>File</b> <span class="mono">${esc(j.seo.filename)}</span></div>
          <div class="s">by ${esc(j.seo.by)}</div></div>` : ""}
      <div class="md-acts">
        ${busy ? `<button class="btn sm" data-cancel="${esc(j.id)}">${icon("x")} Stop waiting</button>` : ""}
        ${file ? `<a class="btn sm" href="${fileUrl(file)}" download="${esc((j.seo?.filename || j.model) + file.slice(file.lastIndexOf(".")))}">${icon("ext")} Download</a>` : ""}
        ${j.status === "done" && !j.seo ? `<button class="btn sm" data-seo="${esc(j.id)}">Alt text &amp; file name</button>` : ""}
        ${j.prompt ? `<button class="btn sm" data-reuse="${esc(j.id)}">Reuse prompt</button>` : ""}
      </div></div></article>`;
}
