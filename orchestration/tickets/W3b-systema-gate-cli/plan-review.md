# W3b plan-review — codex dispositions

Codex `gpt-5.5`, read-only, high effort. Raw trace: `plan-review.out` (the first run was
reaped when the machine slept overnight; this is the capped re-run). **Verdict:
APPROVE-WITH-FIXES** — 1 blocker, 2 should-fix, 1 nit. All four ACCEPTED + amended into
`plan.md`; sound axes confirmed.

## F1 — BLOCKER: `on_idle`/`poke` arity mismatch — ACCEPTED, fixed

System B's idle callback is `Callable[[str], None]` and is invoked with `ticket_id`, but the
plan wired `set_idle_callback(system_a.poke)` where `poke()` is zero-arg → `TypeError` at
runtime (the step-completion fast path would silently never fire) / mypy failure.

Fix: `SystemA.poke(self, _key: str | None = None) -> None` — accepts and ignores the key from
the idle callback; the API-side `sa.poke()` calls still work. Wired directly as
`system_b.set_idle_callback(system_a.poke)`.

## F2 — SHOULD-FIX: stale set-off (read-then-enqueue) race — ACCEPTED, fixed by an injected guard

`has_inflight` closes the *duplicate-enqueue* window but not the *read → queue-execution*
window: a human `drop` / `grant at_cap=stop` / park / add-blocks-link between System A's
readiness read and the queue worker's start would still run a stale prompt (System B's start
write at `system_b.py:136-139` is unconditional).

Fix (better than a hand-rolled re-check in System B): factor the readiness predicate into a
pure `src/planner/runtime/readiness.py::is_runnable(conn, ticket)` (terminal / has-gating /
no-parked-proposal / grant-not-stopped-at-ceiling / not-blocked — **no `has_inflight`**).
- System A readiness = `is_runnable(conn, t) and not system_b.has_inflight(t.id)`.
- `SystemB.set_off(..., *, guard: Callable[[Connection, Ticket], bool] | None = None)`; System
  A passes `guard=is_runnable`. System B re-reads the ticket at execution time (inside the
  serialized run, before the start-write) and **skips the run** (no status write, ticket
  untouched) when `guard` is present and returns False.
- **`guard` defaults `None`** → W3a's committed System B tests (which call bare `set_off`) are
  byte-for-byte unaffected: no guard → no skip → identical path. One shared predicate, no
  duplication, no `has_inflight` confusion (the ticket is inflight by definition at exec time).

This keeps System A the sole readiness authority and System B the sole status writer (the skip
writes nothing).

## F3 — SHOULD-FIX: fresh-ticket kickoff misses the fast path — ACCEPTED, fixed

The poke list omitted `POST /tickets`; a fresh `empty`/`propose` ticket is ready immediately,
so without a create-poke kickoff waited up to `tick_seconds`. Fix: poke System A after a
successful `create_ticket` too (added to the poke set).

## F4 — NIT: "no full-table rescan" claim — ACCEPTED, reworded

The candidate SQL returns a candidate-only *result set*, but with only `idx_tickets_state`
present the query plan may still scan. Reworded the claim to "candidate-only result set (no
per-tick full-table unpack)"; a `(status)`/`(status,state)` index is noted as a deferred
optimization. Schema stays identical to W3a (no SCHEMA_VERSION bump) per the ticket — a
single-user board's candidate set is tiny.

## Sound axes confirmed by codex

- Readiness semantics: `agent_working`/`errored` excluded by status; parked-vs-auto-accepted
  distinction matches System B's advanced-or-parked mapping + `decide_file_proposal`; the grant
  stop condition matches `admission.check_agent_proposal`.
- Hermetic safety: `create_app` skips background loops under `test_mode`; e2e sets
  `PLAN_TEST_MODE=1`; the `app.state.system_a` null-guard is the right shape — verify never
  spawns a real gateway.
- Import-cycle / mypy: the day filter must not import `days.api` (it imports `tickets.api`).
  Implemented cleaner than planned — a pure `days/logic/dates.resolve_day_id(seg, now,
  boundary_hour)` shared by both the day routes and the ticket `--day` filter (no api import at
  all). `MindQueue(run, *, on_idle=None)` is backwards-compatible with the positional W1 tests.
