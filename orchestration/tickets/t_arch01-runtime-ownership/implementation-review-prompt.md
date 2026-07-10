# Item 1 implementation review prompt

Review the staged implementation diff against baseline commit
`2a4f56cd9b61977de174384dcfeedfdd46bf0e76` and all ticket artifacts in
`orchestration/tickets/t_arch01-runtime-ownership/`.

This is a read-only implementation review. Check, with concrete file and line
evidence:

1. Every contract and acceptance test in `ticket.md` and the reviewed
   `plan.md` is implemented.
2. `EmployeeStepRunner` owns claims, prompts, worker sessions, worker Chat,
   settlement, direct revision handoff, admission, and shutdown draining.
3. `TicketReadinessLoop` is only the optional automatic discovery/polling
   mechanism and the runner exists when dispatch is disabled or another
   process owns the polling lock.
4. Automatic work rechecks today's-board membership and full readiness under
   the claim transaction.
5. Direct revision uses reserve -> canonical DB transaction -> release/cancel;
   rejects pre-existing running Chat turns before mutation; handles a
   post-commit Chat-turn race without submitting a prompt or leaving the
   Ticket stuck; and returns success only after the handoff is parked.
6. Revision resume is strict-existing-session: a missing or stale stored
   Hermes session key never creates/remints a session, never submits a prompt,
   and never sends a context acknowledgement. Normal automatic execution must
   retain resume-or-create behavior.
7. Stop closes admission, rejects later reservations, drains accepted work,
   then shuts down the gateway in the required lifecycle order.
8. Tests exercise the real boundaries and relevant races, not merely mocks or
   implementation details; regression coverage for Chief external intake,
   worker context, and Chat images remains intact.
9. The implementation stays within item 1. In particular, it must not solve
   the separate SharedGateway completion-correlation defect, implement the
   item 2 doorbell architecture, implement item 3 atomic PATCH, add queues or
   retries, change schema, or redesign the frontend.
10. Names and documentation contain no live `SystemA`, `SystemB`, `system_a`,
    or `system_b` references.

Report only actionable violations introduced or left unresolved by this diff.
For each, give severity, file/line evidence, violated contract, and a concise
fix. If there are no violations, end with exactly `NO VIOLATIONS`.
