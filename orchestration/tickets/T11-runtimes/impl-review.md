# T11 implementation review — codex output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root), pointed at the five implemented files, ticket.md (pinned seam), plan.md (with binding amendments A1–A9), SPEC §6.1–6.3/§7.1–7.5/§13/§16, and the frozen contracts. Checks requested: seam fidelity, §7.1 tick order + A5 close-before-kill, test-mode signal safety, lock discipline (A2/A4), R5 replan semantics (A3/generation), §7.4 real-adapter surface, test fidelity/flakiness, owned-set discipline.

## Codex findings (verbatim)

1. **[P1] `BackgroundLoops.stop()` is not truly idempotent and can release a newer loop's lock.**
   A2 requires second `stop()` to be a no-op and `_active` to represent the not-yet-stopped instance. But `stop()` always cancels/gathers, calls `release_dispatcher_lock(self._lock_path)`, and sets `_active = None` (loops.py:47). Sequence: stop `loops1`, start `loops2`, then call `loops1.stop()` again. The old handle can clear `_active` and release `loops2`'s cached lock path. The A9 test misses this because it starts `loops2` after stop but never calls the old `loops.stop()` again while `loops2` is live.

2. **[P1] Owned-set discipline is violated.**
   `git status --porcelain` shows modifications/untracked files outside the five, including PROGRESS.md, T09/T10/T12/T13 orchestration files, requirements.txt, `src/planner/core/server.py`, `src/planner/core/authctx.py`, `src/planner/core/testmode.py`, and `src/planner/core/ws.py`.

(Full raw output archived in the orchestrator session; codex confirmed all other checked areas clean — no seam, tick-order, signal-safety, replan-semantics, adapter-surface, or test-fidelity violations were reported.)

## Orchestrator dispositions

- **Finding 1 — ACCEPTED, FIXED (orchestrator direct edit, small-fix path per playbook).**
  `loops.py`: `BackgroundLoops` gains a `_stopped` flag — a second `stop()` on the same handle returns immediately; and the lock release + `_active = None` now run only under an `if _active is self:` guard, so a stale handle can never disturb a newer instance. During a handle's first legitimate stop `_active` is always that handle (start raises while `_active` is set), so the guard never skips a legitimate release.
  Test extension in `test_stop_waits_for_inflight_tick`: after `loops2` starts, re-stopping the stale `loops1` handle is asserted to be a no-op — `start_background_loops` still raises `RuntimeError` (the live instance was not cleared), then `loops2` stops cleanly.
  Gates re-run fresh after the fix: ruff clean, `mypy src/` Success (80 files), `tests/unit/test_runtimes.py` 18 passed, whole unit suite 64 passed.

- **Finding 2 — REFUTED (false positive from a shared worktree).**
  T09, T10, T12, and T13 orchestrators are working CONCURRENTLY in this same tree by design (stage-4 wave; see PROGRESS.md). `git status` therefore shows their in-flight edits — core/server.py, ws.py, authctx.py, testmode.py belong to T09's owned set, PROGRESS.md to the team lead, etc. That is not T11 writing outside its owned set; the review prompt's instruction to attribute via `git status` was unanswerable in a shared tree. T11's writes are exactly: `src/planner/dispatch/runtime.py`, `src/planner/days/scheduler.py`, `src/planner/core/adapters/real.py`, `src/planner/core/loops.py`, `tests/unit/test_runtimes.py` (implementer session wrote only these; the orchestrator's post-review fix touched two of the same five). No action.
