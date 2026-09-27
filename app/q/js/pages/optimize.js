// ============================================================
// OPTIMIZATION ENGINE — one product covering SEO, AEO and GEO.
//
// Every number on this page is computed from audit artifacts written by
// audit.py, which fetches and parses the real served HTML. The finding
// areas (technical / on-page / aeo / geo / schema / performance /
// accessibility) are the auditor's own taxonomy, not a UI invention.
// No score is shown without the findings that produced it.
// ============================================================
import {
  store, apiGet, esc, icon, ago, ring, prog, tag, emptyState, errBox, skeleton,
  bindGo, shapeProject, loadAudits, areaHealth, allFindings, AREAS, drawer, toast,
} from "/app/q/js/core.js";

const SEV = { high: ["High", "t-crit"], medium: ["Medium", "t-warn"], low: ["Low", "t-idle"] };
const IMPACT = { high: "High", medium: "Medium", low: "Low" };
/* Effort is a property of the fix type, taken from the auditor's area, not guessed per page. */
const EFFORT = { schema: "Low", "on-page": "Low", aeo: "Medium", geo: "Medium",
                 technical: "Medium", performance: "High", accessibility: "Medium" };

let filterArea = "all", filterSev = "all";

export default async function optimize(el) {
  const ov = store.overview;
  const cards = ((ov && ov.projects) || []).map(shapeProject);
  const withUrl = cards.filter(c => c.p.url);

  el.innerHTML = `<div class="page">
    <div class="phead">
      <div><h1>Optimization engine</h1>
        <p>SEO, AEO and GEO in one place. Every finding below came from fetching the real page.</p></div>
      <div class="acts"><button class="btn" data-go="builder">${icon("build")} Run a new audit</button></div>
    </div>
    <div id="oBody">${skeleton(6)}</div></div>`;

  const body = document.getElementById("oBody");
  if (!withUrl.length) {
    body.innerHTML = `<section class="panel"><div class="panel-b">${emptyState({ ic: "seo",
      title: "No project has a URL to audit",
      body: "Add a website URL to a project, then run an SEO campaign. The audit stage fetches the live page and grades it.",
      cta: `<button class="btn pri" data-go="projects">${icon("proj")} Open projects</button>` })}</div></section>`;
    bindGo(el);
    return;
  }

  try { await loadAudits(withUrl.map(c => c.p.id)); }
  catch (e) { body.innerHTML = errBox(e); return; }

  const audited = withUrl.filter(c => store.audits.get(c.p.id));
  if (!audited.length) {
    body.innerHTML = `<section class="panel"><div class="panel-b">${emptyState({ ic: "seo",
      title: "No audit has been run yet",
      body: `${withUrl.length} project${withUrl.length === 1 ? " has a URL" : "s have URLs"} but none has completed an audit stage. Start an SEO campaign to produce one.`,
      cta: `<button class="btn pri" data-go="builder">${icon("build")} Start an audit</button>` })}</div></section>`;
    bindGo(el);
    return;
  }

  render(body, audited);
  bindGo(el);
}

