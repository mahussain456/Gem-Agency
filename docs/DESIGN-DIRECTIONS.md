# Agency OS — Four Design Directions
**Date:** 2026-07-27 · Coded previews live in `design-concepts/` · All preview data is sample data.

---

## Concept 1 — Executive Command Center
`design-concepts/concept-1-executive-command-center.html`

- **Philosophy:** The agency as a trading desk. Everything that moves money, deadlines, or rankings is on one screen, ranked by "needs a decision" first. Decorative metrics banned by design — every card ends in a verdict or a button.
- **Target experience:** You open it once in the morning, make 5 decisions in 10 minutes, close it.
- **Layout:** 3-column: fixed nav rail → decision/health/SEO stack → live activity + alerts rail.
- **Navigation:** Grouped sidebar (Command / Operations / Divisions / Business) with count badges; ⌘K palette as the universal jump.
- **Hierarchy:** KPI strip → "Needs your decision" → project health → SEO movement. Agents and deadlines secondary column.
- **Typography:** Segoe/Inter for UI, monospace for numbers, timestamps, and data-provenance chips.
- **Color:** Near-black blue-greys, single gold accent, strict semantic red/amber/green/blue. Data-source chips (STRIPE, GSC, AHREFS) on every metric.
- **Components:** Dense rows, small pills, inline actions. No modals for review — approvals open as full pages.
- **Data viz:** Sparse — health bars and deltas, not charts. A number with a direction beats a graph here.
- **Agent presentation:** Fleet status card + live feed rail; deep agent work lives one click away.
- **SEO presentation:** Movement feed (what changed, verified/imported flags) on the dashboard; full workspace as a division section.
- **Desktop:** Excellent — built for a big monitor. **Mobile:** Collapses to single column, rail hidden; usable but secondary.
- **Advantages:** Maximum situational awareness; fastest decision loop; feels premium/serious; scales to many clients.
- **Tradeoffs:** Dense = steeper first-day learning curve; needs real data wired up or it looks embarrassing; less "calm."
- **Best for:** A solo operator or small team running 8+ clients who lives in this tool daily.

## Concept 2 — Minimal Workspace (Apple-inspired)
`design-concepts/concept-2-minimal-workspace.html`

- **Philosophy:** The dashboard is a butler, not a cockpit. It computes priority for you, surfaces ONE most-important thing, a short queue, and hides everything running fine behind "quietly running."
- **Target experience:** Zero anxiety. You always know the single next action; the system proves it's handling the rest.
- **Layout:** Single centered column (980px), sticky translucent top nav, generous whitespace.
- **Navigation:** 6 flat top-level tabs + ⌘K. Progressive disclosure everywhere — depth on click, never on load.
- **Hierarchy:** Greeting + count → Focus card → Your queue → Clients → Quietly running.
- **Typography:** Large friendly headings (-.02em tracking), SF/system stack, sentence-case labels, almost no uppercase.
- **Color:** White/light-grey, one blue accent, soft semantic pastel badges. Light theme first (dark variant later).
- **Components:** Big rounded cards, chevron rows like iOS Settings, pill badges, few borders, soft shadows.
- **Data viz:** Minimal — numbers in sentences ("18 keywords on page 1") over charts. Charts only inside reports.
- **Agent presentation:** Deliberately backgrounded — agents appear as outcomes ("crawl finished"), not as machinery.
- **SEO presentation:** Same philosophy: plain-language results with provenance notes, drill-in for tables.
- **Desktop:** Calm and focused. **Mobile:** Best of all four — the single column is already a phone layout.
- **Advantages:** Zero learning curve; genuinely reduces overwhelm; forces the system to prioritize (which is the hard, valuable part); best mobile.
- **Tradeoffs:** Low information density — power users will click more; hides the agent machinery you may want to watch; the "compute the one thing" ranking logic must be good or trust dies.
- **Best for:** Founder who wants the agency to feel self-driving and checks in from anywhere.

## Concept 3 — Agent Operations Center
`design-concepts/concept-3-agent-operations-center.html`

