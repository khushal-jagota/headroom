# Item 2 implementation review prompt

Review the complete item 2 diff against baseline commit `2e3a6ca` and the
reviewed `ticket.md`, `plan.md`, and plan-review artifacts in this directory.
This is a read-only implementation review.

Check, with concrete file and line evidence:

1. `ReadinessDoorbell` is one non-raising `ring()` protocol. Production has
   only a best-effort loop adapter and no-op implementation; the adapter logs a
   delivery exception once and adds no retry, queue, persistence, IPC, ticket
   key, or generic event/mutation machinery.
2. Explicit Ticket, Day, and Link actions own write/commit -> ring. Routes only
   parse, authorize, call actions, and serialize; low-level writers remain
   runtime-free. Ticket/Day routes never call `.ring()` or know the concrete
   readiness loop.
3. Ring exactly once after each approved actual successful mutation and zero
   after validation, authorization, database failure, or a true no-op. Deleting
   a Ticket rings once even if its transaction removes several memberships or
   links.
4. Chief external create/reconcile writers remain unchanged. Reconcile compares
   complete Ticket semantics while ignoring only `updated_at`; normalization-
   only `errored -> empty` rings once and the next exact replay rings zero.
5. Day add/remove actions own one transaction and ring for actual membership
   changes on any day. Duplicate add and absent removal ring zero;
   `remove_day_ticket` changes only enough to return whether a removal occurred.
6. Link actions begin the transaction before the existing writer. Only blocks
   add/remove rings; every non-block kind and all duplicate/cycle/constraint/
   missing-remove failures ring zero.
7. `EmployeeStepRunner` rings exactly once only after a canonical settlement
   has committed and its active count is decremented. Normal, interrupted,
   gateway error/crash, busy, ownership-lost, and accepted-revision settlement
   branches ring; stale/non-runnable discovery, losing claim, internal claim,
   and cancelled reservation do not. The temporary idle callback is gone.
8. Composition injects/exposes one selected doorbell. Dispatch-disabled and
   polling-lock-loser branches retain an available runner with a no-op. The lock
   winner binds a real adapter to loop wake before start. A construction/start
   failure stops any partial loop, releases the lock once, discards the real
   adapter/runner, and composes a fresh no-op runner. Shutdown ordering from
   item 1 is preserved.
9. The long-timer fast-path test can only be satisfied by HTTP Day-add commit ->
   real doorbell -> loop wake -> scan -> fake runner: first empty scan is
   synchronized, Ticket seeding is low-level, and no earlier Ticket-create ring
   can race. Commit-before-ring tests assert the second connection actually
   observed committed state before a deliberately throwing callback; swallowed
   assertions cannot create a false pass.
10. Tests exercise every approved positive action and every explicit successful
    non-ringing path: ordinary PATCH, notes, recap, worker proposal, sprint-item
    parentage, Project/Sprint edits, Chat/session persistence, day text,
    non-block links, and revision handoff before runner settlement.
11. Item 1 strict revision/session/race/drain contracts remain intact, item 3
    atomic PATCH is not implemented early, and there are no frontend/schema/
    gateway-correlation/candidate 4/candidate 6 changes.
12. Live Markdown and designed HTML docs say the ring is best effort and the
    database/timer are canonical. Source scans show no old route poke/loop
    dependency and `.ring()` only at approved action/settlement ownership seams.

Report only actionable violations introduced or left unresolved by this diff.
For each, give severity, file/line evidence, violated contract, and a concise
fix. If none exist, end with exactly `NO VIOLATIONS`.
