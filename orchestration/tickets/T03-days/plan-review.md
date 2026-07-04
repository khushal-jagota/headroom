# T03 plan review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive), pointed at plan.md, ticket.md, SPEC §3.4/§6.1/§6.2/§6.3/§18.3 items 1/12/17/18, the days/core contracts, core infra, conftest, and scripts/verify_lib.py.

## Codex findings (verbatim summary)

1. plan.md §4 boundary failure path references undefined `timeout_s` in `_error_text(exc, timeout_s)` — would fail ruff F821 / mypy strict.
2. plan.md §1.7 uses `BoundaryJudgment` in `_judgment_with_timeout`'s return type but the import list only names `BoundaryAdapter, BoundaryInputs`.
3. plan.md §1.7 helpers `_read_overdue_candidates` / `_read_approval_candidates` typed `tuple[list, list]` — bare generics fail mypy strict.
4. plan.md §1.5 `approvals_digest` omits `needs_review` tickets. SPEC §4.5 (line 104) defines the approval queue as tickets with a pending gating-field proposal **plus sprint items with pending status proposals, plus `needs_review` tickets**; §6.2 step 3 (line 126) says the boundary computes "the approval-queue digest".
5. plan.md §5 `test_a18_boundary_job` covers only the day-ticket branch of the skip condition; SPEC §6.2 (line 131) skips on **any day-ticket OR accepted plan** — the accepted-plan branch is untested.

Codex explicit clean checks: planning-date arithmetic exact at 04:59/05:00 (no off-by-one); middle-element re-pack preserves relative order and contiguity; replan semantics (one `ReplanRoot` on root invalidation, one `ReplanChild(position)` on child invalidation, `old_tree` belongs to stage-4 `plan_replanned` — deferral defensible per ticket.md line 23); one-test-per-token packing matches `verify_lib.py::score()`.

## Orchestrator dispositions

1. **Accept.** Pseudocode bug. Amendment A1: bind `timeout_s = config.boundary_timeout_seconds` before the `try` and pass it to both `_judgment_with_timeout` and `_error_text`.
2. **Accept.** Amendment A2: add `BoundaryJudgment` to the `planner.core.adapters.base` import in boundary.py.
3. **Accept.** Amendment A3: fully parameterize — `tuple[list[dict[str, Any]], list[dict[str, Any]]]` for both helpers.
4. **Accept.** §4.5 defines what "approval queue" means and §6.2 step 3 references it; omitting `needs_review` tickets would be a weakened approximation. Amendment A4: `approvals_digest` additionally emits a row for every ticket with `state == needs_review`, `kind = "review"`, `waiting_since = row["updated_at"]` (DB-internal proxy for when it entered review; both proposal shapes carry `created_at`, tickets rows carry `updated_at` — the projection gains that column). Digest ordered oldest-pending first (ascending `waiting_since`) mirroring §4.5. Not asserted by a18 (item 18 fences only carryover/overdue), so no fence change.
5. **Accept.** The dispatch brief states the skip rule verbatim as "any day-ticket exists OR an accepted plan node exists"; testing only one branch under-asserts the fence. Amendment A5: a18 gains Part 4 (same single test function, per the one-test-per-token rule): fresh planning date 2026-07-07, pre-store a plan on `day_2026-07-07` with root `proposed` and one child `accepted` (no day-tickets), fresh recording adapter, `run_boundary`; assert adapter calls `== []` and `boundary_runs['2026-07-06'... wait — '2026-07-07']` judgment `== "skipped"`. (Also implicitly re-covers that a childless-day deterministic pass still closes yesterday.)

No structural re-plan needed: all five findings are point amendments; the architecture (pure logic / data / boundary split, effects vocabulary, guard-first control flow) stands as planned. Amendments appended to plan.md as the binding section.