- **Philosophy:** The agents ARE the agency. The primary object is the pipeline run — a visible DAG of stages, gates, and parallel work — with the fleet on the left and the truth (live log + approval queue) on the right.
- **Target experience:** Air-traffic control. You watch work flow through stages, and the UI makes human gates (approvals, external sends, deploys) physically obvious.
- **Layout:** 3-pane: agent roster → pipeline canvas (stage DAG) → live run log + approval queue.
- **Navigation:** Top tab bar (Missions / Pipelines / Agents / Workflow Builder / Clients / SEO / Reports); breadcrumbs client → project → run.
- **Hierarchy:** The running pipeline is the hero; everything else orbits it.
- **Typography:** UI sans + heavy monospace for logs, run IDs, costs, timestamps. Terminal-adjacent without neon clichés.
- **Color:** Deep navy-charcoal; cyan = activity, violet = planning, amber = human gate, red = failure, green = done. Status glows only on live states.
- **Components:** Stage nodes with per-agent attribution and inline gate buttons; escalation rules readable in plain English on the pipeline itself.
- **Data viz:** The DAG is the visualization. Progress bars in nodes, cost meters per agent.
- **Agent presentation:** Best-in-class — states, costs, logs, gates, dependencies all first-class. The visual workflow builder is native to this concept, not bolted on.
- **SEO presentation:** SEO campaigns are just pipelines (crawl → cluster → briefs → outreach gates), so the model unifies both divisions.
- **Desktop:** Excellent on wide screens. **Mobile:** Weakest — a DAG doesn't fold well; mobile gets approvals + log only.
- **Advantages:** Matches how the system actually works; approval gates are structurally visible (compliance-friendly for outreach rules); debugging agent failures is trivial; most differentiated.
- **Tradeoffs:** Client/revenue view is secondary; highest build complexity (DAG rendering, live log infra); intimidating to non-operators.
- **Best for:** Operator who runs heavy automation and wants total transparency and control over agents.

## Concept 4 — Modular Agency Workspace
`design-concepts/concept-4-modular-workspace.html`

- **Philosophy:** Notion-meets-Grafana. The unit is the widget; the product is the board. You compose dashboards per role, per client, per workflow — "My Command Deck," "SEO Ops," a client board you can screen-share.
- **Target experience:** It's YOUR dashboard. Rearrange, resize, theme, duplicate a board per client in seconds.
- **Layout:** 12-column responsive grid of drag/resize widgets; board tabs across the top.
- **Navigation:** Boards as tabs + widget library + ⌘K. Almost no fixed chrome.
- **Hierarchy:** User-defined — the default board ships opinionated (approvals + health top-left) but nothing is locked.
- **Typography:** Neutral geometric sans, bold tabular numerals for stat widgets.
- **Color:** Light + dark themes (toggle is live in the preview), indigo accent, soft tag colors; widgets stay chrome-light so many coexist.
- **Components:** Widget = header (title + data-source chip + grip) + body. 24-widget library planned (rank tracker, backlink CRM, agent log, revenue…).
- **Data viz:** Per-widget mini-viz: sparklines, bars, stat deltas. Bigger charts = bigger widget.
- **Agent presentation:** Fleet/log/approval widgets — as prominent as you choose to make them.
- **SEO presentation:** Strongest multi-client story: one "SEO Ops" board for you, one white-label board per client for reporting/screen-shares.
- **Desktop:** Great. **Mobile:** Good — widgets stack by priority order.
- **Advantages:** Fits every role and future feature (new feature = new widget); client-facing boards fall out for free; grows with the agency.
- **Tradeoffs:** Blank-canvas problem (needs strong defaults); drag/resize/persist layout engine is real engineering; risk of feeling generic without a strong opinionated start; global consistency harder to keep.
- **Best for:** An agency that will grow past one operator — different people, different decks.

---

## Decision matrix (1–5)

| Criterion | C1 Executive | C2 Minimal | C3 Agent Ops | C4 Modular |
|---|---|---|---|---|
| Usability | 4 | **5** | 3 | 4 |
| Scalability (clients/features) | 4 | 3 | 4 | **5** |
| Visual quality | **5** | **5** | 4 | 4 |
| Information density | **5** | 2 | 4 | 4 |
| Learning curve (5 = easiest) | 3 | **5** | 2 | 4 |
| Customization | 2 | 1 | 3 | **5** |
| Dev complexity (5 = cheapest) | **4** | **5** | 2 | 2 |
| Mobile experience | 3 | **5** | 2 | 4 |
| Agent management | 3 | 2 | **5** | 4 |
| SEO management | 4 | 3 | 4 | **5** |
| **Total /50** | **37** | **36** | **31** | **41** |

## Recommendation

**Build Concept 1's opinionated command center on Concept 4's widget architecture, and steal Concept 3's pipeline view as the project-detail page.**

Straight scoring says C4, but a blank modular canvas on day one is a trap — you'd spend week one designing your own dashboard instead of running the agency. So: implement the layout engine as widgets under the hood (C4), ship C1's decision-first executive deck as the locked default board, and make C3's stage-DAG the view you get when you open any project. C2's "one most important thing" focus card earns a slot at the top of the default board — it's the single best idea of the four.

If you'd rather pick one pure direction: **Concept 1** — it matches how you actually operate (daily solo decision loop, 8+ clients, revenue-aware) and is the best value-per-build-hour.

**⛔ GATE: No rebuild starts until you pick a direction (1, 2, 3, 4, or the recommended hybrid).**
