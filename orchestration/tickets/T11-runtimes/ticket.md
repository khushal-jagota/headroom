# T11 — Dispatcher runtime, boundary scheduler, real adapters (stage 4)

## Scope

The living parts: the dispatcher tick loop, the boundary scheduler, run-timeout enforcement, and the real adapter implementations. Test mode never runs loops (D6); ticks are callables registered with T09's test router.

Contracts: `dispatch/` data+logic (T05), `days/boundary.py` (T03), adapter protocols/registry, config. SPEC §7.1 (tick order, lock, fail-safe flag), §7.3–7.5, §6.2, §7.4, R3/R5/R7.

## Files owned

- `src/planner/dispatch/runtime.py` — the tick: (1) reclaim expired claims + dead PIDs (T05 primitives); (2) enforce per-run max runtime (running runs past `run_max_seconds` → SIGTERM the pid, close `timed_out`); (3) recompute eligibility; (4) spawn up to `max_runs` minus active. Machine-wide advisory file lock on `dispatcher_lock_path` (fcntl.flock, taken once at loop start, skip tick if held elsewhere); `read_dispatch_enabled` re-read every tick, fail-safe false. Spawning: build SpawnRequest (env per §7.4: PLAN_SERVER_URL, PLAN_TICKET_ID, PLAN_RUN_ID, PLAN_CLAIM; per-run log path under logs_dir), call the registered spawn adapter, record pid or close `spawn_failed`.
- `src/planner/days/scheduler.py` — boundary tick: compute planning date from the clock; if no `boundary_runs` row for it, run the T03 boundary job (deterministic pass; judgment pass through the boundary adapter with `boundary_timeout_seconds`, failure event-logged, day survives; prior-planning skip). Replan execution for day-plan invalidations, serialized latest-wins (R5): a single worker consumes the latest pending replan request; stale in-flight results discarded.
- `src/planner/core/adapters/real.py` — real implementations: RealSpawnAdapter (subprocess.Popen of `hermes -p <profile> --skills <skill> chat -q "work planning ticket <id>"`, detached session, stdout/stderr → the per-run log file, env from SpawnRequest); RealBoundaryAdapter (invokes hermes non-interactively with the deterministic inputs, parses `{brief_markdown, plan_tree}` from its output; best-effort — live use is §18.4 post-run); RealGatewayAdapter (guarded `tui_gateway.ws` import; unavailable → GatewayStatus(available=False)).
- `src/planner/core/loops.py` — asyncio background tasks driving both ticks every `tick_seconds`, registered via T09's lifespan hooks; started only when not test_mode.

## Acceptance for integration

Self-smoke under test mode with fakes on a temp DB: eligible ticket + `POST /api/test/tick-dispatcher` → claim + runs row + FakeSpawn called with correct env/log path; second tick → no double-claim (cap respected); expired claim on next tick → reclaimed. `POST /api/test/tick-boundary` at a fake 05:01 → day row + brief + proposed tree from FakeBoundary; second tick same date → no-op. Real adapters: constructed OK from config; RealSpawn smoke = spawn `/bin/sh -c 'echo hi'` variant via injected binary override writing the log file (never hermes in tests). ruff + mypy strict clean; unit suite stays green.