function render(body, audited) {
  const audits = audited.map(c => store.audits.get(c.p.id)).filter(Boolean);
  const H = areaHealth(audits);
  const findings = allFindings(store.audits, audited.map(c => c.p));
  const counts = { high: 0, medium: 0, low: 0 };
  findings.forEach(f => { counts[f.severity] = (counts[f.severity] || 0) + 1; });

  body.innerHTML = `
    <div class="kpis">
      ${["technical", "aeo", "geo"].map(id => {
        const a = AREAS.find(x => x.id === id), b = H[id];
        return `<div class="kpi" style="--kpi-ink:var(--cyan);cursor:default">
          <div class="k">${icon("seo")}${esc(a.name)}</div>
          <div style="display:flex;align-items:center;gap:14px;margin-top:9px">
            ${ring(b.score, "", 58)}
            <div><div class="s" style="margin:0;color:var(--tx-2)">${b.total ? `${b.good} checks passing` : "not measured"}</div>
              <div class="s" style="margin-top:3px">${b.total ? `${b.issues} to fix` : "run an audit"}</div></div>
          </div></div>`;
      }).join("")}
      <div class="kpi" style="--kpi-ink:var(--crit);cursor:default">
        <div class="k">${icon("alert")}Open findings</div>
        <div class="v">${findings.length}</div>
        <div class="c">${counts.high} high · ${counts.medium} medium · ${counts.low} low</div>
      </div>
      <div class="kpi" style="--kpi-ink:var(--ok);cursor:default">
        <div class="k">${icon("shield")}Sites audited</div>
        <div class="v">${audits.length}</div>
        <div class="c"><span class="prov">${icon("check")} verified page fetch</span></div>
      </div>
    </div>

    <div class="grid">
      <section class="panel s4">
        <div class="panel-h"><div><h2>Health by area</h2>
          <div class="sub">Share of checks passing in each area</div></div></div>
        <div class="panel-b">
          ${AREAS.map(a => {
            const b = H[a.id];
            return `<button class="chip" data-area="${a.id}" style="width:100%;justify-content:flex-start;
              height:auto;padding:9px 11px;margin-bottom:7px;text-align:left">
              <span style="flex:1;min-width:0">
                <span style="display:block;font:500 13px/1.3 var(--sans);color:var(--tx)">${esc(a.name)}</span>
                <span class="s" style="margin-top:4px;display:block">${
                  b.total ? `${b.good} passing · ${b.issues} issue${b.issues === 1 ? "" : "s"}` : "no checks recorded"}</span>
              </span>
              <span style="width:56px;flex-shrink:0;display:flex;align-items:center;gap:7px">
                ${prog(b.score == null ? 0 : b.score, b.score == null ? "" : b.score >= 80 ? "ok" : b.score >= 55 ? "warn" : "crit")}
              </span>
              <span class="mono" style="width:30px;text-align:right;font-size:12px;color:var(--tx-2)">${b.score == null ? "—" : b.score}</span>
            </button>`;
          }).join("")}
        </div>
      </section>

      <section class="panel s8">
        <div class="panel-h">
          <div><h2>What to fix</h2><div class="sub" id="fCount">${findings.length} findings, highest impact first</div></div>
          <span class="r">
            <span class="chips">
              <button class="chip on" data-sev="all">All</button>
              <button class="chip" data-sev="high">High</button>
              <button class="chip" data-sev="medium">Medium</button>
              <button class="chip" data-sev="low">Low</button>
            </span></span>
        </div>
        <div class="panel-b flush" id="fList"></div>
      </section>
    </div>

    <div class="grid"><section class="panel s12">
      <div class="panel-h"><div><h2>Audited pages</h2>
        <div class="sub">What the auditor actually fetched</div></div></div>
      <div class="tblwrap" style="border:0;background:none">
        <table class="tbl"><thead><tr>
          <th>Project</th><th>URL</th><th class="num">Score</th><th class="num">Load</th>
          <th class="num">Words</th><th>Schema</th><th>robots</th><th>sitemap</th><th>llms.txt</th><th>Audited</th>
        </tr></thead><tbody>
          ${audited.map(c => {
            const a = store.audits.get(c.p.id);
            if (!a) return "";
            /* Tri-state, deliberately. A "self_audit" grades a page the agents
               just built and never fetches robots/sitemap/llms.txt, so those
               must read "not checked" — printing "no" would assert a failure
               the auditor never tested for. */
            const ok = v => !a.site ? `<span style="color:var(--tx-3)" title="not checked by this audit">—</span>`
              : v ? `<span style="color:var(--ok)">yes</span>`
              : `<span style="color:var(--crit)">no</span>`;
            return `<tr class="click" data-go="project/${esc(c.p.id)}">
              <td>${esc(c.p.name)}</td>
              <td class="mono" style="font-size:12px;color:var(--tx-2)">${esc(String(a.final_url || a.url || "").replace(/^https?:\/\//, ""))}</td>
              <td class="num"><b style="color:${a.score >= 80 ? "var(--ok)" : a.score >= 55 ? "var(--warn)" : "var(--crit)"}">${a.score}</b></td>
              <td class="num">${a.load_ms != null ? a.load_ms + "ms" : "—"}</td>
              <td class="num">${a.page && a.page.text_chars != null ? Math.round(a.page.text_chars / 5) : "—"}</td>
              <td>${a.page && a.page.schema_types && a.page.schema_types.length
                    ? esc(a.page.schema_types.join(", ")) : `<span style="color:var(--crit)">none</span>`}</td>
              <td>${ok(a.site && a.site.robots && a.site.robots.ok)}</td>
              <td>${ok(a.site && a.site.sitemap && a.site.sitemap.ok)}</td>
              <td>${ok(a.site && a.site.llms && a.site.llms.ok)}</td>
              <td class="s" style="margin:0">${ago(a._at)}</td>
            </tr>`;
          }).join("")}
        </tbody></table></div>
    </section></div>`;

  const list = document.getElementById("fList");
  const count = document.getElementById("fCount");

  function paint() {
    const shown = findings.filter(f =>
      (filterArea === "all" || f.area === filterArea) &&
      (filterSev === "all" || f.severity === filterSev));
    count.textContent = `${shown.length} of ${findings.length} findings`
      + (filterArea === "all" ? "" : ` · ${filterArea}`);
    list.innerHTML = shown.length ? shown.slice(0, 60).map((f, i) => {
      const [sl, sc] = SEV[f.severity] || SEV.low;
      return `<div class="find" data-f="${i}">
        <div class="hd">
          ${tag(sl, sc)}
          <span class="t">${esc(f.title)}</span>
          ${tag(f.area, "t-idle")}
        </div>
        ${f.detail ? `<div class="d">${esc(f.detail)}</div>` : ""}
        ${f.fix ? `<div class="fx"><b>Fix:</b> ${esc(f.fix)}</div>` : ""}
        <div style="display:flex;gap:8px;align-items:center;margin-top:9px;flex-wrap:wrap">
          <span class="s" style="margin:0">Impact ${IMPACT[f.severity]} · Effort ${EFFORT[f.area] || "Medium"}</span>
          <span style="flex:1"></span>
          <button class="btn sm" data-go="project/${esc(f.project.id)}">${esc(f.project.name)} ${icon("arrowR")}</button>
          <button class="btn sm" data-fix="${i}">${icon("ai")} Ask an agent to fix this</button>
        </div></div>`;
    }).join("") : emptyState({ ic: "check", title: "Nothing matches that filter",
      body: "Clear the filters to see the rest of the findings." });

    list.querySelectorAll("[data-fix]").forEach(b => b.addEventListener("click", () => {
      const f = shown[+b.dataset.fix];
      const prompt = `On ${f.project.name} (${f.url || f.project.url}): fix the ${f.severity} ${f.area} issue `
        + `"${f.title}". ${f.fix || f.detail || ""}`.trim();
      location.hash = "ai/" + encodeURIComponent(prompt);
    }));
    bindGo(list);
  }

  body.querySelectorAll("[data-area]").forEach(b => b.addEventListener("click", () => {
    filterArea = filterArea === b.dataset.area ? "all" : b.dataset.area;
    body.querySelectorAll("[data-area]").forEach(x => x.classList.toggle("on", x.dataset.area === filterArea));
    paint();
  }));
  body.querySelectorAll("[data-sev]").forEach(b => b.addEventListener("click", () => {
    filterSev = b.dataset.sev;
    body.querySelectorAll("[data-sev]").forEach(x => x.classList.toggle("on", x.dataset.sev === filterSev));
    paint();
  }));
  paint();
  bindGo(body);
}
