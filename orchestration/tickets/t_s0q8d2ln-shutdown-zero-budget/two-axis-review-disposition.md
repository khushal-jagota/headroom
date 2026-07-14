# Two-axis review disposition

## Standards

All four hard findings are accepted. The root orchestrator will update `PROGRESS.md` after the
corrections. The Employee runtime doc will own the plain-language shutdown account; the broader
systems doc will keep only a handoff. New test variables will use `ticket_id`.

Both judgment calls are accepted because the cleanup is small and clarifies the tests and runtime.
The matched Employee session, Ticket, and turn identity will become one private named immutable
snapshot. Tests will use the real `GatewayStatus` contract through one shared available value rather
than constructing repeated anonymous dynamic classes.

## Spec

The unbounded command-lock acquisition is valid. The Live-session interrupt will spend one relative
budget across lock acquisition and reply wait; the concrete caller derives that budget from the one
absolute shutdown deadline. A lock-held regression must prove bounded return and no request send.

The child cleanup finding is valid. `GatewayChild.shutdown` will accept the existing absolute
deadline path and recompute remaining time before every process wait and reader-thread join.
`SharedGateway` will pass the absolute deadline instead of converting it once to a reusable grace.
The ticket file boundary is expanded only for this already-required child-cleanup contract and its
focused test.

The ordinary-Pause race is valid. If first-wins Chat settlement is not `complete`, shutdown leaves
the Ticket recoverable, while an ordinary interruption marks the running Ticket errored. A late
complete after ordinary interruption must be tested directly.

The TDD-evidence finding is refuted. The missing-id and parked-reservation cases are preservation
guards for behavior that already existed, so baseline GREEN is the correct evidence; the new positive
interruption, deadline, settlement, cleanup, and process behaviors were each observed RED before
their production changes. Creating an artificial defect merely to make preservation guards red would
not be test-driven implementation.
