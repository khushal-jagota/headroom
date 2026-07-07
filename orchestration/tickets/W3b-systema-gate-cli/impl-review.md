# W3b impl (diff) review — codex dispositions

Codex `gpt-5.5`, read-only, high effort, run against the working-tree diff (`git diff` from
committed W3a). Raw: `impl-review.out`. **Verdict: APPROVE-WITH-FIXES — no blockers**; 1
should-fix + 1 nit. Both ACCEPTED + fixed. Codex confirmed the four plan-review fixes (F1–F4)
all landed and every audited axis (readiness semantics, fast-path concurrency, hermetic safety,
server wiring, CLI/day-filter, import-cycle, no dangling `pickup`) is sound.

## D1 — SHOULD-FIX: guarded start not atomic with the start-write — ACCEPTED, fixed

`_run` checked `guard(...)` then called `set_run_status(agent_working)`, which opens its own
`BEGIN IMMEDIATE` only afterward. A human drop / grant-stop / block / park committed in that
sub-millisecond gap could still slip a stale `agent_working` write + a real spawn past the guard.

Fix: a new single-door writer `tickets_data.start_run_if_runnable(conn, ticket_id, *, worker,
guard, now)` does the re-read + `guard` + the `agent_working` write **inside one `BEGIN
IMMEDIATE` transaction**. A concurrent human mutation in the gap is now either seen by the
in-txn re-read (guard skips, writes nothing, returns None) or blocked until this commits — the
TOCTOU is closed. System B's `_run` calls it as its start transition and reuses the returned
ticket as `pre` (dropping the now-redundant second read). `guard=None` => unconditional start,
so W3a's bare-`set_off` tests and event trails are byte-for-byte unchanged (verified: full unit
suite green, incl. the four W3a status-transition tests and the kickoff-serialization test).
System B remains the sole `tickets.status` writer.

## D2 — NIT: `--date` not actually exercised — ACCEPTED, fixed

`test_ticket_list_day_filter` claimed to test `--date` but only ran an unscoped list. Added
`ticket list --date 2026-07-04` (the fake clock's planning date) and asserted its result set
equals the `--day today` filtered set.

## Sound axes (codex, confirmed)

- F1 poke arity fixed (`SystemA.poke(self, _key=None)`, wired directly as the idle callback).
- F2 guard: `guard` defaults None; System A passes `readiness.is_runnable`; `is_runnable` carries
  no `has_inflight`; skip writes nothing/never spawns. (D1 above hardens its atomicity.)
- F3 `POST /tickets` pokes System A. F4 candidate SQL is candidate-only, excludes
  agent_working/errored/terminal.
- Readiness semantics match `machine` / `admission` / `resolution`. System A never writes status.
- Fast path: `on_idle` fires after `_active.discard`, outside the lock — no deadlock / double-fire
  / lost-wake. `has_inflight` delegates to the synchronized `is_active`.
- Hermetic: loops skip under `test_mode`; the real `spawn_popen` is reachable only from `loops.py`;
  e2e sets `PLAN_TEST_MODE=1`; API pokes are null-guarded via `_poke`. No dangling `pickup` refs.

## Non-findings

- Codex could not run `mypy --no-incremental` (its read-only sandbox blocks mypy's SQLite cache)
  and saw `ruff check .` trip on pre-existing orchestration smoke scripts. Both are sandbox
  artifacts: locally `.venv/bin/mypy src/` = clean (82 files) and `.venv/bin/ruff check .` = clean.
