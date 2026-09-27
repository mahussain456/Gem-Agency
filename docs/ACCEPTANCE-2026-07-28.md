# Agency OS — Acceptance Report (v1 + rounds 2–3)
**Date:** 2026-07-28 · **Design:** Concept 4 (Modular Agency Workspace) · **URL:** http://localhost:51764 · legacy UI at `/legacy`

## Completed and verified (tested in the running app or by passing tests)

### Core platform (round 1)
| Feature | Verification |
|---|---|
| Modular widget dashboard, 16 widgets, boards with add/remove/resize/drag + per-board persisted layouts | Browser-verified light + dark |
| Clients / Projects / Tasks / Approvals CRUD with validation | UI + API + unit tests |
| Approval queue with blocking flags, ✓/✗ decide | Clicked in UI, audit-logged |
| Bearer-token auth on all mutations, Host check, path-traversal guard | Negative tests (401/403/400) |
| Audit log on every mutation | Visible in Activity widget |
| Watchdog repair-loop fix, git history, .gitignore | Committed |

### Google Search Console (round 2)
| Feature | Verification |
|---|---|
| OAuth desktop flow (stdlib-only), creds/token in gitignored files, CSRF state on callback | Unit tests + bogus-state rejected live |
| Client→property mapping, sync (update tracked keywords → `verified`, import ≥10-impression queries), 28-day performance panel | Sync logic unit-tested with mocked Google; **live handshake pending operator's Google credentials** |
| Migration 2 (clicks/impressions/ctr, gsc_properties) | Applied to live DB, `schema v2→v3` |

### Round 3 — this session
| Feature | Verification |
|---|---|
| **Agent runs wired to bridge**: real sends create `agent_runs` (running→completed/failed); status checks excluded; `@all` recorded as one unattributed run | Unit-tested lifecycle incl. double-close no-op; Runs widget added |
| **Client detail page** (`#client/<id>`): projects, provenance-labeled keywords, backlink pipeline, competitors, report button | Browser-verified with screenshot |
| **Project detail page** (`#project/<id>`): full 20-stage studio pipeline (10-stage SEO variant), stage advance/back, project approvals + tasks | Browser-verified — all 20 stages render, current stage highlighted |
| **Backlink CRM with enforced ethics gate**: `sent` status is impossible without an approved outreach approval; approval request requires a concrete draft; rejection reverts the prospect | Browser-verified (gate message shown in UI) + API loop (draft→request→attention item→`outreach_drafted`) + 2 unit tests |
| **Competitor tracking** per client | CRUD tested |
| **Client report generator**: JSON + print-ready HTML (`/api/agency/report.html?client_id=`) — projects, work done, verified keyword movement, GSC section or honest "not connected" note, backlink stats, data policy footer | Endpoint returns rendered report live; unit-tested |
| **Global search** across 7 entity types + **Ctrl+K palette** with navigation | Endpoint live-tested; palette wired |
| **Attention bell**: derived list (blocking approvals, failed runs <24h, blocked projects, deadlines ≤3d, GSC errors), red highlight on high severity | Live-tested — outreach approval appeared in attention |
| Migration 3 (backlink_prospects, competitors) | Applied live, integrity ok |

**Tests: 29 total — 27 pass.** The 2 failures are the pre-existing `test_mission_memory.py` attachment tests that also fail at the baseline commit (inherited, untouched by this work).

## Partial / pending operator action

- **GSC live handshake**: needs your Google Cloud OAuth credentials (widget walks you through it). Everything up to the Google door is tested.
- **Bridge live send**: widget wired to the proven endpoint; not fired to avoid spending gateway tokens. First real send will also light up the Runs widget.
- **Drag-to-reorder widgets**: implemented, not browser-exercised (resize/remove/add were).
- **Palette UI interactions**: search endpoint live-tested; palette rendering/keyboard wired but not browser-exercised.

## Not built (honest scope line)

Client portal & multi-user RBAC, push/email notifications (attention list is in-app only), calendar/timeline/kanban project views, visual workflow builder, content brief generator, rank-history charts, GA4/Ahrefs/Stripe adapters (still labeled NOT CONNECTED), automated report scheduling.

## Configuration / rollback

Unchanged from v1: `python server.py`, port 51764, no new env vars, no new dependencies. New gitignored local files: `.agency_token`, `.gsc_credentials.json`, `.gsc_token.json`. Rollback = `git revert` (5 clean commits from baseline `723b00a`).

## Recommended next phase

1. Connect GSC (5 min of your time) → verified data flows through keywords, reports, and the client page
2. GA4 adapter (same OAuth pattern, sessions/conversions into reports)
3. Report scheduling + email/Slack delivery (behind approval, per your rules)
4. Kanban + calendar views on the project page
5. Content brief generator driven by the Scribe agent through the bridge (runs + approvals already in place)
