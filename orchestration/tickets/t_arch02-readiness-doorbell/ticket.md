# t_arch02 — Put readiness ringing behind domain actions

## Outcome

Routes stop knowing about the readiness loop. Successful domain actions ring a small best-effort readiness doorbell after committing whenever their result could affect whether a Ticket should run. The database and periodic readiness scan remain canonical; the ring only asks the loop to check sooner.

## Public contracts

- `ReadinessDoorbell` exposes one non-raising `ring()` operation.
- Production provides a best-effort implementation around the live readiness loop and a no-op implementation when no loop runs. Recording behavior belongs in test support.
- A delivery failure is logged and never changes a successful domain response or committed state.
- Domain action modules own the `write/commit -> ring` sequence. Routes parse, authorize, serialize, and call actions; they never import the readiness loop or decide whether to ring.
- Low-level data writers stay runtime-free.
- Ring exactly once after a successful actual readiness-affecting mutation; never ring on validation/auth/DB failure or a true no-op.
- Ring conservatively after these successful Ticket actions: create, Chief external-work create/reconcile, delete, accept proposal, approve Review, settled-field edit, scope change, direct state change, drop, takeover, and release.
- Revision delivery remains ticket `t_arch01`'s direct runner handoff and does not route through the doorbell.
- Worker proposal routes do not add a redundant ring; runner settlement owns automatic continuation.
- Ring after actual day membership add or removal for any day. Change day removal's low-level result only as needed to distinguish a removed row from a no-op.
- Ring after adding or removing a `blocks` link. Other link kinds do not affect readiness and do not ring.
- Runner settlement rings once after its canonical settlement is closed/committed. The internal empty-to-running claim does not ring.
- Multi-process requests handled by a process without the polling lock use a no-op doorbell; the lock owner's timer remains the accepted backstop.

## Red-first acceptance tests

1. A recording doorbell matrix covers every listed action: one ring after an actual successful mutation, zero on validation/auth/DB failure, and zero on no-op.
2. Duplicate day add and absent day removal ring zero; actual add/removal ring once.
3. Add/remove of a `blocks` link rings once; other link kinds ring zero.
4. The previously missed takeover and day-removal paths ring once.
5. A throwing delivery callback wrapped by the production best-effort doorbell leaves one Ticket action and one Day or Link HTTP action successful and durably committed, while logging the delivery failure.
6. With a long readiness timer already waiting, creating and placing a Ticket through FastAPI triggers route -> domain action -> real doorbell -> readiness loop -> fake runner without waiting for the timer.
7. Preserve automatic step chaining by wiring runner settlement through the doorbell.
8. Startup tests prove dispatch disabled or polling-lock loss still yields an employee runner, no readiness loop, and a no-op doorbell.
9. A source-level regression proves Ticket and Day routes no longer import `TicketReadinessLoop`, `get_system_a`, `Sa`, or `_poke`, and server state exposes no `system_a` route dependency.

## Contract and implementation scope

- Add `src/planner/runtime/readiness_doorbell.py` with the protocol and real/no-op implementations.
- Add or extend narrow domain application seams in `tickets/actions.py`, `days/actions.py`, and the existing link domain's corresponding action module.
- Rewire Ticket/Day APIs, link operations, runtime settlement, startup composition, and focused tests.
- Update live docs to describe the doorbell as best effort and the database/timer as canonical.

## Explicit exclusions

- No generic mutation service, event bus, durable queue, IPC wake, retry system, new event kind, or database migration.
- No ring for ordinary Ticket attribute PATCH, notes, recap, sprint-item parentage, project/sprint edits, chat/session persistence, day text, or non-block links.
- Do not reopen or merge the atomic Chief external-work writers; wrap their completed operation.
- No frontend work and no candidate 4 or candidate 6 work.
