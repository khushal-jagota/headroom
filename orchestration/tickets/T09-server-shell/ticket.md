# T09 — Server shell: WS tailer, error handling, claim validation, test endpoints (stage 4)

## Scope

Turn the T01 app-factory stub into the real server shell. No domain routes here (T10/T13 own those); this ticket delivers the infrastructure they plug into.

Contracts: `core/contracts.py`, `core/errors.py`, `core/config.py`, `core/clock.py`, `core/events.py`, `core/db.py`, adapter registry — all as on disk. SPEC §9 (WS semantics), §7.6 (write validation), §13 (test mode), §2 (bind), decisions D5/D6.

## Files owned

- `src/planner/core/server.py` — app factory completed: PlannerError→JSON handler with the status mapping from the T01 plan (§10.5); static `assets/` mount; `GET /` app shell page (placeholder until stage 5); `GET /api/meta`; router inclusion for all domains; startup assertions (title_max_chars == DDL literal; data dirs created); lifespan management for background loops (started only when NOT test_mode, per D6 — the loops themselves are T11's, the shell exposes registration hooks).
- `src/planner/core/ws.py` — `WS /api/events?since=<id>`: tails the events table every `ws_poll_ms` (config), pushes `{events: [...], cursor}` batches; `since` resumes from a cursor; connection-per-client polling loop; clean disconnect handling.
- `src/planner/core/authctx.py` — the §7.6 request context dependency: extracts `X-Plan-Run-Id`/`X-Plan-Claim`/`X-Plan-Actor`; classifies the request (claimed-agent / plain-agent / human); `require_claim(ticket_id)` validates run id + claim token against the ticket's active claim and TTL, rejecting stale/foreign/absent with `stale_claim` and a detail naming the mismatch (never echoing the real token); `reject_agents()` for (H) routes → `agent_forbidden`.
- `src/planner/core/testmode.py` — the test router (mounted only under test_mode): `POST /api/test/set-now {now}` mutates the TestClock; `POST /api/test/tick-boundary` and `POST /api/test/tick-dispatcher` invoke tick callables registered by T11 (until T11 lands, they call the registered hook or 501); all three 404 outside test mode by not being mounted.

## Behavior notes

- WS is an invalidation signal: payload is the raw event rows; no reconciliation server-side.
- The §7.6 dependency is used by T10/T13 route implementations; its API must be importable and typed now.
- `plan serve` (existing CLI handler) must run this app cleanly: `PLAN_TEST_MODE=1 PLAN_PORT=8799 plan serve` boots, `/api/meta` answers, WS accepts and streams an appended event, test endpoints respond, Ctrl-C exits clean.

## Acceptance for integration

Self-smoke (scripted, not committed as tests — e2e items cover this later): boot under test mode on a temp DB; `GET /api/meta` 200; WS `?since=0` receives an event appended via `append_event`; `POST /api/test/set-now` changes the planning date returned by a probe; stale-claim rejection unit-exercisable by calling `require_claim` directly. ruff + mypy strict clean; existing unit suite stays green.

## Pinned seam with T11 (binding for both tickets)

T09 and T11 run concurrently; this seam is fixed so they never touch each other's files:
- Test tick endpoints lazily import and call `planner.dispatch.runtime.run_tick(conn_factory, config, clock, adapters)` and `planner.days.scheduler.run_boundary_tick(conn_factory, config, clock, adapters)`; each returns a small JSON-able report dict. Until T11 lands, ImportError/AttributeError → HTTP 501 `{"error": {"code": "validation", "message": "runtime not wired yet"}}`.
- The lifespan, when NOT test_mode, lazily imports and calls `planner.core.loops.start_background_loops(config, clock, adapters, conn_factory)` → returns an object with `.stop()` awaited at shutdown; ImportError tolerated (log, continue) until T11 lands.
- authctx public API (T10 will consume): `RequestContext` dataclass (actor: str, run_id: str | None, claim: str | None, is_claimed_agent: bool, is_human: bool), FastAPI dependency `request_context`, `require_claim(conn, ctx, ticket_id, now) -> None` (raises stale_claim), `reject_agents(ctx) -> None` (raises agent_forbidden).
