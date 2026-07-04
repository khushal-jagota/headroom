# T05 plan review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` pointed at plan.md, ticket.md, SPEC §3.6/§7/§13/§14/§18.3, the four
contract files, and core infrastructure. Verdict: VIOLATIONS FOUND (4 findings).

## Codex findings (verbatim summary)

1. **`close_run` lets an expired claim still write** (plan File 7 / D1–D4). `close_run`
   only checks run status; a claim expired at `T0+900` can still be closed `done` at
   `T0+901` before the sweep runs. Conflicts with SPEC §7.3 lines 153–154 and undermines
   §7.6's active-claim requirement for run close (line 167).
2. **D11 accepts concurrent cycle creation** for `blocks`/`parent_child`: two connections
   each BFS-checking then inserting `A→B` and `B→A` can admit a cycle. SPEC §3.6 line 56
   requires rejection at write time, transitive check included.
3. **Sibling logic imports inside the pure logic package** (`ordering.py` imports
   `eligibility.is_eligible`; lint notes allow sibling imports). Ticket line 26 says pure
   logic imports stdlib + contracts only, matching SPEC §14 line 229.
4. **Test fences weaken "exact error shapes" to error-code assertions.** SPEC §18.3 line
   271 requires exact error shapes; the error contract is
   `{"error": {code, message, detail}}` (errors.py:39); planned tests assert only `.code`.

Also confirmed: no planned writes outside the owned files.

(Full raw output preserved by the orchestrator; findings above are complete — nothing
omitted.)

## Orchestrator dispositions

**Finding 1 — ACCEPTED.** The dispatch brief's binding invariant is "no code path lets an
expired claim keep writing", and `close_run` is a write path. Amendment A1: `close_run`
gains an active-claim guard (same `has_active_claim` predicate as the heartbeat guard,
D1/D3) — a close at or after `claim_expires` raises `stale_claim` and changes nothing;
the run's only exit is then the reclaim sweep. This is coherent with stage 4: `timed_out`
closes happen while the worker is heartbeating (claim active), `spawn_failed` closes
happen immediately after claim (claim fresh), and a worker that went silent past its TTL
is precisely the reclaim case. New fence test `test_a15_close_after_expiry_rejected`
added (item 15 is the TTL/reclaim item).

**Finding 2 — ACCEPTED (scoped fix).** In production a single server process serializes
link writes, but the fix is cheap and makes the write-time guarantee unconditional.
Amendment A2: `add_link` wraps belongs_to-check + cycle-BFS + INSERT + event in
`BEGIN IMMEDIATE … COMMIT` when not already inside a transaction (`conn.in_transaction`
guard, ROLLBACK on any raise). BEGIN IMMEDIATE takes the write lock before the reads, so
two connections cannot interleave check-then-insert; `busy_timeout` (set by
`core.db.connect`) handles contention. `remove_link` stays autocommit (no invariant to
protect).

**Finding 3 — ACCEPTED IN SUBSTANCE, one carve-out refuted.** The functional sibling
dependency is removed. Amendment A3: `eligible_ordered` is dropped entirely — the ticket
asks for eligibility as a pure function and ordering as a pure *key* function; the
two-line composition `sorted((c for c in cands if is_eligible(c)), key=ordering_key)`
belongs to callers (stage-4 tick, tests). After A3 no logic module imports a sibling
module. The carve-out: `logic/__init__.py` facade re-exports remain. Every existing logic
package in this repo (`sprints/logic/__init__.py`, `days/logic`, `seed/logic`) uses
exactly this pattern; the constraint's object is external dependencies (DB, FastAPI,
config), not a package's own facade. Refuting the strictest reading for `__init__` only.

**Finding 4 — ACCEPTED.** Amendment A4: every error-asserting test asserts (a) `ErrorCode`
identity on `exc.value.code`, and (b) the full envelope via `exc.value.to_payload()`:
top-level key `"error"` with exactly the keys `code`/`message`/`detail`, `code` equal to
the exact string (`"link_cycle"`, `"link_invalid"`, `"stale_claim"`, `"not_found"`), and
`detail` carrying the plan-specified keys (e.g. `from_id`/`to_id`/`kind` for link errors,
`run_id`/`run_status` for stale closes). Message *content* stays unpinned — SPEC fixes
the shape and codes, not prose.

Amendments A1–A4 are appended to plan.md as a binding section; the implementer follows
plan.md including amendments.
