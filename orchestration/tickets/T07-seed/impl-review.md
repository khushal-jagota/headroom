# T07 implementation review — codex output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive), pointed at the implemented files
(src/planner/seed/logic/*.py, importer.py, demo.py, tests/unit/test_seed.py, all 11 fixture files),
ticket.md, the amended plan.md, SPEC §12/§18.3(19), the contracts, the DDL/events/ids/errors
infrastructure, and the real snapshot. Ten named areas checked (the dispatch-mandated set plus
idempotency, transaction safety, fields JSON, test-fence integrity, purity/scope).

## Codex verdicts

1. CLEAN — status + readiness mappings applied via the contract maps (ITEM_STATUS_MAP at
   tracking.py, READINESS_MAP at workspace.py), no re-declared literals.
2. CLEAN — title-match linking: exact equality, exactly-one-candidate, this run's tracking titles
   only; parented columns per §3.3; dedupe hits skip the link attempt.
3. CLEAN — R6 latest-daily selection and skip enumeration.
4. **VIOLATION** — two silent-drop edge paths:
   - [P1] blocks.py `parse_bullets`: a non-bullet prose line with no preceding bullet (empty stack)
     inside a *recognized* section was neither imported nor reported.
   - [P2] workspace.py `_field_value`: a recognized field bullet's own non-bullet continuation
     lines (`extra_lines`) were dropped from the field value.
5. CLEAN — idempotency keys, duplicate counting, no events on re-run, links only on new tickets.
6. CLEAN — single BEGIN IMMEDIATE / COMMIT transaction with ROLLBACK-and-reraise; no writes
   outside it.
7. CLEAN — demo dataset (guard over all tables raising db_not_empty, 8 tickets over all 7 states,
   valid ceilings never dropped, one blocks link, parent item with 2 children per §3.3, PlanTree
   JSON per days/contracts.py, contiguous day positions).
8. CLEAN — fields JSON: four keys exactly; Success:/Approach: → values; Body: + unrecognized
   sub-bullets → success.notes per binding amendment A1; plan/result all-null.
9. CLEAN — exactly one `test_a19_` function asserting every item-19 clause including exact
   skip-list equality and re-run zero duplicates; no snapshot reads; no forbidden patterns.
10. CONCERN — the worktree contains many untracked files outside T07's owned set.

## Orchestrator dispositions

**Finding 4 [P1] — ACCEPTED and FIXED.** `parse_bullets` now returns
`(bullets, orphan_lines)`; all four section parsers (tracking, workspace, deferred, ideas) emit one
`SkippedSection(source_file, heading, REASON_PROSE, excerpt)` per section when orphan prose exists.
New reason constant in blocks.py: `REASON_PROSE = "prose inside a bullet section; not an importable
item"`. Granularity note: one entry per section (all orphan lines of that section joined) rather
than strictly one per contiguous block — no fixture/snapshot data contains such lines at all, so
the pinned skip lists are unchanged; the guarantee delivered is the one that matters (nothing
silent). Regression test added: `test_orphan_prose_in_recognized_section_is_enumerated`.

**Finding 4 [P2] — ACCEPTED and FIXED.** `_field_value` now emits base value + children (via
`emit_body`, which already carries nested extras) + the field bullet's own `extra_lines`, verbatim
per the plan's tokenizer rule. Regression test added:
`test_workspace_field_continuation_lines_preserved` (continuation lines kept verbatim, indentation
included).

**Finding 10 — REFUTED (not a T07 defect).** The untracked sibling files are the concurrent
stage-3 wave (T03–T08) working disjoint domains in the same tree by design (dispatch brief:
"Other ticket-orchestrators work sibling domains concurrently in this tree"). The T07 change set is
exactly the ticket's owned files; codex itself confirmed seed/contracts.py and seed/api.py are
untouched. Integration is the top-level orchestrator's serial step.

All other areas CLEAN — no action.

## Post-fix gate results (fresh)

- `.venv/bin/ruff check src/planner/seed tests/unit/test_seed.py` → All checks passed!
- `.venv/bin/mypy src/` → Success: no issues found in 74 source files
- `.venv/bin/pytest tests/unit/test_seed.py -q` → 9 passed (7 pre-fix + 2 regression)
- Snapshot sanity re-run after fixes: identical to pre-fix — counts 1 / 12 (6 todo, 5 active,
  1 done "Ship waitlist mechanics.") / 4 tickets / 9 deferred / 20 ideas / 0 links, 7 skips,
  re-run 0 new entities, duplicates_skipped 46, events unchanged at 46.
