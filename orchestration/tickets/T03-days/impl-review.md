# T03 implementation review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive), pointed at the eight owned files, ticket.md, plan.md (incl. §8 binding amendments), SPEC §3.4/§6.1/§6.2/§6.3/§18.3 items 1/12/17/18, the contracts, and core infra. The review was asked to specifically probe the three dispatch-mandated areas: planning-date boundary arithmetic, position re-packing after removal, and replan request semantics.

## Codex findings

**Finding 1 — boundary.py:191 (as reviewed): timeout not actually bounded.**
`_judgment_with_timeout` used `with ThreadPoolExecutor(...)`; on a `FuturesTimeout` the context-manager exit calls `executor.shutdown(wait=True)`, which blocks until the hung adapter thread finishes — so `run_boundary` would stall past `config.boundary_timeout_seconds`, violating SPEC §6.2 (60s caller-owned timeout) and plan §4/A1's "failure recorded promptly".

Codex explicit clean areas:
- **Area 1, planning date:** no violation. Arithmetic shown: 04:59 − 5h → 2026-07-04 23:59 → date 2026-07-04; 05:00 − 5h → 2026-07-05 00:00 → date 2026-07-05. test_a01 asserts exact values, config hour honored.
- **Area 2, day-ticket removal:** no violation (association-only delete, contiguous re-pack preserving order, state untouched, exact `{ticket_id}` payload; test_a12 asserts all four fence clauses).
- **Area 3, plan tree:** no violation (root invalidation → all nodes invalidated + exactly one `ReplanRoot`; child invalidation → only that child + one `ReplanChild(position)`; accept-all → every node accepted + exactly-once idempotent day-list add; test_a17 covers all clauses including the already-present-ticket case).
- **Area 4, boundary job:** all other clauses implemented (guard-first, deterministic pass, both skip branches, failure path, proposed-tree storage); test_a18 covers first tick, second-tick no-op with adapter call count, and BOTH skip branches.
- **Area 5, purity/layering:** no violation. (Codex noted dirty `PROGRESS.md`/`decisions.md` in git status — those are the top-level orchestrator's files, outside T03's owned set, not touched by this ticket.)
- **Area 6, test integrity:** no violation — exactly the four named functions, no skip/xfail/empty bodies, no noqa/type-ignore suppressions.

## Orchestrator disposition

**Finding 1 — Accept, fixed directly by the orchestrator** (small fix in an owned file, per playbook step 5). `_judgment_with_timeout` now creates the executor without the context manager and calls `executor.shutdown(wait=False, cancel_futures=True)` in a `finally`, so the timeout exception propagates immediately and the abandoned worker thread is left to finish on its own. The implementer had independently flagged this same risk in its handoff notes. Behavior under the fakes is unchanged (they return/raise instantly); the fix matters only for a genuinely hung real adapter (stage 4).

Post-fix gates, run fresh:
- `.venv/bin/pytest tests/unit/test_days.py -q` → 4 passed.
- `.venv/bin/ruff check src/planner/days/ tests/unit/test_days.py` → All checks passed!
- `.venv/bin/mypy src/` → Success: no issues found in 49 source files.

No other findings. No refutations needed.
