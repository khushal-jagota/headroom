# Ticket C — e2e proofs, harness sweep, docs, skills

Implements plan.md §8–§9 on the integrated tree (tickets A and B are landed; the unit, web, and
lint gates are green). Read plan.md and plan-review-disposition.md first.

## Owns
`tests/e2e/**`, `docs/**`, root `CLAUDE.md`/`AGENTS.md`,
`src/planner/skills/panels-ticket-management/SKILL.md` (the two events-history lines).

## Must not touch
`src/planner/**` (except the named SKILL.md), `web/src/**`, `orchestration/**`. No commits.

## Facts fixed by the landed implementation
- SSE endpoint `GET /api/changes`; browser client exposes `window.__plannerDebug` with fields
  `sseOpens` (incremented per EventSource open) and `flushes` (incremented per invalidation round,
  both debounced flushes and the on-open reconciliation).
- Connection pill has exactly two states: `connected`, `reconnecting` (`offline` is gone).
- `/api/meta` is exactly `{test_mode, release_sha}`.
- `panels ticket events` and `GET /api/tickets/{id}/events` no longer exist.
- The `events` table no longer exists; `tickets.ticket_status_changed_at` holds review's
  waiting_since for needs_user tickets.
- `PLAN_WS_POLL_MS`/`PLAN_WS_HEARTBEAT_MS`/`PLAN_UI_DEBOUNCE_MS`/`PLAN_EVENTS_READ_LIMIT` config
  knobs are gone; `PLAN_SSE_HEARTBEAT_MS` (default 15000) is new.

## Deliverables
1. `tests/e2e/test_live_update.py`:
   - change-shows-up-without-refresh: open the board, mutate a ticket through the API helper,
     assert the card updates with no reload.
   - composer acceptance (non-negotiable): open a ticket editor, focus, type, trigger a server
     change to that same ticket mid-composition, keep typing; assert focus retained, full typed
     text intact, scroll position unmoved. If current component behavior fails this, fixing the
     owning component's state locality is in scope (coordinate: that one fix may touch web/src —
     report it, keep it minimal).
2. `test_connection_status.py` rewritten for SSE: route-abort `/api/changes` → pill
   `reconnecting`; restore → `connected`; a change made while blocked appears after reconnect.
3. Delete `test_resource_catalogue.py` (superseded by test_live_update.py).
4. `test_trusted_ingress_browser.py`: the wrong-origin rejection proof moves to
   `/api/conversation`; SSE needs no origin gate (contentless, CORS-blocked reads).
5. Harness sweep: `conftest.py` gates (`wsOpens` → `sseOpens`; the `settled` catch-up-replay gate
   dies — initial fetches are always current), events-table reads/inserts in
   `test_acp_conversation.py`, retired CLI verb in `test_cli_verbs.py`, meta assertion in
   `test_server_lifecycle.py`, plus `test_workers_frontend.py`, `test_stage_ownership_frontend.py`,
   `test_flows_a.py`, `test_flows_b.py`, `test_ticket_file_previews.py` (debug-gate names,
   `PLAN_WS_POLL_MS`, anything event-log shaped).
6. docs: `docs/frontend.md`, `docs/README.md`, `docs/systems.md`, `docs/systems.html`,
   `docs/tickets-and-gates.md`, `docs/cli.md`, `docs/chat.md`, `docs/worker-types.md` — every
   description of the event log, envelope push, mapping, or live updates now describes the new
   mechanism, in the plain language docs/CLAUDE.md requires. AGENTS.md "Frontend reactivity"
   project note: one short paragraph on the new mechanism.
7. `src/planner/skills/panels-ticket-management/SKILL.md` lines telling agents to inspect ticket
   events/history: rewrite to canonical state (fields, recap, status, timestamps).

## Gate
Full e2e suite is NOT the gate (that is the program-final `./verify`). The gate here is a targeted
Playwright run, through the worktree venv, of exactly: `test_live_update.py`,
`test_connection_status.py`, `test_trusted_ingress_browser.py`, plus the harness-touched files you
changed (`test_flows_a.py` or another representative to prove the conftest gates still work).
Survey ports before starting anything; the harness assigns its own free ports per test.
