# Final two-axis re-review disposition

## Standards

Accepted. The root orchestrator rewrote the current shutdown cycle in `PROGRESS.md` as the live
implemented-and-reviewed state and removed the stale diagnostic claims. No production correction is
required for this finding.

## Spec

Both findings are accepted. The public `LiveSession.interrupt(*, timeout)` signature will be restored
unchanged. The deadline-aware shared gateway will use a private live-session entry point that passes
the original absolute deadline to the existing private manager operation; no exported contract gains
a deadline parameter. The focused lock/reply tests will continue proving the same absolute deadline.

The Employee runtime doc will name the worker and Chief-of-Staff connections accurately in plain
language. No behavior or additional lifecycle concept is introduced.

## Correction outcome

Both corrections are complete. The exported timeout-only signature is restored; the exact absolute
deadline uses only the private session/manager seam. Focused session and gateway tests pass, Ruff is
clean, mypy passes across 113 source files, and the diff check is clean. The documentation now names
the worker and Chief-of-Staff connections directly.
