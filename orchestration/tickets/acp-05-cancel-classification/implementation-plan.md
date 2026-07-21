# ACP-05 cancellation classification implementation plan

This correction is intentionally local to `_Actor._settle_prompt`.

1. Capture the already-recorded cancellation cause before interpreting `task.result()`.
2. If `task.result()` raises and the cause is user, Send Now, New conversation, or shutdown, continue
   through the existing cause-specific settlement with `response=None`.
3. Preserve generation failure for no cause and for `child_failure`; preserve the existing separate
   cancel-send, permission-cancel, and timeout failure paths.
4. Add deterministic fake-child broker tests whose prompt future raises only after cancel delivery,
   covering Stop and Send Now, plus the no-cause control.
5. Run the exact broker/hub/gateway/e2e cancellation slice, Ruff, and strict Mypy; write the output
   ledger to `implementation-report.md`.

One independent focused diff review checks only cause classification, single settlement/delivery,
and the control. A second round occurs only for a concrete finding.
