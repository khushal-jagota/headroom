# T05 — Dispatch: links/blocking, eligibility, ordering, claims, TTL, circuit breaker (stage 3)

## Scope

The dispatch domain's pure logic and data layer plus the links infrastructure, and unit tests for acceptance items 9, 11, 14, 15, 16 (test names `test_a09_*`, `test_a11_*`, `test_a14_*`, `test_a15_*`, `test_a16_*`).

Contracts implemented against (never modified): `src/planner/dispatch/contracts.py`, `src/planner/tickets/contracts.py` (read-only), `src/planner/core/contracts.py` (LinkKind), `core/errors.py`; infrastructure `core/db.py`, `core/events.py`, `core/clock.py`.

## Files owned

- `src/planner/core/links.py` — link creation/removal over the `links` table: at most one `belongs_to` per ticket (enforced), self-links rejected, `blocks`/`parent_child` transitive cycle check at write time (§3.6); blocked-derivation: a ticket is blocked iff it is the target of a `blocks` link whose source ticket is not `done`/`dropped`.
- `src/planner/dispatch/logic/` — pure: eligibility (§7.2 exactly: not done/dropped/needs_review; not blocked; not auto_blocked; no active claim; current gating field has no pending proposal AND (advance-target ≤ ceiling OR (at ceiling AND at_cap = propose)); at-ceiling+stop never eligible), ordering (priority P0 first, then deadline ascending NULLs last, then created_at ascending), breaker rules (§7.5), claim/TTL arithmetic.
- `src/planner/dispatch/data.py` — claim CAS exactly as §7.3 (`UPDATE tickets SET claim_lock=?, claim_expires=? WHERE id=? AND claim_lock IS NULL`, rowcount 0 = lost), runs row insert on claim, heartbeat extends expiry by one TTL, reclaim on expired claim or dead PID (run → `reclaimed`, lock cleared), run close with outcome, breaker increment/reset (crashed/timed_out/spawn_failed increment; done resets; reaching failure limit (2) sets sticky `auto_blocked` + event; human clear-action resets flag and counter).
- `tests/unit/test_dispatch.py` — items 9, 11, 14, 15, 16.

## Test fences (SPEC §18.3, exact)

9. Ticket blocked by an open ticket is dispatch-ineligible; blocker → `done` makes it eligible; `blocks` cycle creation rejected.
11. Five eligible tickets: P0 before P1; equal priority by earlier deadline; NULL deadline last; then created_at.
14. Two concurrent claim attempts on one ticket → exactly one wins, one `runs` row created (drive both through the CAS; concurrency may be simulated by interleaved connections, but the CAS must be the real UPDATE).
15. Expired claim reclaimed (run `reclaimed`, lock cleared, ticket eligible again); heartbeat extends expiry by one TTL.
16. Two consecutive `crashed` runs → `auto_blocked`, ineligible; unblock action clears flag and counter; a `done` run resets the counter.

## Constraints

- Pure logic imports stdlib + contracts only; no FastAPI/pydantic anywhere here.
- The dispatcher tick loop, spawn adapter invocation, and §7.6 claim validation of API writes are stage 4 — NOT this ticket. This ticket delivers the primitives they will call.
- Files outside the owned list untouched; shared conftest read-only.
- ruff + mypy strict clean; all five named tests green.
