# T05 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md exactly. Your ticket: orchestration/tickets/T05-dispatch/ticket.md.

Model tiers (D8): planner model "fable" (claim CAS, TTL/reclaim, and the breaker are load-bearing); implementer model "opus".

Ticket-specific guidance:
- The claim CAS must be literally the §7.3 UPDATE ... WHERE claim_lock IS NULL shape; item 14's "concurrent" attempts may interleave two connections but the CAS is the arbiter — no Python-side locking.
- Eligibility (§7.2) is one pure function over DispatchCandidate; ordering is a pure key function; both unit-tested exactly per items 9 and 11.
- links cycle detection is transitive at write time (§3.6) for blocks AND parent_child; self-links and second belongs_to rejected with the structured error codes from core/errors.py.
- The breaker: increments on crashed/timed_out/spawn_failed, reset on done, sticky auto_blocked at failure_limit (config, 2), auto_blocked event; clear action resets both. Item 16 asserts each.
- Codex reviews must specifically cover: CAS correctness under interleaving, TTL arithmetic (heartbeat extends by exactly one TTL from now), reclaim path (run status reclaimed + lock cleared + re-eligible), and that no code path lets an expired claim keep writing.
