# Agency OS — Technical Architecture (Phase 4)
**Approved design:** Concept 4 — Modular Agency Workspace · **Date:** 2026-07-28

## Stack decision

**Stay on the existing stack for this milestone — but modularized.** The audit recommended FastAPI + React long-term; this milestone deliberately does not introduce that toolchain, because:

1. The working assets (agent bridge, Hermes telemetry, watchdog/launcher, tests) are all Python stdlib — porting them buys zero user-visible value today.
2. No npm/pip dependency chain = nothing to break on this Windows box, no build step, instant iteration.
3. The modular structure below is a straight lift into FastAPI/React later — module boundaries are drawn where the future framework seams are.

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3 stdlib, `ThreadingHTTPServer` | Existing `server.py` continues to own the HTTP loop; ALL new logic lives in `agency.py` (new module) hooked in with 3-line edits |
| Frontend | Vanilla ES modules served from `app/` | No build step. One JS module per widget — the Concept-4 "widget" is the component boundary |
| Database | **`agency.db`** (new, single SQLite) | WAL mode; `schema_migrations` table; every business table carries `client_id`; every metric value carries `source` + `provenance` (`estimated\|imported\|verified`) |
| Legacy DBs | untouched, read-compat | `board.db`, `missions.db`, `bridge.db`, `workflows.db` keep working; legacy UI preserved at `/legacy` |
| Agents | Existing Hermes gateway (`:8643`) | Bridge send/history APIs reused as-is. New `agent_runs` + `approvals` tables wrap them with states, cost, and gates |
| Auth | Bearer token (`AGENCY_TOKEN` in Hermes `.env`), localhost binding | Mutating `/api/agency/*` endpoints require the token; the UI fetches it via a one-time localhost handshake. CSRF-safe: token header, no cookies |
| Background jobs | `threading` + `jobs` table | Good enough single-user; queue table is the future Celery/queue seam |
| Files | existing `uploads/` machinery | reused |
| Search | SQLite FTS5 (phase 2) | virtual table over clients/projects/tasks/notes |
| Logging/audit | `activity_log` table | every mutation writes actor, action, entity, before/after |
| Error tracking | structured server log + `/api/agency/health` | external tracker deferred |
| Backups | git + `scripts/backup_dbs.py` (sqlite `.backup` API) | zero-dependency |
| Deployment | localhost only (127.0.0.1:51764) | multi-tenant schema means a hosted deployment is a config change, not a rewrite |

## Data model (agency.db)

```
clients(id, name, company, status, services TEXT/json, contacts json, notes, mrr_cents, created_at, updated_at)
projects(id, client_id→clients, name, type, status, stage, stage_total, health, brief, due_date, budget_cents, created_at, updated_at)
tasks(id, project_id→projects, title, status, priority, assignee, due_date, notes, created_at, updated_at)
approvals(id, project_id, title, detail, kind[copy|deploy|outreach|proposal|other], status[pending|approved|rejected], blocking INT, source_agent, payload json, created_at, decided_at, decided_by)
agents(id, name, role, purpose, gateway_target, permissions json, approval_required INT, status, last_run_at)
agent_runs(id, agent_id→agents, project_id, state[idle|planning|running|waiting|needs_approval|blocked|failed|completed], summary, cost_cents, tokens_in, tokens_out, started_at, finished_at, error)
seo_keywords(id, client_id, keyword, intent, volume, difficulty, position, prev_position, url, source, provenance[estimated|imported|verified], tracked_since, updated_at)
boards(id, name, kind[default|client|custom], client_id NULL, layout json, created_at, updated_at)
activity_log(id, ts, actor, action, entity_type, entity_id, detail json)
schema_migrations(version, applied_at)
```

## API surface (new, all JSON)

```
GET  /api/agency/overview        one call → everything the default board needs
GET|POST /api/agency/clients     ·  POST /api/agency/clients/update|delete
GET|POST /api/agency/projects    ·  POST /api/agency/projects/update|delete
GET|POST /api/agency/tasks       ·  POST /api/agency/tasks/update|delete
GET  /api/agency/approvals       ·  POST /api/agency/approvals/decide
GET  /api/agency/agents          merges registry + live Hermes telemetry
GET|POST /api/agency/keywords    ·  POST /api/agency/keywords/update|delete
GET|POST /api/agency/boards      ·  POST /api/agency/boards/update
GET  /api/agency/activity        ·  GET /api/agency/health
```

Mutations: `Authorization: Bearer <AGENCY_TOKEN>` required. Reads: localhost-open (same as today).
Existing endpoints (`/api/bridge/*`, `/api/snapshot`, …) unchanged — the new UI consumes them for agent chat + telemetry.

## Frontend structure (Concept 4)

```
app/
  index.html          shell: top bar (board tabs, theme, add-widget), grid mount
  css/app.css         design tokens (light+dark), grid, widget chrome
  js/main.js          boot, board loading, layout persistence
  js/api.js           fetch wrapper (token, error envelope, provenance helpers)
  js/grid.js          12-col grid render + drag/resize + save layout
  js/widgets/*.js     one module per widget, each exports {id, title, render, refresh}
```

Widget contract: a widget declares its data endpoint(s), renders three states honestly — **loading / empty / not-connected** — and never shows a number without a source chip. v1 widget set: approvals, project health, clients, agent fleet, activity, deadlines, stats (clients/projects/approvals/agents), agent bridge (send + stream), SEO keywords, alerts.

## Integrations

Adapter pattern, config in `.env`, none hardcoded. v1 ships **no external integrations** — GSC/GA4/Ahrefs/Stripe widgets render "Not connected" with setup instructions rather than fake data. Bridge to Hermes gateway is the only live integration (it already exists).

## Security model

- Bind 127.0.0.1 only (unchanged) · bearer token on all mutations (new) · no secrets in code/git (`.env` stays in Hermes home) · audit log on every mutation (new) · upload guards preserved · agent actions with external effect (email, deploy, publish) must route through `approvals` before any send.

## Testing

`tests/test_agency.py` (stdlib `unittest`): schema migration idempotency, CRUD round-trips, approval decide flow, token rejection, provenance required on keyword insert. Existing 2 test files keep passing.
