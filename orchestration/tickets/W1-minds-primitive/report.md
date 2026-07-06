# W1 — Agent-operation primitive: orchestrator report

## What was built

`src/planner/minds/` — the standalone agent-operation primitive per the ticket, purely additive
(8 new files, zero existing files modified; nothing imports `planner.minds` yet):

| File | Lines | Content |
|---|---|---|
| `src/planner/minds/__init__.py` | 43 | public re-exports (`fake`/`smoke` deliberately not re-exported) |
| `src/planner/minds/gateway.py` | 281 | `ChildProcess` Protocol + `SpawnFn` (the injection seam), `PopenChild`/`spawn_popen`, `GatewayChild` — reader thread routing responses-by-id vs events, ready gate, child-death signaling, stderr tail, shutdown (close stdin → grace → kill) |
| `src/planner/minds/runner.py` | 151 | `run_step(...) -> RunResult` — env assembly (`HERMES_PYTHON_SRC_ROOT`/`HERMES_HOME`/`HERMES_TUI_SKILLS` + `context_env`), create-vs-resume, prompt.submit, event drain with no run deadline, full status mapping, child always reaped in `finally` |
| `src/planner/minds/queue.py` | 72 | `MindQueue[T]` — one in-flight run per durable `session_key`, FIFO per key, concurrency across keys, injected run callable, `wait_idle` |
| `src/planner/minds/fake.py` | 163 | `FakeGateway` — implements `ChildProcess`; canned per-method `Reply` scripts; frames flow through the REAL router |
| `src/planner/minds/config.py` | 70 | interpreter/home resolution (`PLAN_HERMES_PYTHON`/`PLAN_HERMES_HOME`, default `~/.hermes/hermes-agent/venv/bin/python`), `hermes_src_root`, `boot_smoke_check` (nothing calls it this wave) |
| `src/planner/minds/smoke.py` | 143 | real-gateway smoke, human-run only (`python -m planner.minds.smoke`), mirrors spike probes 1-3; not pytest-collectable |
| `tests/unit/test_minds.py` | 583 | 28 tests, all against the fake double — no subprocess, no model calls |

Design decisions and protocol facts are in `plan.md` (§0 ground-truth table with source
citations; §12 binding amendments). Pipeline artifacts: `plan.md`, `plan-review.md`,
`impl-review.md`, this report.

## Test results (run fresh by the orchestrator, not quoted from the implementer)

```
.venv/bin/ruff check .                      → All checks passed!
.venv/bin/mypy src/                         → Success: no issues found in 91 source files
.venv/bin/pytest tests/unit/test_minds.py   → 28 passed in 0.45s
.venv/bin/pytest tests/unit                 → 134 passed, 1 warning in 2.24s
```

(The 1 warning is the pre-existing starlette `TestClient` deprecation, unrelated.)
Full `./verify` intentionally NOT run here — integrator runs it serially per contention rule.

## Review outcomes

### Plan review (codex, gpt-5.5 high) — verdict REPLAN, resolved by amendment

Three findings, all one axis: the planner had keyed the queue on a caller-owned "mind identity"
instead of the ticket's literal `session_key`. Dispositions (full text in `plan-review.md`):
- F1 (BLOCKER, key contract) — ACCEPTED: contract restored to "key IS the durable
  `session_key`" (ticket law); mechanics unchanged (codex judged the locking race-free).
- F2 (BLOCKER, step-0 two-creates) — ACCEPTED as observation, resolved by descoping the claim:
  W1 does not route step-0 through the queue and prescribes nothing about item contents; the
  wave-3 boundary is documented in the queue docstring (resolve the current stored key at
  execution time, not enqueue time). See "Concerns for the integrator" below.
- F3 (MAJOR, tests keyed wrong) — ACCEPTED: queue tests re-keyed to session-key-shaped strings;
  ticket bullets covered verbatim.
Codex explicitly found clean: protocol shapes, run_step mapping, locking, hermeticity,
mypy/ruff, scope. The REPLAN verdict was dispositioned as amendments (plan.md §12) rather than
a structural re-plan, since every clean part survived untouched.

