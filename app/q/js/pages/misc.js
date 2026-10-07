// ============================================================
// Remaining routes: Keywords, Backlinks, Clients, Integrations.
// Every one is wired to an endpoint that already exists.
// ============================================================
import {
  store, apiGet, apiPost, esc, icon, ago, money, titleCase, tag, emptyState,
  errBox, skeleton, bindGo, toast, refresh, modal, shapeProject,
} from "/app/q/js/core.js";

const head = (h1, p, acts = "") => `<div class="phead"><div><h1>${h1}</h1><p>${p}</p></div>
  <div class="acts">${acts}</div></div>`;

/* ---------------- LINK OUTREACH ---------------- */
export async function backlinks(el) {
  el.innerHTML = `<div class="page">${head("Link outreach",
    "Sites your SEO campaigns found worth earning a link from, and where each one stands. To analyse the links a site already has, use OpenSEO.",
    `<button class="btn" data-go="seo">${icon("seo")} Backlink analysis in OpenSEO</button>`)}<div id="bBody">${skeleton(4)}</div></div>`;
  const body = document.getElementById("bBody");
  let list = [];
  try { list = (await apiGet("/api/agency/backlinks")).prospects || []; }
  catch (e) { body.innerHTML = errBox(e); return; }

  if (!list.length) {
    body.innerHTML = `<section class="panel"><div class="panel-b">${emptyState({ ic: "link",
      title: "No prospects yet",
      body: "The backlink stage of an SEO campaign finds and verifies these for real." })}</div></section>`;
    bindGo(el); return;
  }
  // "identified" is what the campaign's backlink stage writes; it was missing here, hiding those prospects
  const STAGES = ["new", "identified", "qualified", "approved", "contacted", "won", "rejected"];
  const CLS = { new: "t-idle", identified: "t-idle", qualified: "t-blue", approved: "t-cyan", contacted: "t-warn", won: "t-ok", rejected: "t-crit" };
  const cols = STAGES.map(s => ({ s, items: list.filter(p => (p.status || "new") === s) }))
    .concat([{ s: "other", items: list.filter(p => !STAGES.includes(p.status || "new")) }])   // never hide a prospect
    .filter(c => c.items.length);

  body.innerHTML = `<div class="grid">${cols.map(c => `<section class="panel s3">
    <div class="panel-h"><div><h2>${titleCase(c.s)}</h2>
      <div class="sub">${c.items.length} prospect${c.items.length === 1 ? "" : "s"}</div></div></div>
    <div class="panel-b flush">${c.items.map(p => `<div class="row">
      <span class="g"><span class="t">${esc(p.domain)}</span>
        <span class="s">${esc(p.kind || "link")}${p.contact ? " · " + esc(p.contact) : ""}</span></span>
      ${tag(titleCase(p.status || "new"), CLS[p.status] || "t-idle")}</div>`).join("")}</div></section>`).join("")}</div>`;
  bindGo(el);
}

