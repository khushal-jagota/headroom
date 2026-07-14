# Final two-axis re-review

## Standards

- `PROGRESS.md` retained stale diagnostic wording that said the router, runner interruption, and test
  coverage were still broken. This violated the repository memory rule that `PROGRESS.md` is the
  current snapshot rather than historical narration.

No baseline smell findings.

## Spec

- Scope creep: the contract fixes public shapes and permits only concrete
  `SharedGateway.interrupt` to gain the optional deadline. `LiveSession.interrupt`, which is publicly
  re-exported, also changed from required `timeout` to mutually exclusive `timeout | deadline`.
  Preserve the absolute deadline through a private session/manager seam instead.
- Wrong documentation: the outcome requires both role gateways to be cleaned up, but
  `docs/employee-runtime.md` calls these “both Employee roles.” Production has one worker connection
  and one Chief-of-Staff connection.

Summary: Standards found one stale-memory violation and no smells. Spec found one public-scope
violation and one inaccurate documentation phrase.
