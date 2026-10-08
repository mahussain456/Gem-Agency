// ============================================================
// ROUTE TABLE. Hash routing: #key or #key/id.
// ============================================================
import { icon, bindGo } from "/app/q/js/core.js";
import deck from "/app/q/js/pages/deck.js";
import growth from "/app/q/js/pages/growth.js";
import builder from "/app/q/js/pages/builder.js";
import optimize from "/app/q/js/pages/optimize.js";
import agentsPage from "/app/q/js/pages/agents.js";
import monitor, { monitorTeardown } from "/app/q/js/pages/monitor.js";
import computer, { computerTeardown } from "/app/q/js/pages/computer.js";
import media, { mediaTeardown } from "/app/q/js/pages/media.js";
import office, { officeTeardown } from "/app/q/js/pages/office.js";
import openseo, { openseoTeardown } from "/app/q/js/pages/openseo.js";
import approvals from "/app/q/js/pages/approvals.js";
import ai from "/app/q/js/pages/ai.js";
import projects, { projectPage } from "/app/q/js/pages/projects.js";
import { backlinks, clients, integrations, models,
} from "/app/q/js/pages/misc.js";

function missing(el) {
  el.innerHTML = `<div class="page"><div class="phead"><div>
      <h1>Not found</h1><p>That page does not exist in this build.</p></div></div>
    <section class="panel"><div class="panel-b"><div class="empty">
      <div class="ei">${icon("search")}</div>
      <b>Nothing here</b>
      <div>Use the command palette (<kbd>Ctrl K</kbd>) to jump anywhere.</div>
      <div class="cta"><button class="btn pri" data-go="">${icon("deck")} Back to the Overview</button></div>
    </div></div></section></div>`;
  bindGo(el);
}

export { monitorTeardown, computerTeardown, mediaTeardown, openseoTeardown, officeTeardown };

export const ROUTES = {
  "": deck,
  growth,
  monitor,
  ai,
  approvals,
  projects,
  project: projectPage,
  builder,
  optimize,
  keywords: () => location.replace("#seo"),   // keyword tracking moved to OpenSEO; old links land there
  backlinks,
  agency: agentsPage,
  agents: agentsPage,
  clients,
  client: clients,
  models,
  computer,
  media,
  office,
  seo: openseo,
  integrations,
  _missing: missing,
};