/* ---------------- CLIENTS ---------------- */
export async function clients(el) {
  const ov = store.overview;
  const list = (ov && ov.clients) || [];
  const projects = (ov && ov.projects) || [];
  el.innerHTML = `<div class="page">${head("Clients",
    `${list.length} client${list.length === 1 ? "" : "s"} · ${money(ov ? ov.mrr_cents : 0)} recurring each month`,
    `<button class="btn pri" id="addCl">${icon("plus")} Add client</button>`)}
    ${list.length ? `<div class="grid">${list.map(c => {
      const ps = projects.filter(p => p.client_id === c.id);
      return `<section class="panel s4">
        <div class="panel-h"><div style="min-width:0"><h2>${esc(c.name)}</h2>
          <div class="sub">${esc(c.company || (c.services || []).join(", ") || "no services set")}</div></div>
          <span class="r">${tag(titleCase(c.status), c.status === "active" ? "t-ok" : "t-idle")}</span></div>
        <div class="panel-b">
          <div style="display:flex;gap:20px;margin-bottom:13px">
            <div><div class="s" style="margin:0">Monthly</div>
              <div style="font:600 21px/1.2 var(--sans);margin-top:3px">${money(c.mrr_cents)}</div></div>
            <div><div class="s" style="margin:0">Projects</div>
              <div style="font:600 21px/1.2 var(--sans);margin-top:3px">${ps.length}</div></div>
          </div>
          ${ps.length ? ps.slice(0, 4).map(p => {
            // Step count comes from the playbook, matching every other view.
            const c = shapeProject(p);
            return `<button class="row click" style="padding:8px 0;border-top:1px solid var(--line)"
            data-go="project/${esc(p.id)}"><span class="g"><span class="t">${esc(p.name)}</span>
            <span class="s">${c.done}/${c.total} stages · ${esc(c.status.k)}</span></span>${icon("arrowR")}</button>`;
          }).join("")
            : `<div class="s">No projects yet.</div>`}
        </div>
        <div class="panel-f" style="display:flex;gap:8px">
          <a class="btn sm" href="/api/agency/report.html?client_id=${encodeURIComponent(c.id)}"
             target="_blank" rel="noopener">${icon("doc")} Client report</a>
          <button class="btn sm" data-edit="${esc(c.id)}">${icon("gear")} Edit</button></div>
      </section>`;
    }).join("")}</div>`
    : `<section class="panel"><div class="panel-b">${emptyState({ ic: "users",
        title: "No clients yet", body: "Add one to attach projects and record recurring revenue." })}</div></section>`}
  </div>`;

  const form = (c = {}) => modal({
    title: c.id ? "Edit client" : "Add client",
    fields: [
      { name: "name", label: "Name", required: true, value: c.name || "" },
      { name: "company", label: "Company", value: c.company || "" },
      { name: "services", label: "Services", value: (c.services || []).join(", "), hint: "comma separated" },
      { name: "mrr_cents", label: "Monthly retainer (cents)", value: c.mrr_cents || "",
        hint: "2500 dollars = 250000 cents" },
      { name: "status", label: "Status", type: "select", value: c.status || "active",
        options: ["active", "paused", "former"].map(v => ({ value: v, label: titleCase(v) })) },
    ],
    submitLabel: c.id ? "Save" : "Add client",
    onSubmit: async v => {
      v.services = v.services ? v.services.split(",").map(s => s.trim()).filter(Boolean) : [];
      v.mrr_cents = parseInt(v.mrr_cents || "0", 10) || 0;
      if (c.id) await apiPost(`/api/agency/clients/update?id=${encodeURIComponent(c.id)}`, v);
      else await apiPost("/api/agency/clients", v);
      await refresh(); toast("Saved."); clients(el);
    },
  });
  document.getElementById("addCl").addEventListener("click", () => form());
  el.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () =>
    form(list.find(c => c.id === b.dataset.edit) || {})));
  bindGo(el);
}

