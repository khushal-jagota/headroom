# T05 implementation review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` over the 8 implemented files against ticket.md, plan.md (incl.
amendments A1–A5), SPEC §3.6/§7/§13/§14/§18.3, contracts, and infrastructure. Verdict
line: VIOLATIONS FOUND (2 findings, both P2), with all nine mandated checks answered
individually.

## Codex mandated-check conclusions (verbatim substance)

1. **CAS correctness — Pass.** Literal CAS at data.py:55, rowcount-loss at :58, runs
   insert at :61; test_a14 uses two real connections, asserts one winner + one runs row.
2. **TTL arithmetic — Pass.** `expiry_at(now, ttl)` exact (claims.py:9); expired is
   `<= now` (claims.py:20); used consistently by heartbeat, close, sweep, candidate
   assembly.
3. **Reclaim path — Pass.** Expired precedes dead PID (data.py:241); finalize via the
   `runs.status='running'` CAS (data.py:131); lock+expiry cleared (data.py:150).
4. **Expired-claim write paths — Pass.** heartbeat and close_run claim-gated via
   `has_active_claim`; sweep intentionally handles expired claims; claim and
   clear_auto_block are not agent claim-carrying writes.
5. **Eligibility/ordering — Pass.** needs_review excluded, at-ceiling+stop rejected
   (eligibility.py:41); ordering key matches §7.2 (ordering.py:20).
6. **Breaker — Pass.** Increment/reset/neutral in breaker.py:18; sticky flag and the
   0→1 event in data.py:141.
7. **Links — Pass** (implementation). BEGIN IMMEDIATE at links.py:61; cycle/link errors
   at :83/:105; blocked derivation at :137.
8. **Test fences — Partial fail:** exactly one anchored test per item, no skip/xfail,
   but A4 envelope coverage incomplete at four error sites.
9. **Scope/imports/params — Partial fail:** logic/data/links import + parameter rules
   all pass; worktree contains files modified outside the T05 owned list.

## Findings and dispositions

**Finding 1 (P2) — A4 envelopes missing at four error sites — ACCEPTED, FIXED.**
The transitive `blocks` cycle (test_a09), both `parent_child` cycle rejections, and the
duplicate-triple rejection asserted only the ErrorCode. Fixed by the orchestrator
directly (small test-only fix, no behavior change): `_assert_error(...)` added at all
four sites, pinning the full `to_payload()` envelope, the exact code string, and the
detail keys/values (`from_id`/`to_id`/`kind` for cycles; `from_id` for the duplicate,
whose belongs_to pre-check fires first). Rerun after the fix: 18 passed, ruff clean,
mypy clean (`Success: no issues found in 74 source files`).

**Finding 2 (P2) — files outside the owned list modified in the worktree — REFUTED.**
Codex flagged `PROGRESS.md`, `decisions.md`, `src/planner/days/data.py`,
`src/planner/tickets/data.py`, `tests/unit/test_days.py`, `tests/unit/test_tickets_engine.py`
as out-of-scope modifications, itself hedging "assuming this worktree is the T05
implementation diff". That assumption is wrong: this is a shared main worktree in which
sibling per-ticket orchestrators (T03 days, T04 tickets-engine, T06 sprints, T07 seed,
T08 instrument) work concurrently by design (top-level orchestrator's parallelisation
call), and PROGRESS.md/decisions.md are written by the top-level orchestrator. T05's
agents wrote exactly the 8 owned files and nothing else; the T05-attributable diff is:
`src/planner/dispatch/logic/` (5 files), `src/planner/core/links.py`,
`src/planner/dispatch/data.py`, `tests/unit/test_dispatch.py`.
