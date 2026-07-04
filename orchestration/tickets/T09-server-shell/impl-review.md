# T09 implementation review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root) over the implemented files
(`src/planner/core/server.py`, `ws.py`, `authctx.py`, `testmode.py`,
`orchestration/tickets/T09-server-shell/smoke.py`) against ticket.md (binding seam), plan.md
(amendments A1–A7 at review time), SPEC §7.6/§9/§13, D5/D6.

Process note: the first review invocation hung with zero output for >10 minutes and was killed;
the rerun with a tighter prompt completed normally (per the team lead's guidance).

## Codex findings (verbatim substance)

1. `require_claim` can echo the stored claim token on `run_mismatch`: `_reject_stale` always
   includes `ctx.claim` as `presented_claim` (authctx.py:94), and the `run_mismatch` branch is
   reached only after `ctx.claim != claim_lock` passed — so `ctx.claim == claim_lock` when it
   raises. The smoke exercised this path with the real token but asserted token absence only on
   the `foreign` path. Violates the plan's "the stored token appears in no message and no
   detail, on any path".
2. smoke.py did not guarantee server-subprocess cleanup when Phase C fails after boot: `proc`
   was local to `phase_c` and only handed to `main` on successful return, so an assertion
   between `Popen` and `return` skipped the `finally` kill.
3. Phase B was not isolated to temp lock/log paths: its env set only `PLAN_DB_PATH` + adapter
   pins, so a T11-landed `run_tick` on the 200 path could write the repo-default
   `data/dispatcher.lock` / `data/logs` from config.yaml.

Codex additionally confirmed (no violations found in): seam fidelity (lazy imports, exact call
signature, exact 501 body, lifespan loop start/stop semantics, pinned authctx API), the §7.6
validation order and half-open expiry via `dispatch.logic.claims`, §9 WS batch/cursor/drain and
connection cleanup incl. the A7 watcher, §13/D5/D6 test-router mounting and set-now semantics,
and preservation of every T01 server.py behavior.

## Dispositions

1. **Accepted — real leak channel.** The presenter already knows the token, but the detail is
   loggable/displayable, so an observer of the error surface could learn the live stored token
   on the `expired`, `run_mismatch`, and correct-token `missing_header` paths. Fixed in
   authctx.py (`_reject_stale` now takes the stored lock and replaces `presented_claim` with the
   literal `"(redacted)"` whenever the presented token equals it); smoke checks 4 and 7 now
   assert the redaction and token absence from the whole payload. Recorded as amendment A8(1).
2. **Accepted.** `phase_c` now appends the `Popen` handle to a `procs` list owned by `main`
   immediately after spawn; `main`'s `finally` kills anything in that list. A8(2).
3. **Accepted.** Phase B env now pins `PLAN_LOGS_DIR` and `PLAN_DISPATCHER_LOCK_PATH` into the
   smoke temp dir. A8(3).

## Post-fix verification (run fresh by the orchestrator)

- `ruff check` on the five owned files: `All checks passed!`
- `mypy src/` strict: `Success: no issues found in 81 source files` (file count grew mid-run as
  T11 landed; zero errors anywhere, so zero attributable to T09)
- `pytest tests/unit -q`: all green (64 tests at time of run, incl. T11's newly landed ones)
- smoke: `SMOKE PASS (22 checks)`, exit 0 — including the strict single-SIGINT check 22 and the
  new redaction assertions in checks 4 and 7.
