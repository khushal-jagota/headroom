# W2-removals — codex plan-review + orchestrator dispositions

Review run: `codex exec -m gpt-5.5 --sandbox read-only --config model_reasoning_effort="high"`,
prompt pointed at `plan.md`, `ticket.md`, the notes.md Removal & rework ledger + linked sections,
and the source/tests on disk. Categories requested: missed references, tests going red, schema/
migration hazards, W3 exclusion breaches, plan-vs-ticket delta, verify-instrument hazards.

---

## Codex output (verbatim)

**A. MISSED REFERENCES**

No findings.

**B. TESTS THAT WOULD GO RED**

No findings beyond tests already listed by the plan.

**C. SCHEMA / MIGRATION HAZARDS**

No findings. The plan's account matches `src/planner/core/db.py`: `SCHEMA_VERSION` is only
stamped via `PRAGMA user_version`; no code reads it to gate or migrate. Fresh DB fixtures call
`create_schema`.

**D. W3 EXCLUSION BREACHES**

No findings. The plan correctly leaves `runs`, claim columns/headers, dispatch code,
breaker/budget/timeout/concurrency config, and seed/ticket W3 columns intact. Verified seed
importer still inserts `auto_blocked` / `consecutive_failures`, and tickets data still owns
claim columns.

**E. PLAN vs TICKET DELTA**

1. `config.yaml` (line ~20) `title_max_chars` is not listed in the plan.
   Failure: W2 mandates removing the `title_max_chars` config knob; leaving the checked-in key
   behind leaves a stale config surface even after `Config.title_max_chars` and
   `PLAN_TITLE_MAX_CHARS` are removed.
   Fix: add `config.yaml` to the edit list and delete `title_max_chars: 200`.

2. `src/planner/sprints/logic/freeze.py` deletion is not mechanically forced by the decided
   column removals.
   Failure: not a verify failure, but plan scope overstatement. `freeze.py` is dead/pure scalar
   logic; deleting it is cleanup, not required by dropping `kickoff_frozen_at` /
   `review_frozen_at`.
   Fix: mark it explicitly as optional dead-code cleanup needing sign-off, or leave it.

3. Freeze route/event removal is not strictly forced; addenda removal is.
   Evidence: `weekly_addenda` is actively read/written by `sprints.data.add_addendum` and
   serialized by `sprints.views.sprint_json`, so addenda route/event removal is forced. Current
   `freeze_kickoff` / `freeze_review` are inert loaders and emit no events.
   Failure: deleting freeze routes/events is scope expansion beyond the column drop, though
   coherent.
   Fix: call it out as explicit collateral, separate from forced addenda removal.

4. Dogfood deletion classification needs precision.
   Evidence: `tests/e2e/test_dogfood.py` uses `plan seed --demo`; `scripts/dogfood_cli.py`
   calls the removed day-plan endpoint.
   Failure: the test cannot stay green as written. Deleting the script is not verify-forced if
   the test is deleted, but the script would remain a broken reference to removed machinery.
   Fix: keep the plan's deletion only with explicit "broken harness collateral" sign-off;
   otherwise delete the test and file a follow-up to rebuild dogfood.

**F. VERIFY-INSTRUMENT HAZARDS**

No findings. `scripts/verify.py` has no pinned test-name/count manifest; it gates ruff, mypy,
unit/e2e collection, skip-scan, `compileall`, `node --check`, and CSS brace syntax only.

**Verdict: FAIL** — needs a small plan amendment for the missed checked-in config key and
explicit scope sign-off for the freeze/dogfood collateral.

---

## Orchestrator dispositions (Fable sense-check)

**A / B / C / D / F — no findings: CONCUR.** Independently corroborated before the review
landed, by direct grep/read: every `boundary_runs` reference, every `days.logic.effects`
importer, every emission site of the nine removed `EventKind` members, every
`title_max_chars` code reference, and the `planTree` frontend dead-export are all inside code
or tests the plan already deletes or rewrites. `scripts/verify_lib.py` pins no test names or
counts.

**E1 (config.yaml key) — ACCEPT. Real gap.** Verified: `config.yaml` contains
`title_max_chars: 200`. It is the only checked-in config key tied to W2-removed machinery (all
`boundary_*` keys stay — the boundary remains functional; all dispatcher keys are W3).
→ Amendment 1: add `config.yaml` to the owned-files edit list; delete that one line.

**E2 (freeze.py not forced) — ACCEPT as classification, keep the deletion.** The plan itself
already said leaving it would not fail verify and flagged it in §14. Amendment 2 makes the
classification binding: `freeze.py` deletion (plus its `logic/__init__` re-exports) is
*collateral dead-code cleanup*, not a forced consequence of the column drop; it stays in the
plan as the recommended action, subject to integrator sign-off at greenlight.

**E3 (freeze routes vs addenda) — ACCEPT as classification, keep the removal.** Verified the
asymmetry: `add_addendum` UPDATEs `weekly_addenda` (removal forced by the column drop);
`freeze_kickoff`/`freeze_review` writers are inert no-ops that never touch the columns
(route/writer/event removal is collateral dead-surface cleanup — their only reason to exist
was latching the now-dropped columns). Note the affected test legs in
`test_a20_freeze_rules_and_overlap` assert the dropped `Sprint` fields, so they are edited
under either choice; keeping the routes buys nothing. Amendment 3 records the forced/collateral
split for the integrator.

**E4 (dogfood) — ACCEPT; already flagged in plan §14.1.** Verified both dependencies
(`seed --demo` in the test, `POST /api/day/{date}/plan/accept` in the script). Amendment 4
makes the recommendation binding pending integrator sign-off: delete
`tests/e2e/test_dogfood.py` + `scripts/dogfood_cli.py` as broken-harness collateral; a rebuilt
dogfood harness is a follow-up ticket, not W2.

**On the FAIL verdict:** the sole hard gap is E1 (one line in one file); E2–E4 are
classification/sign-off precision on removals the plan already mapped and flagged. That is an
amendment, not a re-plan — the removal map's structure, reference inventory, and test
inventory stand unchallenged (A/B/C/D/F clean). Post-amendment status: **implementable as
planned**. The freeze/dogfood collateral sign-off is surfaced to the integrator in the
orchestrator report.
