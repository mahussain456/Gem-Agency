// ============================================================
// APPROVAL CENTRE.
// The backend accepts exactly two decisions (approved / rejected), so
// those are the only two buttons that write. "Send back to the agent"
// is a real action too — it opens AI Command with the item's context —
// but it is labelled for what it does rather than implying a third state.
// ============================================================
import {
  store, apiGet, apiPost, esc, icon, ago, titleCase, tag, emptyState, errBox,
  skeleton, bindGo, drawer, toast, refresh, approvalStats,
} from "/app/q/js/core.js";

const KIND_CLS = { copy: "t-blue", deploy: "t-ok", outreach: "t-warn", proposal: "t-cyan", other: "t-idle" };
let tab = "pending";

export default async function approvals(el) {
  el.innerHTML = `<div class="page">
    <div class="phead"><div><h1>Approvals</h1>
      <p>Work the agents have finished that needs a human decision before it goes further.</p></div>
      <div class="acts"><button class="btn" id="addBtn">${icon("plus")} Add an item</button></div></div>
    <div class="tabs" id="apTabs"></div>
    <div id="apBody">${skeleton(5)}</div></div>`;

  let all = [];
  try { all = (await apiGet("/api/agency/approvals")).approvals || []; }
  catch (e) { document.getElementById("apBody").innerHTML = errBox(e); return; }

  const groups = {
    pending: all.filter(a => a.status === "pending"),
    approved: all.filter(a => a.status === "approved"),
    rejected: all.filter(a => a.status === "rejected"),
    history: all,
  };
  const LABEL = { pending: "Needs approval", approved: "Approved", rejected: "Rejected", history: "History" };
  document.getElementById("apTabs").innerHTML = Object.keys(LABEL).map(k =>
    `<button class="${k === tab ? "on" : ""}" data-tab="${k}">${LABEL[k]}
      <span class="ct">${groups[k].length}</span></button>`).join("");

  function paint() {
    const list = groups[tab];
    const body = document.getElementById("apBody");
    if (!list.length) {
      body.innerHTML = `<section class="panel"><div class="panel-b">${
        tab === "pending"
          ? `<div class="allclear" style="padding:26px 18px"><span class="ic">${icon("check")}</span>
             <div><b>You're all caught up.</b>
             <span>Nothing is waiting on your decision. Agents keep working and will call you when that changes.</span></div></div>`
          : emptyState({ ic: "inbox", title: `Nothing ${tab} yet` })}</div></section>`;
      return;
    }
    body.innerHTML = `<div class="grid">${list.map(a => card(a)).join("")}</div>`;
    wire(body);
  }

  function card(a) {
    let payload = null;
    try { payload = a.payload ? JSON.parse(a.payload) : null; } catch (e) { payload = null; }
    const preview = payload && (payload.draft || payload.body || payload.content || payload.summary);
    return `<section class="panel s6">
      <div class="panel-h">
        <div style="min-width:0"><h2 style="white-space:normal">${esc(a.title)}</h2>
          <div class="sub">${a.project_name ? esc(a.project_name) + " · " : ""}${
            a.source_agent ? "raised by " + esc(a.source_agent) + " · " : ""}${ago(a.created_at)}</div></div>
        <span class="r">${a.blocking ? tag("Blocking", "t-crit", true) : ""}
          ${tag(titleCase(a.kind), KIND_CLS[a.kind] || "t-idle")}</span>
      </div>
      <div class="panel-b">
        ${a.detail ? `<div style="font:400 13px/1.55 var(--sans);color:var(--tx-2)">${esc(String(a.detail).slice(0, 420))}${
          String(a.detail).length > 420 ? "…" : ""}</div>` : `<div class="s">No detail recorded.</div>`}
        ${preview ? `<div style="margin-top:11px;padding:11px 13px;border:1px solid var(--line);border-radius:9px;
          background:var(--bg);font:400 12.5px/1.6 var(--sans);color:var(--tx-2);
          max-height:150px;overflow:auto;white-space:pre-wrap">${esc(String(preview).slice(0, 900))}</div>` : ""}
        ${a.status !== "pending" ? `<div class="s" style="margin-top:11px">
          ${titleCase(a.status)} by ${esc(a.decided_by || "operator")} ${ago(a.decided_at)}</div>` : ""}
      </div>
      ${a.status === "pending" && a.kind === "design" ? `<div class="panel-f" style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn pri" data-go="project/${esc(a.project_id)}">${icon("eye")} See both designs and pick one</button>
        <button class="btn dang" data-decide="rejected" data-id="${esc(a.id)}">${icon("x")} Stop this build</button>
      </div>` : a.status === "pending" ? `<div class="panel-f" style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ok" data-decide="approved" data-id="${esc(a.id)}">${icon("check")} Approve</button>
        <button class="btn dang" data-decide="rejected" data-id="${esc(a.id)}">${icon("x")} Reject</button>
        <button class="btn" data-back="${esc(a.id)}">${icon("ai")} Send back to the agent</button>
        <span style="flex:1"></span>
        ${a.project_id ? `<button class="btn sm" data-go="project/${esc(a.project_id)}">Open project ${icon("arrowR")}</button>` : ""}
      </div>` : ""}
    </section>`;
  }

  function wire(root) {
    root.querySelectorAll("[data-decide]").forEach(b => b.addEventListener("click", async () => {
      root.querySelectorAll("[data-id='" + b.dataset.id + "']").forEach(x => x.disabled = true);
      try {
        await apiPost(`/api/agency/approvals/decide?id=${encodeURIComponent(b.dataset.id)}`,
          { decision: b.dataset.decide });
        toast(`Recorded: ${b.dataset.decide}.`);
        await refresh();
        approvalsReload(el);
      } catch (e) {
        root.querySelectorAll("[data-id='" + b.dataset.id + "']").forEach(x => x.disabled = false);
        toast(String(e.message || e), true);
      }
    }));
    root.querySelectorAll("[data-back]").forEach(b => b.addEventListener("click", () => {
      const a = all.find(x => x.id === b.dataset.back);
      const p = `Revise this before I approve it — "${a.title}". ${a.detail || ""}`.trim();
      location.hash = "ai/" + encodeURIComponent(p)
        + (a.source_agent ? "?to=@" + String(a.source_agent).replace("@", "") : "");
    }));
    bindGo(root);
  }

  el.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => {
    tab = b.dataset.tab;
    el.querySelectorAll("[data-tab]").forEach(x => x.classList.toggle("on", x.dataset.tab === tab));
    paint();
  }));
  document.getElementById("addBtn").addEventListener("click", () => addApproval(el));
  paint();
  bindGo(el);
}

function approvalsReload(el) { approvals(el); }

function addApproval(el) {
  import("/app/q/js/core.js").then(({ modal, apiPost, toast }) => {
    const projects = (store.overview && store.overview.projects) || [];
    modal({
      title: "Add an approval item",
      note: "Use this to put a human decision into the queue that no agent raised.",
      fields: [
        { name: "title", label: "What needs deciding?", required: true },
        { name: "detail", label: "Detail", type: "textarea" },
        { name: "kind", label: "Kind", type: "select", value: "other", options:
          ["copy", "deploy", "outreach", "proposal", "other"].map(k => ({ value: k, label: titleCase(k) })) },
        { name: "project_id", label: "Project", type: "select", options:
          [{ value: "", label: "— none —" }].concat(projects.map(p => ({ value: p.id, label: p.name }))) },
      ],
      submitLabel: "Add to queue",
      onSubmit: async v => { await apiPost("/api/agency/approvals", v); toast("Added."); approvals(el); },
    });
  });
}