### Implementation review (codex, gpt-5.5 high)

Verdict **APPROVE-WITH-FIXES**. All seven substantive sections clean (protocol shapes; queue
single-in-flight invariant; run_step mapping/reaping/no-wall-timeout; gateway races/deadlocks;
test integrity & hermeticity; mypy/ruff/stdlib/smoke-collection; import wiring). Codex ran its
own verification inside the review: ruff + mypy + pytest all passed (28 tests). One finding:
- F1 (MINOR, `__pycache__` bytecode under the new package "outside the eight-file scope pin") —
  REFUTED as not part of the change: interpreter-generated, `.gitignore:3-4` covers it
  (verified with `git check-ignore`), `git status` shows exactly the 8 authored paths.
Zero code changes required by review. Post-review fresh re-run: ruff clean, mypy clean
(91 files), `28 passed`. Full text + disposition in `impl-review.md`.

### Orchestrator spot-checks (own reading, all 8 files)

- Frame router (`gateway.py`): response-vs-event split, payload-key-omission tolerance,
  gateway.ready without session_id, id-null frames ignored, `_on_child_dead` ordering
  (dead → fail pending → ready gate → event sentinel), pending registered before send — all
  match the verified gateway source behavior.
- `run_step` mapping: table implemented verbatim (complete/interrupted/error → RunResult;
  error event → errored; EOF → errored; 4007/4009 via `GatewayRpcError` → errored;
  `session_key` = `stored_session_id` on create, `resumed` chain tip on resume, best-known on
  failure); child reaped in `finally`; no run deadline (design: timeout-as-failure removed).
- `MindQueue`: submit/drain interleavings walked by hand — no lost item, no double worker, no
  lost wakeup; empty-key `ValueError`; A2 docstring contract exact.
- Fake fidelity: event frames built exactly as `_emit` does; response echoes the request id;
  unscripted methods → structured `-32601` (never a hang); `close_stdin()` → EOF like the real
  stdin-EOF exit; `send()` after death raises `BrokenPipeError`.

## Deviations from the ticket

None in behavior or scope. One test-file detail vs the plan (not the ticket): the plan's test
header listed a `GatewayRpcError` import no test uses directly; importing it would trip ruff
F401, so it was dropped from the test imports (still exported and used by `runner.py`).

## Concerns for the integrator

1. **Wave-3 boundary (from plan-review F2, deliberate descope):** the queue serializes per
   `session_key`; it cannot serialize two step-0 runs for the same mind (both have no key yet),
   and a queued item that captured `session_key=None` at enqueue would `session.create` even if
   an earlier run already created the mind's session. Wave 3's System B wiring must (a)
   serialize kickoff itself, and (b) resolve the mind's CURRENT stored key at execution time,
   not enqueue time. Documented in `queue.py`'s docstring.
2. **Rare slow-error window in `gateway.py.request()`:** a request that passes the dead-check in
   the same instant the reader thread sweeps pending in `_on_child_dead` will wait its full
   `request_timeout` (default 60s) before raising — the outcome is still `errored`, just slow.
   Not a correctness violation; a later hardening could re-check `_dead` after registering.
3. **`DEFAULT_PLANNER_HOME = "data/hermes-home"` is provisional** — the dedicated-home
   provisioning question (spike 01 sub-Q1) owns the real answer; nothing consumes it this wave.
4. **Request ids start at 1 and increment** — documented contract in `gateway.py`'s docstring;
   one fake-router test (out-of-order responses) relies on it.
5. The real-gateway smoke (`python -m planner.minds.smoke`) has NOT been executed (per ticket:
   human-run, model-cost); mypy/ruff-clean only.

## Spot-check pointers (ticket's named integrator checks)

- Response-vs-event routing: `src/planner/minds/gateway.py:156-197` (reader loop + child-death).
- `run_step` status mapping: `src/planner/minds/runner.py:105-151` (drain loop + mapping).
- Queue single-in-flight invariant: `src/planner/minds/queue.py:43-72` (submit/_drain lock
  discipline); tests `tests/unit/test_minds.py:467-542`.