/* ---------------- INTEGRATIONS ---------------- */
export async function integrations(el) {
  const ov = store.overview;
  const ints = (ov && ov.integrations) || {};
  const META = {
    hermes_gateway: { n: "Hermes Gateway", d: "Agent execution on localhost:8643", ic: "bot" },
    gsc: { n: "Google Search Console", d: "Impressions, clicks and real positions", ic: "chart" },
    ga4: { n: "Google Analytics 4", d: "Traffic and conversions", ic: "trend" },
    dataforseo: { n: "DataForSEO", d: "Search volume, difficulty, live rankings, backlinks", ic: "chart" },
    openseo: { n: "OpenSEO", d: "Keywords, rank tracking, backlinks, site audits, AI visibility (open source, runs on this PC)", ic: "seo" },
    stripe: { n: "Stripe", d: "Billing and verified MRR", ic: "money" },
    brain: { n: "Brain", d: "Claude, then ChatGPT, then Ollama", ic: "ai" },
    claude: { n: "Claude", d: "Anthropic API", ic: "ai" },
    chatgpt: { n: "ChatGPT", d: "OpenAI API", ic: "ai" },
    ollama: { n: "Ollama", d: "Local models on this computer", ic: "bot" },
    jev: { n: "Typed decisions", d: "Laya on this computer (free); Jev cloud optional", ic: "bot" },
  };
  const CLS = { connected: ["Connected", "t-ok"], running: ["Connected", "t-ok"], configured: ["Connected", "t-ok"],
                error: ["Needs attention", "t-crit"], not_connected: ["Not connected", "t-idle"] };
  const broken = Object.entries(ints).filter(([, v]) => v && v.state === "error");

  el.innerHTML = `<div class="page">
    ${head("Integrations", "Where this workspace gets its data. Anything not connected leaves its metrics blank rather than estimated.")}
    ${broken.length ? `<div class="errbox" style="margin-bottom:var(--gap)">
      <b>${broken.length} connection${broken.length === 1 ? " needs" : "s need"} attention</b>
      Metrics that depend on ${broken.map(([k]) => (META[k] || {}).n || k).join(", ")} are showing as blank.</div>` : ""}
    <div class="grid">${Object.entries(ints).map(([k, v]) => {
      const m = META[k] || { n: k, d: "", ic: "plug" };
      const [label, cls] = CLS[v.state] || ["Unknown", "t-idle"];
      return `<section class="panel s4">
        <div class="panel-h"><div style="min-width:0"><h2>${esc(m.n)}</h2>
          <div class="sub">${esc(m.d)}</div></div><span class="r">${tag(label, cls)}</span></div>
        <div class="panel-b">
          ${v.error ? `<div class="errbox" style="margin:0 0 11px"><b>Last error</b>
            <span class="mono" style="font-size:11.5px">${esc(String(v.error).slice(0, 260))}</span></div>` : ""}
          ${v.last_sync_at ? `<div class="s">Last sync ${ago(v.last_sync_at)}</div>` : ""}
          ${k === "dataforseo" ? `
            ${v.state === "configured"
              ? `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
                   <span class="s" id="dfsBal">checking balance…</span>
                   <button class="btn sm" id="dfsCheck">${icon("refresh")} Re-check</button>
                   <button class="btn sm" id="dfsOff">Disconnect</button></div>`
              : `<form id="dfsForm" style="display:grid;gap:7px">
                   <div class="s">Pay-as-you-go. Used by the dashboard and by OpenSEO, so you enter it once. Stored locally, never committed.</div>
                   <input id="dfsLogin" placeholder="DataForSEO login (email)" required
                     style="padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:13.5px inherit">
                   <input id="dfsPass" type="password" placeholder="API password" required
                     style="padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:13.5px inherit">
                   <button class="btn pri sm" type="submit">${icon("plug")} Connect</button>
                 </form>`}`
          : k === "openseo" ? `<div class="s" style="margin-bottom:9px">${v.state === "running"
                ? (v.dataforseo ? "Running on this computer with your DataForSEO key." : "Running on this computer. Add the DataForSEO key here to fill it with data.")
                : v.state === "configured" ? "Installed. It starts when you open it." : "Not installed yet."}</div>
              <button class="btn ${v.state === "not_connected" ? "pri " : ""}sm" data-go="seo">${icon("seo")} ${v.state === "not_connected" ? "Set up OpenSEO" : "Open OpenSEO"}</button>`
          : k === "gsc" ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn pri sm" id="gscConnect">${icon("plug")} ${v.state === "error" ? "Reconnect" : "Connect"}</button>
            <button class="btn sm" id="gscCheck">${icon("refresh")} Re-check</button></div>`
          : v.state === "not_connected" ? `<div class="s">Add credentials in Settings to enable this source.</div>`
          : `<div class="s">No action needed.</div>`}
        </div></section>`;
    }).join("")}</div></div>`;

  const dfsForm = document.getElementById("dfsForm");
  if (dfsForm) dfsForm.addEventListener("submit", async ev => {
    ev.preventDefault();
    const btn = dfsForm.querySelector("button");
    btn.disabled = true;
    try {
      const r = await apiPost("/api/agency/dataforseo/setup", {
        login: document.getElementById("dfsLogin").value.trim(),
        password: document.getElementById("dfsPass").value,
      });
      toast(r.ok === false ? String(r.error) : `Connected. Balance $${r.balance ?? "?"}.`);
      await refresh();
      integrations(el);
    } catch (e) { toast(String(e.message || e), true); btn.disabled = false; }
  });
  const dfsBal = document.getElementById("dfsBal");
  if (dfsBal) apiGet("/api/agency/dataforseo/status")
    .then(s => { dfsBal.textContent = s.ok ? `Balance $${s.balance ?? "?"}` : String(s.error || "unavailable"); })
    .catch(e => { dfsBal.textContent = String(e.message || e); });
  const dfsCheck = document.getElementById("dfsCheck");
  if (dfsCheck) dfsCheck.addEventListener("click", () => integrations(el));
  const dfsOff = document.getElementById("dfsOff");
  if (dfsOff) dfsOff.addEventListener("click", async () => {
    try { await apiPost("/api/agency/dataforseo/disconnect"); await refresh(); integrations(el); toast("Disconnected."); }
    catch (e) { toast(String(e.message || e), true); }
  });

  const gc = document.getElementById("gscConnect");
  if (gc) gc.addEventListener("click", async () => {
    gc.disabled = true;
    try {
      const r = await apiGet("/api/agency/gsc/auth-url");
      window.open(r.url, "_blank", "noopener");
      toast("Approve access in the Google tab, then press Re-check.");
    } catch (e) { toast(String(e.message || e), true); }
    gc.disabled = false;
  });
  const gk = document.getElementById("gscCheck");
  if (gk) gk.addEventListener("click", async () => {
    gk.disabled = true;
    try { await apiGet("/api/agency/gsc/status"); await refresh(); integrations(el); toast("Re-checked."); }
    catch (e) { gk.disabled = false; toast(String(e.message || e), true); }
  });
  bindGo(el);
}

/* ---------------- MODELS: Claude, ChatGPT, Jev ----------------
   Text providers plus Jev for typed decisions. A key is stored locally and
   never committed; the model list comes from the account, so no model id is
   ever guessed on the operator's behalf. */
const MODEL_META = {
  claude: { n: "Claude (Anthropic)", d: "Long-form writing, code and reasoning" },
  chatgpt: { n: "ChatGPT (OpenAI)", d: "General generation, a second opinion and failover" },
};

export async function models(el) {
  el.innerHTML = `<div class="page">${head("Models",
    "Claude and ChatGPT are the agency's brain, with a local Ollama model as the fallback. Every website build, SEO stage, Jarvis answer and agent chat walks the chain below; if one engine fails, the next takes over instead of the work stopping.")}
    <div id="mdl">${skeleton(3)}</div></div>`;
  await drawModels(document.getElementById("mdl"));
}

async function drawModels(slot) {
  let st;
  try { st = await apiGet("/api/agency/models/status"); }
  catch (e) { slot.innerHTML = errBox(e); return; }
  const p = st.providers || {};
  const signin = st.signin || {};

  const card = (key, prov) => {
    const m = MODEL_META[key] || { n: prov.label || key, d: "" };
    const on = prov.connected;
    const needsModel = !on && !!prov.key_source && /no model selected/i.test(prov.error || "");
    const hint = key === "claude"
      ? `Defaults to <b>claude-opus-5</b>; you can change it after connecting.`
      : `You will pick the model from the list your account returns.`;
    return `<section class="panel s6"><div class="panel-h">
        <div style="min-width:0"><h2>${esc(m.n)}</h2><div class="sub">${esc(m.d)}</div></div>
        <span class="r">${tag(on ? "Connected" : "Not connected", on ? "t-ok" : "t-idle")}</span></div>
      <div class="panel-b">
        ${prov.error && !needsModel ? `<div class="errbox" style="margin:0 0 10px"><b>Not usable yet</b>${esc(prov.error)}</div>` : ""}
        ${needsModel ? `
          <div class="s" style="margin-bottom:10px">Key saved${prov.key_source === "environment" ? " (from the environment)" : ""}. One step left: choose which model to use.</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn pri sm" data-pick="${key}">Choose a model</button>
            <button class="btn sm" data-off="${key}">Remove key</button>
          </div>`
        : on ? `
          <div class="s" style="margin-bottom:9px">Model <b>${esc(prov.model || "—")}</b>${
            prov.key_source === "environment" ? " · key from environment" : ""}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn sm" data-test="${key}">Send a test call</button>
            <button class="btn sm" data-pick="${key}">Change model</button>
            <button class="btn sm" data-off="${key}">Disconnect</button>
          </div>
          <div class="s" data-out="${key}" style="margin-top:9px"></div>`
        : `
          <form data-setup="${key}" style="display:grid;gap:7px">
            <input name="api_key" type="password" placeholder="${key === "claude" ? "Anthropic" : "OpenAI"} API key"
              autocomplete="off" spellcheck="false"
              style="padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);font:13.5px inherit">
            <div class="errbox" data-err hidden style="margin:0"></div>
            <div class="s">Stored locally in a gitignored file. ${hint}</div>
            <button class="btn pri sm" type="submit">Connect</button>
          </form>
          ${key === "claude" ? `<div class="s" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)">
            <b>Or sign in instead of pasting a key.</b> Run <code>ant auth login</code> in a terminal;
            the SDK reads the profile it writes, so nothing is stored here.
            ${signin.claude && signin.claude.cli_installed
              ? `The CLI is installed — sign in, then press Re-check.`
              : `The <code>ant</code> CLI is not on PATH yet; install it first.`}
            <div style="margin-top:7px"><button class="btn sm" data-recheck>Re-check</button></div></div>`
          : `<div class="s" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)">
            OpenAI has no sign-in for third-party apps — a ChatGPT subscription is a separate
            product from the API, so this one needs a key.</div>`}`}
      </div></section>`;
  };

  const jevOn = (st.jev || {}).connected;
  const cloudOn = (st.jev || {}).cloud;
  const laya = (st.jev || {}).laya || {};
  slot.innerHTML = `<div class="grid">
    ${brainPanel(p)}
    ${card("claude", p.claude || {})}
    ${card("chatgpt", p.chatgpt || {})}
    ${ollamaCard(p.ollama || {})}
    <section class="panel s6"><div class="panel-h">
      <div style="min-width:0"><h2>Typed decisions</h2>
        <div class="sub">Yes/no, pick one and score, each with a calibrated confidence</div></div>
      <span class="r">${tag(jevOn ? "Ready" : "Not set up", jevOn ? "t-ok" : "t-idle")}</span></div>
      <div class="panel-b">
        <div class="s" style="margin-bottom:11px">Used where the pipeline needs a decision rather than prose,
          for example whether minor build defects justify regenerating a page. Laya answers free on this
          computer; Jev (TypeSafe) is an optional cloud engine used only if Laya cannot answer.</div>
        <div class="dec-engine">
          <div class="dec-h">${icon("bot")}<b>Laya</b><span class="cu-free">free</span>
            <span class="r">${tag(laya.running ? "Running" : laya.phase === "installing" ? "Installing" : laya.phase === "starting" ? "Starting"
              : laya.phase === "failed" ? "Failed" : laya.installed ? "Installed" : "Not installed",
              laya.running ? "t-ok" : laya.phase === "failed" ? "t-crit" : laya.installed ? "t-blue" : "t-idle")}</span></div>
          <div class="s">${laya.installed
            ? `Open source, runs on this computer (${esc(laya.device || "CPU")}). Models downloaded: ${esc((laya.models_downloaded || []).join(", ") || "none yet")}.`
            : "Open source (Apache-2.0). Installs about 700 MB of packages and downloads a model of about 800 MB on its first decision."}</div>
          ${laya.error ? `<div class="errbox" style="margin:8px 0 0"><b>Laya</b>${esc(laya.error)}</div>` : ""}
          <div class="dec-a">${!laya.installed
            ? `<button class="btn pri sm" data-laya="install" ${laya.phase === "installing" ? "disabled" : ""}>${icon("plus")} Install Laya</button>`
            : laya.running ? `<button class="btn sm" data-laya="stop">${icon("pause")} Stop</button>`
            : `<button class="btn sm" data-laya="start">${icon("play")} Start</button>`}</div>
        </div>
        <div class="dec-engine">
          <div class="dec-h">${icon("plug")}<b>Jev (TypeSafe)</b><span class="r">${tag(cloudOn ? "Connected" : "Optional", cloudOn ? "t-ok" : "t-idle")}</span></div>
          ${cloudOn ? `<div class="dec-a"><button class="btn sm" data-jevoff>Disconnect</button></div>`
          : `<form id="jevForm" style="display:grid;gap:7px;margin-top:6px">
            <input name="api_key" type="password" placeholder="TypeSafe API key (paid, optional)"
              autocomplete="off" spellcheck="false"
              style="padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);font:13.5px inherit">
            <div class="errbox" data-err hidden style="margin:0"></div>
            <button class="btn sm" type="submit">Connect Jev</button></form>`}
        </div>
        ${jevOn ? `<div style="margin-top:11px"><button class="btn sm" data-jevtest>${icon("bolt")} Send a test decision</button>
          <div class="s" id="jevOut" style="margin-top:8px"></div></div>` : ""}
      </div></section>
  </div>`;
  wireBrain(slot, p);
  wireOllama(slot);

  slot.querySelectorAll("[data-recheck]").forEach(b =>
    b.addEventListener("click", () => drawModels(slot)));

  slot.querySelectorAll("[data-setup]").forEach(f => f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const key = f.dataset.setup;
    const btn = f.querySelector("button");
    const input = f.querySelector("[name=api_key]");
    const err = f.querySelector("[data-err]");
    const value = input.value.trim();
    // `required` used to block the submit natively, and the browser tooltip is
    // easy to miss on this dark panel — the button simply looked dead. Say it
    // in the card instead.
    if (!value) {
      err.textContent = key === "claude"
        ? "Paste an Anthropic API key, or sign in with the CLI below instead."
        : "Paste an OpenAI API key to connect ChatGPT.";
      err.hidden = false;
      input.focus();
      return;
    }
    err.hidden = true;
    btn.disabled = true;
    try {
      await apiPost("/api/agency/models/setup", { provider: key, api_key: value });
      toast(`${MODEL_META[key].n} connected.`);
      if (key === "chatgpt") await pickModel(slot, key);
      else await drawModels(slot);
    } catch (e) {
      err.textContent = String(e.message || e);
      err.hidden = false;
      toast(String(e.message || e), true);
      btn.disabled = false;
    }
  }));

  slot.querySelectorAll("[data-test]").forEach(b => b.addEventListener("click", async () => {
    const key = b.dataset.test;
    const out = slot.querySelector(`[data-out="${key}"]`);
    b.disabled = true; out.textContent = "Calling…";
    try {
      const r = await apiPost("/api/agency/models/test", { provider: key });
      out.innerHTML = `Replied in ${r.seconds}s on <b>${esc(r.model)}</b>: ${esc(r.reply)}`;
    } catch (e) { out.innerHTML = `<span style="color:var(--crit)">${esc(String(e.message || e))}</span>`; }
    b.disabled = false;
  }));

  slot.querySelectorAll("[data-pick]").forEach(b =>
    b.addEventListener("click", () => pickModel(slot, b.dataset.pick)));

  slot.querySelectorAll("[data-off]").forEach(b => b.addEventListener("click", async () => {
    try { await apiPost("/api/agency/models/disconnect", { provider: b.dataset.off }); await drawModels(slot); }
    catch (e) { toast(String(e.message || e), true); }
  }));

  const jf = slot.querySelector("#jevForm");
  if (jf) jf.addEventListener("submit", async ev => {
    ev.preventDefault();
    const btn = jf.querySelector("button");
    const input = jf.querySelector("[name=api_key]");
    const err = jf.querySelector("[data-err]");
    const value = input.value.trim();
    if (!value) {
      err.textContent = "Paste a TypeSafe API key to connect Jev.";
      err.hidden = false;
      input.focus();
      return;
    }
    err.hidden = true;
    btn.disabled = true;
    try {
      const r = await apiPost("/api/agency/jev/setup", { api_key: value });
      toast(r.ok ? "Jev connected." : String(r.error || "Connected, but the test call failed."));
      await drawModels(slot);
    } catch (e) {
      err.textContent = String(e.message || e);
      err.hidden = false;
      toast(String(e.message || e), true);
      btn.disabled = false;
    }
  });

  const jt = slot.querySelector("[data-jevtest]");
  if (jt) jt.addEventListener("click", async () => {
    const out = slot.querySelector("#jevOut");
    jt.disabled = true; out.textContent = "Deciding… the first decision loads the model, which can take a minute.";
    try {
      const r = await apiGet("/api/agency/jev/status");
      out.innerHTML = r.ok ? `${esc(r.sample)} <b>${esc(r.engine_label)}</b> answered in ${r.latency_ms} ms (${esc(r.model)}).`
                           : `<span style="color:var(--crit)">${esc(r.error || "failed")}</span>`;
    } catch (e) { out.innerHTML = `<span style="color:var(--crit)">${esc(String(e.message || e))}</span>`; }
    jt.disabled = false;
  });

  slot.querySelectorAll("[data-laya]").forEach(b => b.addEventListener("click", async () => {
    const act = b.dataset.laya;
    b.disabled = true;
    try {
      await apiPost(`/api/agency/laya/${act}`, {});
      toast(act === "install" ? "Installing Laya. This takes a few minutes; this page updates when it is done."
        : act === "start" ? "Starting Laya." : "Laya stopped.");
      // installs and starts carry on in the background: refresh until they settle
      const until = Date.now() + (act === "install" ? 30 * 60000 : 120000);
      const tick = async () => {
        if (!slot.isConnected) return;
        const st = (await apiGet("/api/agency/laya/status")).laya;
        if (["installing", "starting"].includes(st.phase) && Date.now() < until) return setTimeout(tick, 3000);
        drawModels(slot);
      };
      setTimeout(tick, 1500);
    } catch (e) { toast(String(e.message || e), true); b.disabled = false; }
  }));

  const jo = slot.querySelector("[data-jevoff]");
  if (jo) jo.addEventListener("click", async () => {
    try { await apiPost("/api/agency/jev/disconnect", {}); await drawModels(slot); }
    catch (e) { toast(String(e.message || e), true); }
  });
}

/* ---------------- the brain chain ---------------- */
const SHORT = { claude: "Claude", chatgpt: "ChatGPT", ollama: "Ollama", hermes: "Hermes gateway" };

function brainPanel(p) {
  const b = p._brain || { order: [], live: [], active: "" };
  const order = b.order || [], live = new Set(b.live || []), fails = new Set(b.failing || []);
  const missing = ["claude", "chatgpt", "ollama", "hermes"].filter(k => !order.includes(k));
  const first = order[0];
  const state = !b.active ? ["Offline", "t-crit"] : b.active === first ? ["Thinking", "t-ok"] : ["On fallback", "t-warn"];
  const row = (k, i) => {
    const prov = p[k] || {}, on = live.has(k), active = b.active === k;
    const detail = fails.has(k) ? `${prov.model || ""} · last call failed: ${prov.failing}`
      : on ? (prov.model || (k === "hermes" ? "routed per agent" : "")) : (prov.error || "not connected");
    return `<li class="bc ${on ? "on" : "off"} ${active ? "active" : ""}" style="--i:${i}">
      <span class="bc-n">${i + 1}</span>
      <div class="bc-g"><b>${esc(SHORT[k] || k)}</b><span>${esc(detail)}</span></div>
      <span class="bc-st ${fails.has(k) ? "bad" : ""}">${active ? "Answering" : fails.has(k) ? "Failing" : on ? "Standing by" : "Not connected"}</span>
      <div class="bc-acts">
        <button class="iconbtn" data-bmove="${k}" data-dir="-1" ${i === 0 ? "disabled" : ""} aria-label="Move ${esc(SHORT[k])} earlier" title="Earlier">${icon("chevL")}</button>
        <button class="iconbtn" data-bmove="${k}" data-dir="1" ${i === order.length - 1 ? "disabled" : ""} aria-label="Move ${esc(SHORT[k])} later" title="Later">${icon("chevL")}</button>
        <button class="iconbtn" data-bdel="${k}" ${order.length < 2 ? "disabled" : ""} aria-label="Remove ${esc(SHORT[k])} from the chain" title="Remove">${icon("x")}</button>
      </div></li>`;
  };
  return `<section class="panel s12 brain"><div class="panel-h">
      <div style="min-width:0"><h2>The brain</h2>
        <div class="sub">Tried in this order for every thinking task. The first that answers does the work; the result records which model it was.</div></div>
      <span class="r">${tag(state[0], state[1])}</span></div>
    <div class="panel-b">
      <ol class="brain-chain">${order.map(row).join("")}</ol>
      <div class="s brain-note">${icon("shield")}<span>SEO, AEO and GEO work (keyword maps, schema, answer-engine plans, backlinks and questions to Rank) runs only on Claude or ChatGPT, never on the local fallback.</span></div>
      ${missing.length ? `<div class="bc-add"><span class="s">Add to the chain:</span>${missing.map(k =>
        `<button class="btn sm" data-badd="${k}">${icon("plus")} ${esc(SHORT[k])}</button>`).join("")}</div>` : ""}
    </div></section>`;
}

function wireBrain(slot, p) {
  const order = [...((p._brain || {}).order || [])];
  const save = async next => {
    try { await apiPost("/api/agency/models/brain", { order: next }); await drawModels(slot); }
    catch (e) { toast(String(e.message || e), true); }
  };
  slot.querySelectorAll("[data-bmove]").forEach(b => b.addEventListener("click", () => {
    const i = order.indexOf(b.dataset.bmove), j = i + Number(b.dataset.dir);
    if (i < 0 || j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    save(next);
  }));
  slot.querySelectorAll("[data-bdel]").forEach(b => b.addEventListener("click", () =>
    save(order.filter(k => k !== b.dataset.bdel))));
  slot.querySelectorAll("[data-badd]").forEach(b => b.addEventListener("click", () =>
    save([...order, b.dataset.badd])));
}

/* ---------------- local fallback ---------------- */
function ollamaCard(o) {
  const on = o.connected, reach = o.reachable;
  const list = o.models || [];
  return `<section class="panel s6"><div class="panel-h">
      <div style="min-width:0"><h2>Ollama (local)</h2>
        <div class="sub">The fallback that runs on this machine: no key, no bill, no data leaves</div></div>
      <span class="r">${tag(on ? "Ready" : reach ? "Choose a model" : "Not running", on ? "t-ok" : reach ? "t-warn" : "t-idle")}</span></div>
    <div class="panel-b">
      ${!reach ? `<div class="errbox" style="margin:0 0 10px"><b>Not reachable</b>${esc(o.error || "")}</div>
        <div class="s" style="margin-bottom:10px">Start the Ollama app (or run <span class="mono">ollama serve</span>), then press Re-check.</div>` : ""}
      ${reach && list.length ? `<form id="olForm" style="display:grid;gap:8px">
          <label class="s" for="olModel">Model</label>
          <select id="olModel" name="model" style="padding:8px 10px;border:1px solid var(--line-2);border-radius:9px;background:var(--bg);color:var(--tx)">
            ${o.model ? "" : `<option value="">Choose a local model…</option>`}
            ${list.map(m => `<option value="${esc(m.name)}" ${m.name === o.model ? "selected" : ""}>${esc(m.name)}${m.size ? " · " + esc(m.size) : ""}</option>`).join("")}
          </select>
          <div class="errbox" data-err hidden style="margin:0"></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn pri sm" type="submit">${o.model ? "Use this model" : "Set as fallback"}</button>
            ${on ? `<button class="btn sm" type="button" data-test="ollama">Send a test call</button>` : ""}
          </div>
          <div class="s" data-out="ollama"></div>
        </form>` : reach ? `<div class="s">Ollama is running but has no local chat models. Install one, e.g. <span class="mono">ollama pull llama3.1</span>.</div>` : ""}
      ${(o.cloud_hidden || []).length ? `<div class="s" style="margin-top:10px">Hidden: ${esc(o.cloud_hidden.join(", "))}. Ollama cloud models run on ollama.com, not this machine, so they can't be the local fallback.</div>` : ""}
      <details style="margin-top:10px"><summary class="s" style="cursor:pointer">Server: ${esc(o.host || "")}</summary>
        <form id="olHost" style="display:flex;gap:8px;margin-top:8px">
          <input name="host" value="${esc(o.host || "")}" aria-label="Ollama server address" spellcheck="false"
            style="flex:1;min-width:0;padding:7px 10px;border:1px solid var(--line-2);border-radius:9px;background:var(--bg);color:var(--tx)">
          <button class="btn sm" type="submit">Save</button>
          <button class="btn sm" type="button" data-recheck-ol>Re-check</button>
        </form></details>
    </div></section>`;
}

function wireOllama(slot) {
  const f = slot.querySelector("#olForm");
  if (f) f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const err = f.querySelector("[data-err]"), model = f.elements.model.value;
    if (!model) { err.textContent = "Choose a model first."; err.hidden = false; return; }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await apiPost("/api/agency/models/ollama", { model });
      toast(`Local fallback set to ${model}.`);
      await drawModels(slot);
    } catch (e) { err.textContent = String(e.message || e); err.hidden = false; btn.disabled = false; }
  });
  const h = slot.querySelector("#olHost");
  if (h) h.addEventListener("submit", async ev => {
    ev.preventDefault();
    try { await apiPost("/api/agency/models/ollama", { host: h.elements.host.value }); await drawModels(slot); }
    catch (e) { toast(String(e.message || e), true); }
  });
  const rc = slot.querySelector("[data-recheck-ol]");
  if (rc) rc.addEventListener("click", () => drawModels(slot));
}

async function pickModel(slot, provider) {
  let list;
  try { list = (await apiPost("/api/agency/models/list", { provider })).models; }
  catch (e) { toast(String(e.message || e), true); return drawModels(slot); }
  if (!list || !list.length) { toast("That account returned no usable models.", true); return drawModels(slot); }
  // Whatever happens in the dialog, the card behind it reflects the saved state.
  await drawModels(slot);
  const ovl = modal({
    title: `Choose a ${MODEL_META[provider].n} model`,
    note: `${list.length} chat model${list.length === 1 ? "" : "s"} your account can use, newest first. You can change this later.`,
    fields: [{ name: "model", label: "Model", type: "select", value: list[0],
               options: list.map(m => ({ value: m, label: m })) }],
    submitLabel: "Use this model",
    onSubmit: async v => {
      await apiPost("/api/agency/models/select", { provider, model: v.model });
      toast(`${MODEL_META[provider].n} connected on ${v.model}.`);
      await drawModels(slot);
    },
  });
  return ovl;
}
