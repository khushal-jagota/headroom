# Independent implementation review

1. **[P1] Live guidance still uses deleted runtime names.**
   `CLAUDE.md` still references `runtime/system_a.py`, `runtime/system_b.py`,
   System A, and System B, violating the required live terminology rename.
2. **[P1] Build memory is stale.** `PROGRESS.md` says implementation has not
   started and still lists item 1 as next. Update it before integration.
3. **[P2] Shutdown draining is not tested through a released real run.** The
   existing test only cancels the parked handoff. Release into a blocking
   gateway run and prove `stop()` waits until that run finishes.
4. **[P2] Tests do not prove the complete readiness predicate is rechecked at
   claim time.** The current test removes today membership, which short-circuits
   before `readiness.is_runnable`. Leave the Ticket on today but invalidate a
   separate readiness condition before `run_ready_step`.
5. **[P3] Loop-start failure coverage proves runner presence, not usability.**
   Use an available gateway stub and prove reserve/cancel or direct revision
   remains usable after readiness-loop construction fails.

All five findings were accepted because each follows directly from the reviewed
ticket or implementation plan. Their resolution and follow-up review are
recorded in `implementation-report.md`. The reviewer inspected the corrected
diff and returned:

```text
NO VIOLATIONS
```
