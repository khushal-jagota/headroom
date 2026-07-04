# T10 — Domain APIs over the canonical writers + derived views (stage 4)

## Scope

Implement every route in the T01 route table (plan.md §13) for tickets, sprints/items/ideas, days, links, and runs — thin HTTP shells over the stage-3 writers and pure logic. Plus the derived read views: `GET /api/board`, `GET /api/queues` (approvals ordered oldest-pending-first / pickup per §7.2 ordering / overdue per §4.5), `GET /api/sprint/current` (§5 view with rollups, blockers_cleared, loose tickets), `GET /api/day/{date}` (materializes on read; accepts literal `today`).

Contracts: the route table (T01 plan §13 + amendments), domain contracts, `core/authctx.py` (T09), and the stage-3 data layers as they exist on disk — read them before planning; route handlers NEVER contain domain rules (one canonical writer per edge lives in data layers; API converts HTTP⇄writer calls).

## Files owned

- `src/planner/tickets/api.py`, `src/planner/sprints/api.py`, `src/planner/days/api.py`, `src/planner/dispatch/api.py` — replace NotImplementedError stubs with real handlers.
- `src/planner/tickets/views.py`, `src/planner/sprints/views.py` — read-view assembly (board, queues, sprint-current, rollups) as query functions; pure assembly, no writes.
- Request/response pydantic models live inside each api.py, mirroring contracts field-for-field (no new shapes).

## Behavior notes

- (H) routes call `reject_agents()`; claim-carrying writes call `require_claim`; plain-agent proposal/recap writes pass the actor through (§7.6).
- Accept/edit-accept carries the mandatory grant pair; absence → structured error from the engine (do not pre-validate in the route; the writer is the door).
- `GET /api/tickets/{id}/copy-text` returns the §10 plain-text block (title, state, priority, field values, recap, links) as text/plain.
- Every mutation returns the fresh entity JSON; every handler is a thin adapter (parse → writer → serialize).
- Events/WS: writers already append events; routes must not.

## Acceptance for integration

Scripted self-smoke against a test-mode server on a temp DB driving the golden path end to end via HTTP: create ticket → propose success (plain-agent) → human accept with grant pair → state advances; item create/transition/propose/accept; day add/remove ticket; links add/cycle-reject; board/queues/sprint-current/day views return coherent JSON; (H) route with claim headers → agent_forbidden. ruff + mypy strict clean; unit suite stays green.
