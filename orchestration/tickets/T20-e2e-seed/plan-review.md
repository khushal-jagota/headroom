# T20 plan review — codex exec output + orchestrator dispositions

Reviewed: orchestration/tickets/T20-e2e-seed/plan.md against ticket.md, SPEC.md §12 + §18.3
items 33–34, the e2e harness, the seed importer/views, the three screens, and the verify
scorer rules. Codex was asked for concrete violations in five categories.

## Codex output (verbatim findings section)

> **Finding**
>
> 1. **Ordering/determinism claim is partly false.**
>    The plan says seeded rows share "one frozen fake-clock tick" from `PLAN_FAKE_NOW`
>    (plan.md:459). Actual seed import uses wall-clock time: `now = int(time.time())` in
>    importer.py:37. The seed route passes only `conn, source_dir` (seed/api.py:55), and the
>    CLI sends only `source_dir` (main.py:184).
>    The nondeterministic ordering conclusion is still valid because one `now` is reused per
>    import and IDs are random, but the plan's fake-clock rationale is wrong.
>
> **Per Category**
>
> 1. Pinned ground-truth contradictions: none found.
> 2. Selector or harness mismatches: none found.
> 3. Fence violation risks: none found.
> 4. Ordering/determinism assumptions: one violation above.
> 5. Missing assertions for SPEC 18.3 items 33-34: none found.

## Dispositions

1. **ACCEPTED.** Verified on disk: `src/planner/seed/importer.py:37` computes
   `now = int(time.time())` once per run — wall clock, not the PLAN_FAKE_NOW clock. Every
   row imported in one run still shares that single `now`, and entity ids carry random
   suffixes, so the plan's *conclusion* (set + count assertions for /api/ideas, the sprint
   loose list, and within-priority deferred blocks; ordered assertions only where sort keys
   are distinct priorities) is unchanged and remains mandatory. Only the stated rationale
   was wrong. Amended as a binding correction appended to plan.md (§6); no test design
   change. Also checked the knock-on: created_at is never asserted anywhere in either test,
   so wall-clock stamping affects nothing else.

Categories 1, 2, 3, 5: clean — matches my own independent probe (importer run in-process
against both sources) and code reads (board_view emits every non-dropped column even when
empty; `sprint_id=null` maps to `sprint_id IS NULL`; boundary_hour default 5).

Verdict: plan approved with the one rationale correction; proceed to implementation.
