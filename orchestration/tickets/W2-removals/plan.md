# W2 — Pure deletions: removal map (plan)

Mechanical removal map for W2. This is a deletion/reference-repair plan, not a design.
It references **symbols and functions, never line numbers**, so it survives the in-flight
W1 changes landing first (W1 touches `days/api.py`, `sprints/api.py`, `tickets/api.py`,
`dispatch/api.py`, and adds `sprints/views.py`/`tickets/views.py`). Implementation is
codex xhigh, write-enabled, after W1 lands.

**Scope guardrails (W3 — do NOT touch).** `runs` table; `claim_lock`/`claim_expires`;
`auto_blocked`/`consecutive_failures`; `alias` column; `X-Plan-Run-Id`/`X-Plan-Claim`;
the `run` CLI group; all of `src/planner/dispatch/*`; the breaker; budget/concurrency/
timeout config; the dispatcher lock. None of these are altered here. Where a W2 deletion
brushes against them, it is flagged in §12, not changed.

**Verify has NO pinned test manifest.** `scripts/verify.py` + `scripts/verify_lib.py`
run whole suites (`tests/unit`, `tests/e2e`) and gate on: ruff, mypy, the skip-scan
(no `@pytest.mark.skip`/`pytest.skip(`/`xfail`/`.only`/commented-out `def test_`/empty
test bodies), a build check (`compileall` + `node --check` per `.js` + a CSS brace check),
and pass/exit. `rc==5` (no tests collected) fails a suite. **Nothing names tests or pins
counts.** Consequences for this ticket: (a) deleting whole test functions is free — no
manifest to update; (b) a suite dir must still collect ≥1 test (both keep several); (c)
do not leave a test file with an empty body, a `# def test_...` comment, or a stub — the
skip-scan fails on those; (d) SCHEMA_VERSION/user_version is informational (see §8).

---

## 1. Confirmed owned-files set (from evidence, superseding the ticket's "likely" list)

**Delete whole files:**
- `src/planner/days/logic/tree.py`
- `src/planner/days/logic/effects.py`
- `src/planner/seed/api.py`
- `src/planner/seed/demo.py`
- `src/planner/sprints/logic/freeze.py` (recommended — see §6 flag)
- `tests/e2e/test_seed_e2e.py`
- `tests/e2e/test_dogfood.py` (collateral — see §14 flag)
- `scripts/dogfood_cli.py` (collateral — see §14 flag)

**New file:**
- `src/planner/seed/__main__.py` (standalone cutover entrypoint — §5)

**Edit:**
- `src/planner/core/db.py` (schema + SCHEMA_VERSION)
- `src/planner/core/config.py` (`title_max_chars`)
- `config.yaml` (drop the `title_max_chars: 200` line — amendment A1)
- `src/planner/core/server.py` (title assert; `seed_router` import + registration)
- `src/planner/core/contracts.py` (EventKind members; `ErrorCode.db_not_empty`)
- `src/planner/core/adapters/base.py`, `real.py`, `fakes.py` (replan methods + plan-tree imports)
- `src/planner/days/api.py` (plan routes + `_day_view` plan key + imports)
- `src/planner/days/boundary.py` (boundary_runs guard; `_human_planned`; return outcome)
- `src/planner/days/scheduler.py` (replan queue removed; `run_boundary_tick` report)
- `src/planner/days/data.py` (plan-storage fns; `days.plan` in materialize/read; imports)
- `src/planner/days/contracts.py` (PlanTree family; `Day.plan`; `PlanNodeBody`)
- `src/planner/seed/importer.py` (drop `current_state_note` from the item INSERT — §6)
- `src/planner/cli/main.py` (remove the `seed` verb + `_seed_human`)
- `src/planner/sprints/contracts.py` (Addendum, AddendumBody, Sprint/SprintItem fields)
- `src/planner/sprints/data.py` (freeze/addenda writers; INSERTs; row builders; field sets)
- `src/planner/sprints/api.py` (freeze/addenda routes; `current_state_note` marshalling)
- `src/planner/sprints/views.py` (`sprint_json`, `item_json`)
- `src/planner/sprints/logic/__init__.py` (drop freeze exports)
- `src/planner/tickets/contracts.py` (add `TITLE_MAX_CHARS` constant — §5)
- `src/planner/tickets/api.py` (source the title cap from the constant — §5)
- `assets/components.js` + `assets/app.css` (dead plan-tree render — §10)
- Tests: `tests/unit/test_days.py`, `test_runtimes.py`, `test_seed.py`, `test_chat_seed.py`,
  `test_sprints.py`, `test_authctx_routes.py`, `test_tickets_engine.py`,
  `test_value_edit_logic.py`; `tests/e2e/test_flows_b.py` (§11)

**Explicitly NO change:** `src/planner/days/logic/carryover.py` (§2 — kept), `seed/contracts.py`,
`seed/logic/*`, `seed/importer.py` parser flow (only the one INSERT edited), `tickets/data.py`
signature, `tickets/logic/admission.py`, `assets/screens-day.js` (already dropped plan-tree).

---

## 2. Item 1 — Plan-tree / day-plan / replan subsystem (+ `days.plan`)

### tree.py / effects.py / carryover.py boundary call (the ticket's required finding)

- **`days/logic/tree.py` — DELETE.** Pure plan-tree transforms (`tree_to_dict`,
  `tree_from_dict`, `as_proposed`, `accept_node`, `accept_all`, `invalidate_root`,
  `invalidate_child`, `reject_all`). Imported only by: `days/api.py` (as `plan_tree`),
  `days/data.py` (`tree_from_dict`, `tree_to_dict`), `days/scheduler.py`
  (`as_proposed`, `tree_to_dict`), `core/adapters/real.py` (`tree_from_dict`). Every
  importer is either deleted or has its plan code removed here. → dies.
- **`days/logic/effects.py` — DELETE. Verified plan-tree-only.** It defines exactly the
  plan effect vocabulary (`AddTicketToDay`, `EmitEvent`, `ReplanRoot`, `ReplanChild`,
  `Effect`, `ReplanRequest`). Importers: `tree.py` (deleted), `days/data.py`
  (`apply_plan_effects` only), `days/scheduler.py` (replan queue only). **No non-plan-tree
  consumer exists** — nothing outside the plan/replan flow references any effect type.
  Finding recorded: effects.py is plan-tree-only → safe to delete.
- **`days/logic/carryover.py` — KEEP (separate concern; do not touch).** It is the
  boundary's deterministic-pass logic (`carryover_candidates`, `day_ticket_counts`,
  `overdue_list`, `approvals_digest`) producing `BoundaryInputs` digest lists. **Zero
  plan-tree involvement.** Imported by `days/boundary.py` (kept, stays functional) and
  `days/scheduler.py` (that scheduler import goes with the replan machinery, but
  boundary.py keeps it). It has no plan types, no effects, no tree. → stays untouched.

### Backend deletions (item 1)

- **`days/api.py`** — remove routes `plan_accept`, `plan_accept_all`, `plan_invalidate`,
  `plan_reject_all` (all `POST /day/{date}/plan/*`); helpers `_marshal_plan_node`,
  `_parse_node`, `_load_plan_or_error`, `_require_child`; imports of `plan_tree`,
  `PlanNodeBody`, `PlanTree`, `submit_replan`, and the `days_data.load_plan`/
  `apply_plan_effects` uses. In `_day_view`, drop the `"plan": ...` key. **Keep**
  `get_day`, `patch_day`, `add_day_ticket`, `remove_day_ticket`, `resolve_day_id`.
- **`days/data.py`** — delete `load_plan`, `store_plan`, `apply_plan_effects`; drop the
  imports from `days.logic.effects` and `days.logic.tree`; in `materialize_day` drop the
  `plan` column + `NULL` from the INSERT; in `read_day` drop `plan` from the SELECT and
  the `plan=tree_from_dict(...)` build. **Keep** `materialize_day`, `read_day`,
  `list_day_tickets`, `store_judgment`, `add_day_ticket`, `remove_day_ticket`,
  `set_day_field`, `DAY_TEXT_FIELDS`.
- **`days/contracts.py`** — delete `NodeStatus`, `PlanRoot`, `PlanNode`, `PlanTree`,
  `PlanNodeBody`; drop the `plan: PlanTree | None` field from `Day`. **Keep** `Day` (minus
  plan), `DayTicket`, `DayPatchBody`, `AddDayTicketBody`, `PlanningDateFn`.
- **`days/scheduler.py`** — delete the entire replan subsystem: `_PendingReplan`,
  `_ReplanQueue`, `_QUEUE`, `submit_replan`, `reset_replan_queue`, `_consume_if_current`,
  `_replan_inputs`, `_call_with_timeout`, `_execute_pending`, `process_pending_replan`,
  and all imports of `NodeStatus`/`PlanNode`/`PlanTree`, `load_plan`/`store_plan`,
  `days.logic.effects`, `days.logic.tree`, and the `carryover`/boundary reader imports
  that only `_replan_inputs` used. **Keep** `run_boundary_tick` (reworked — see §4).
- **`core/adapters/base.py`** — remove `replan_root` and `replan_child` from the
  `BoundaryAdapter` protocol; drop `from planner.days.contracts import PlanNode, PlanTree`.
  **Keep** `judgment`, `BoundaryInputs`, `BoundaryJudgment`.
- **`core/adapters/real.py`** — remove `RealBoundaryAdapter.replan_root`/`replan_child`;
  drop imports `NodeStatus`, `PlanNode`, `PlanTree`, and `from ...logic.tree import
  tree_from_dict`. **Keep** `RealBoundaryAdapter.judgment`, `_invoke`, `_parse_json_object`.
- **`core/adapters/fakes.py`** — remove `FakeBoundaryAdapter.replan_root`/`replan_child`
  and its `replan_tree`/`replan_child_node` fields; drop `from planner.days.contracts
  import NodeStatus, PlanNode, PlanRoot, PlanTree`. **Keep** `judgment`, `judgment_result`,
  `fail`, `calls`.
- **`core/contracts.py` (`EventKind`)** — remove the six plan events: `plan_proposed`
  (already emitted nowhere — dead), `plan_node_accepted`, `plan_accepted_all`,
  `plan_node_invalidated`, `plan_replanned`, `plan_rejected`. (Freeze/addenda events → §6.)
  **Keep** `boundary_failed`, `day_created`, `day_updated`, `day_closed`,
  `day_ticket_added`, `day_ticket_removed`.

### `days.plan` column → §8 (schema).

---

## 3. Item 2 — `boundary_runs` table + dedup guard (boundary.py otherwise stays functional)

**Where the guard lives now (evidence):** `days/boundary.py` defines `_boundary_ran(conn,
pd_iso)` = `SELECT 1 FROM boundary_runs WHERE planning_date = ?` and `_record_boundary(...)`
= `INSERT INTO boundary_runs (...)` with judgment `ok`/`skipped`/`failed`. `run_boundary`
returns early on `if _boundary_ran(...)`. `days/scheduler.py::run_boundary_tick` also reads
`SELECT judgment FROM boundary_runs WHERE planning_date=?` and pre-checks `_boundary_ran`.

**Replacement (as DECIDED in notes.md "Days & rollover"): a direct materialization check.**
- Add `_next_day_materialized(conn, new_day_id)` = `SELECT 1 FROM days WHERE id = ?`
  (`fetchone() is not None`). Put it in `days/boundary.py`, replacing `_boundary_ran`.
- `run_boundary`: change the top guard from `if _boundary_ran(conn, piso): return` to
  `if _next_day_materialized(conn, ndid): return`. The first tick after the boundary hour
  finds `ndid` absent → runs the deterministic pass (which materializes `ndid` as its first
  step) → does work. Every later tick finds `ndid` present → returns. Once-per-date
  idempotence is preserved without a side table.
- Delete `_record_boundary` and all three `_record_boundary(...)` calls (the `ok`/`skipped`/
  `failed` outcomes are already observable via events: `day_updated{cause:"boundary"}` on
  success, `boundary_failed` on failure, and the plain early-return on skip).
- **Judgment/report continuity — have `run_boundary` return its outcome** so the report can
  keep `judgment` (only `boundary_runs` and `replan` are on the removal list; the report's
  `judgment` value is not). Change `run_boundary` to return `str | None`:
  `None` when guarded-skip (next day already materialized), `"skipped"` when human-planned,
  `"failed"` on judgment failure/timeout, `"ok"` on success. `store_judgment`/`day_updated`
  emission is unchanged.
- **`run_boundary_tick`** (scheduler): drop the `_boundary_ran` pre-check, the
  `SELECT judgment FROM boundary_runs` read, and the `process_pending_replan` call. New body:
  acquire the tick lock, snapshot `piso`, open conn, `outcome = run_boundary(conn, clock,
  config, adapters.boundary)`, close conn, return `{"planning_date": piso, "ran": outcome
  is not None, "judgment": outcome}`. **The `"replan"` key is removed** (queue gone).
- **`_human_planned`** in boundary.py: today it checks a day-ticket OR an accepted plan
  (`load_plan`, `NodeStatus.accepted`). With plans gone, simplify to the day-ticket check
  only (`SELECT 1 FROM day_tickets WHERE day_id=? LIMIT 1`); drop the `load_plan`/`NodeStatus`
  branch and imports. Behavior kept: a human-placed day-ticket still skips judgment.
- **Tick lock:** the `_TICK_MUTEX` existed mainly to serialize the boundary_runs
  read-guard-then-insert and single-flight the replan consumer. Both reasons are gone.
  Recommend retaining a single simple module lock around `run_boundary_tick` (the prod
  boundary loop is its one live caller) to keep the materialize check TOCTOU-safe; it may
  otherwise be dropped. Implementer's call; note in decisions.md.

`days/boundary.py` otherwise stays fully functional: `materialize_day`, the deterministic
pass (carryover/overdue/approvals/day_closed via `carryover.py`), the judgment call under
its caller-owned timeout, `store_judgment`, and the `day_updated`/`boundary_failed` events
all remain. `boundary_runs` table → §8.

---

## 4. Item 3 — Seed importer permanent surface (keep importer as a one-shot script)

**Remove:**
- **`src/planner/seed/api.py` — DELETE** (the `POST /api/seed` route, `_demo_report`).
- **`src/planner/seed/demo.py` — DELETE** (`seed_demo`, the `--demo` dataset, the only
  raiser of `ErrorCode.db_not_empty`; also the only other `boundary_runs`/`days.plan`
  writer besides the boundary — moot once deleted).
- **`core/server.py`** — remove `from planner.seed.api import router as seed_router` and
  drop `seed_router` from the `for domain_router in (...)` include tuple.
- **`cli/main.py`** — remove the `seed` command (the whole `@main.command("seed")` +
  `def seed(...)`) and the `_seed_human` helper it uses. Nothing else in the CLI references
  either. **Keep** every other verb (including `serve`).
- **`core/contracts.py`** — remove `ErrorCode.db_not_empty` (the "dedicated seed error
  code"; used only by the deleted `seed_demo` + its tests).

**Keep (retained importer + parsers):** `src/planner/seed/importer.py`,
`src/planner/seed/contracts.py`, all of `src/planner/seed/logic/*`. The only edit to
`importer.py`: in `_import_items`, drop `current_state_note` from the INSERT column list
and drop the `""` bound value (see §6 — the column is being removed). `contracts.py`
(`MigrationReport`, `Parsed*`, `SkippedSection`, mapping tables) needs no change — all are
consumed by the retained importer.

**Standalone entrypoint (must not depend on the removed route/CLI):**
Create `src/planner/seed/__main__.py`, invoked as
`python -m planner.seed --source <dir> [--db-path <path>] [--json]`. Shape:
1. Parse args (argparse or click — a tiny self-contained parser; do **not** import
   `planner.cli.main`).
2. Resolve config: `load_config()` for the default `db_path`, overridden by `--db-path`.
3. `conn = connect(db_path, ...)`; `create_schema(conn)` (idempotent — ensures the target
   DB exists at the new SCHEMA_VERSION).
4. `now = build_clock(config).now_unix()`.
5. `report = seed_from_source(conn, source, now)`; `conn.close()`.
6. Print the report (inline formatter — port the few lines of the removed `_seed_human`,
   or emit `asdict(report)` under `--json`).
Imports: only `planner.core.config`, `planner.core.db`, `planner.core.clock`,
`planner.seed.importer`. **Smoke (acceptance):** `python -m planner.seed --source
migration/source-snapshot` against a fresh DB path (the snapshot is present in-repo). A
`scripts/seed_import.py` wrapper is an equivalent alternative; `__main__.py` is preferred
for package-locality and because `scripts/` is not a package.

**Seed tests — survive vs go:**
- `tests/unit/test_seed.py` — **KEEP** the importer/parser tests:
  `test_a19_seed_fixture_import_counts_mappings_idempotency_and_skip_list` (drives
  `seed_from_source`; note: it selects sprint-item columns but not `current_state_note`, so
  it stays green after §6), `test_match_item_title_ambiguity`,
  `test_resolve_priority_fallback_chain`, `test_pick_latest_daily_selection`,
  `test_workspace_ticket_missing_readiness_is_enumerated`,
  `test_orphan_prose_in_recognized_section_is_enumerated`,
  `test_workspace_field_continuation_lines_preserved`.
  **REMOVE** `test_seed_demo_requires_empty_db` and `test_seed_demo_dataset_shape` (both use
  `seed_demo`; the latter also reads `SELECT id, plan FROM days` / `json.loads(day["plan"])`).
  Then drop the now-unused imports (`seed_demo`; and `pytest`/`ErrorCode`/`PlannerError`/
  `date`/`_DEMO_TODAY` if unused after — ruff will confirm).
- `tests/unit/test_chat_seed.py` — this file mixes chat + seed-route tests. **REMOVE** the
  seed-route tests: `test_seed_demo_counts`, `test_seed_demo_twice_is_db_not_empty`,
  `test_seed_source_dir_imports_fixture`, `test_seed_missing_dir_is_validation`,
  `test_seed_file_not_dir_is_validation`, `test_seed_neither_key_is_validation`,
  `test_seed_both_keys_is_validation`. **KEEP** all `test_chat_*`. Drop the `FIXTURE`
  constant if unused after.
- `tests/e2e/test_seed_e2e.py` — **DELETE the whole file** (`test_e33_seed_fixture_ui`,
  `test_e34_snapshot_migration` both drive `cli(server, "seed", "--source", ...)`). The
  retained importer keeps unit coverage via `test_a19`; the snapshot-migration e2e coverage
  is intentionally dropped and replaced by the one-time standalone-script smoke.
  Note: `tests/e2e/conftest.py::cli` remains used by `test_flows_a/b`, so leave the fixture.

---

## 5. Item 5 — `title_max_chars` config-vs-DDL-vs-startup-assert triple

**The three legs (evidence):**
1. **Config knob:** `core/config.py` `Config.title_max_chars` field + the
   `title_max_chars=_int_value(cfg, env, "title_max_chars", "PLAN_TITLE_MAX_CHARS", 200)`
   line in `load_config`.
2. **DDL literal:** `core/db.py` `tickets.title ... CHECK (length(title) <= 200)`.
3. **Startup assert:** `core/server.py` `assert config.title_max_chars == 200,
   "title_max_chars must equal the DDL literal (200)"`.

**Removal (collapse the fake tunability; keep the cap enforced in one place + DB backstop):**
- Delete leg 1 (the `Config` field, the `load_config` line, the `PLAN_TITLE_MAX_CHARS` env).
- Delete leg 3 (the `core/server.py` assert block).
- **Keep leg 2** (the DDL `CHECK (length(title) <= 200)`) as the DB-level backstop — this is
  the single remaining literal, no longer a redundant "triple".
- Introduce `TITLE_MAX_CHARS: Final = 200` in `tickets/contracts.py` (next to the `title`
  field doc). Repoint the enforcement source from config to this constant: in
  `tickets/api.py`, replace `cfg.title_max_chars` (in the `create_ticket` route and the
  `_set_title` call) with `TITLE_MAX_CHARS`. Leave `tickets/data.py::create_ticket(...,
  title_max_chars: int)` and `tickets/logic/admission.py::validate_title(title,
  title_max_chars)` signatures unchanged (least churn) — callers now pass the constant.
  `ErrorCode.title_too_long` **stays** (the cap still fires).
- Tests that read the (now-removed) config value must switch to the constant/literal:
  `tests/unit/test_tickets_engine.py` (`title_max_chars=cfg.title_max_chars`),
  `tests/unit/test_value_edit_logic.py` (`title_max_chars=cfg.title_max_chars`, two sites)
  → use `TITLE_MAX_CHARS` or literal `200`. Tests already passing literal `200`
  (`test_authctx_routes`, `test_chat_seed`, `test_value_edit_api`) are unaffected.
- **Out of scope / leave:** `seed/logic/workspace.py::_TITLE_MAX = 200` and
  `seed/logic/blocks.py::REASON_TITLE_LONG` — these are the importer's own independent title
  guard, not the config triple.

**W1 overlap:** `tickets/api.py` is in the W1 uncommitted set → sequence this edit after W1.

---

## 6. Item 4 — Dormant columns + their reader/writer/serializer references

Columns dropped from the schema (§8): `days.plan` (item 1), `sprints.weekly_addenda`,
`sprints.kickoff_frozen_at`, `sprints.review_frozen_at`, `sprint_items.current_state_note`.

**`current_state_note` (sprint_items) — direct, low-risk:**
- `sprints/contracts.py`: drop the `current_state_note` field from `SprintItem`.
- `sprints/data.py`: `_row_to_item` drop `current_state_note=row["current_state_note"]`;
  `create_item` drop the `current_state_note` param, the INSERT column, and its bound value;
  `_ITEM_PLAIN_FIELDS` drop `"current_state_note"`.
- `sprints/api.py`: `_ITEM_PLAIN_FIELDS` drop `"current_state_note"`;
  `_marshal_create_item` drop `current_state_note=body_str(...)`; the `create_item(...)` call
  drop the `current_state_note=body["current_state_note"]` kwarg. (`CreateItemBody` TypedDict
  in contracts: drop the `current_state_note` key.)
- `sprints/views.py`: `item_json` drop `"current_state_note": item.current_state_note`.
- `seed/importer.py`: `_import_items` INSERT drop the `current_state_note` column + `""`.

**`weekly_addenda` (sprints) — forces removing the addenda feature (see §14 flag):**
The column cannot be dropped while `add_addendum` still does `UPDATE sprints SET
weekly_addenda = ?`. Everything that reads/writes it goes:
- `sprints/contracts.py`: drop `Sprint.weekly_addenda`; delete the `Addendum` dataclass
  (used only for this column) and the `AddendumBody` TypedDict (used only by the route).
- `sprints/data.py`: `_row_to_sprint` drop the `weekly_addenda=[Addendum(**a) ...]` read;
  `create_sprint` drop the `weekly_addenda` INSERT column + its `'[]'` literal; delete the
  `add_addendum` writer; drop the `Addendum` import.
- `sprints/api.py`: delete the `POST /sprints/{id}/addenda` route (`add_addendum`); drop the
  `AddendumBody` import.
- `sprints/views.py`: `sprint_json` drop `"weekly_addenda": [...]`.
- `core/contracts.py`: remove `EventKind.addendum_added` (emitted only by `add_addendum`).

**`kickoff_frozen_at` / `review_frozen_at` (sprints) — forces removing the vestigial freeze
surface (see §14 flag):**
- `sprints/contracts.py`: drop `Sprint.kickoff_frozen_at` / `Sprint.review_frozen_at`.
  **Keep** `KICKOFF_FIELDS` / `REVIEW_FIELDS` tuples — they are reused by `_SPRINT_TEXT_FIELDS`
  (data.py + api.py) to define the always-editable sprint text fields; only their
  "freeze group" role goes. Update the stale "Freeze groups" comment.
- `sprints/data.py`: `_row_to_sprint` drop the two `..._frozen_at=row[...]` reads;
  `create_sprint` drop the two columns + their `NULL` literals; delete the `freeze_kickoff`
  and `freeze_review` writers (already inert no-ops that only `_load_sprint` and return).
- `sprints/api.py`: delete the `POST /sprints/{id}/freeze-kickoff` and
  `POST /sprints/{id}/freeze-review` routes; delete the dormant-comment block above them.
- `sprints/views.py`: `sprint_json` drop `"kickoff_frozen_at"` and `"review_frozen_at"`.
- `core/contracts.py`: remove `EventKind.kickoff_frozen` / `EventKind.review_frozen`
  (emitted nowhere — dead).
- `sprints/logic/freeze.py`: **DELETE** (recommended). `frozen_group` /
  `field_write_admissible` have **no callers in `src/`** (verified: `update_sprint_field`
  explicitly runs "No admissibility gate"; the only references are the module's own
  definition and the `logic/__init__` re-export). Remove them from
  `sprints/logic/__init__.py`'s imports + `__all__`. (They take `int|None` params, not DB
  columns, so strictly they are dead code rather than a column reference — leaving the file
  would not fail verify, but it is orphaned; recommend deletion for tidiness. Flagged in §14.)

**Sprint dormant-column tests:**
- `tests/unit/test_sprints.py::test_a20_freeze_rules_and_overlap` — **surgically edit, do
  NOT delete whole.** Legs 1–3 (freeze_kickoff / weekly_addenda / freeze_review) are removed;
  **Leg 4 (sprint-overlap rejection) is core behavior and must be kept** — and it depends on
  the `sp = create_sprint(...)` created in Leg 1, so retain that `create_sprint` setup line
  as Leg 4's fixture. Optionally rename the function to `test_a20_sprint_overlap`. Drop the
  now-unused imports (`add_addendum`, `freeze_kickoff`, `freeze_review`, `update_sprint_field`,
  `Addendum`); keep `read_sprint` (Leg 4 uses it) and the item-transition imports.
- `tests/unit/test_authctx_routes.py::test_addendum_agent_is_forbidden_human_succeeds` —
  **REMOVE** (addenda route gone). Check whether its `_sprint`/`_col` helpers are used by any
  remaining test in the file; drop them only if now-unused.

**W1 overlap:** `sprints/api.py` and `sprints/views.py` are in the W1 uncommitted set →
sequence all sprint edits after W1 lands.

---

## 7. Event-type & error-code registry summary

`core/contracts.py` is the only event/error registry. Remove exactly these
`EventKind` members: `plan_proposed`, `plan_node_accepted`, `plan_accepted_all`,
`plan_node_invalidated`, `plan_replanned`, `plan_rejected`, `kickoff_frozen`,
`review_frozen`, `addendum_added`. Remove `ErrorCode.db_not_empty`. Removing StrEnum
members is safe: every remaining reference is inside code being deleted here (verified via
grep — no `assets/` reference to any of these kind strings; the event feed is a blind
invalidation signal). **Do not remove** any `run_*`/`claim_*`/`auto_block*` event or the
`day_*`/`boundary_failed` events (kept), and keep `title_too_long`.

---

## 8. Schema change (`core/db.py`) + migration approach

**DDL edits:**
- `sprints`: drop `weekly_addenda`, `kickoff_frozen_at`, `review_frozen_at` columns.
- `sprint_items`: drop `current_state_note` column.
- `days`: drop the `plan` column.
- Drop the entire `boundary_runs` table (`CREATE TABLE ... boundary_runs (...)`).
- **Leave untouched:** the `runs` table, `tickets.claim_lock`/`claim_expires`/`alias`/
  `auto_blocked`/`consecutive_failures`, and `tickets.title ... CHECK (length(title) <= 200)`
  (kept — §5).
- Bump `SCHEMA_VERSION` `1 → 2`.

**Migration approach (how the repo actually versions schema):** there is **no migration
runner and no ALTER path.** `create_schema(conn)` runs `conn.executescript(DDL)` (all
`CREATE TABLE IF NOT EXISTS`) then `PRAGMA user_version = SCHEMA_VERSION`. Callers:
`cli/main.py::serve` (server boot), `seed/__main__.py` (new), and every test conftest/
setup. **Nothing reads `user_version` to gate or migrate** (verified) — `SCHEMA_VERSION` is
an informational stamp. Therefore:
- **Fresh build only.** A brand-new DB is created at v2 with the new schema. Tests build a
  fresh DB per run (unit conftest, e2e server fixture), so they get v2 automatically.
- **An existing v1 DB is NOT auto-migrated:** `CREATE TABLE IF NOT EXISTS` will not alter an
  existing table, so the dropped columns would linger and `boundary_runs` would remain. The
  dev/test DB lives under `data/` (gitignored) and is simply **discarded and rebuilt** at
  v2. Production cutover creates a fresh DB via the standalone seed script (§4). State this
  in DOCS.md/decisions.md at integration time. (If a real in-place migration is ever wanted,
  that is a separate concern — not this ticket.)

---

## 9. Item 1 report/consumer wiring (loops, testmode)

- `core/loops.py` — **no change.** It runs `run_boundary_tick` as a background tick and
  discards the returned dict; the reworked signature/return is compatible.
- `core/testmode.py` — **no change to code.** `POST /api/test/tick-boundary` returns
  `run_boundary_tick`'s report verbatim; the new `{planning_date, ran, judgment}` shape flows
  through. (The e2e assertions on that shape are updated in §11.)

---

## 10. Frontend references (`assets/`, no-build vanilla JS)

- `assets/screens-day.js` — **no change.** The Day screen already dropped the plan-tree
  ("their backend endpoints stay; they just have no Day home"); it never calls `planTree`.
- `assets/components.js` — the `planTree(plan, handlers)` function and its
  `components` export entry (`planTree: planTree,`) are **dead**: exported but called by no
  screen (verified — no `c.planTree(`/`planTree(` call site). Remove the function body and
  the export line. Also remove the `bindMutating(invalidate, ...)`/`data-invalidate`/
  `data-accept-all`/`data-reject-all` wiring that lives only inside `planTree`.
- `assets/app.css` — remove the dead `.plan-tree`, `.plan-tree-actions`, `.plan-node*`
  (root/child/status/note/ticket/actions) rule block.
- **Do NOT touch** the ticket-field "plan" references: `components.js` `needs_plan: "plan"`
  and `screens-ticket.js` `FIELD_NAMES = ["success","approach","plan","result"]` / `plan:
  "needs_plan"`. These are the ticket's success/approach/**plan**/result field, unrelated to
  the day-plan-tree.
- Not verify-blocking (`node --check` and the CSS brace check pass regardless), but in scope
  as "frontend references to removed machinery" — remove for correctness/cleanliness.

---

## 11. Test inventory (by file + function)

**tests/unit/test_days.py**
- `test_a01_planning_date` — keep (unaffected).
- `test_a12_day_ticket_removal` — keep (unaffected).
- `test_a17_plan_tree` — **REMOVE** (entirely plan-tree).
- `test_a18_boundary_job` — **REWRITE:** remove Part 4 (accepted-plan-skips-judgment — that
  path dies with plans) and the `store_plan`/`accepted_tree` it uses; remove the
  `plan_proposed`-absence assertion (the event kind no longer exists); replace all
  `SELECT ... FROM boundary_runs` reads (Parts 1–3) — assert the `run_boundary` **return
  value** instead (`== "ok"`, `== "skipped"`); Part 2's "second tick is a no-op" now holds
  via the materialize guard (assert event-count invariance + `run_boundary(...) is None` on
  the second call); Part 3 (human day-ticket skips judgment) keeps its structure.
- Remove now-dead imports: `PlanNode`, `PlanRoot`, `PlanTree`, `NodeStatus`, `store_plan`,
  `load_plan`, `apply_plan_effects`, `days.logic.effects`, `days.logic.tree`; and delete
  `RecordingBoundaryAdapter.replan_root`/`replan_child` (the fake's replan methods).

**tests/unit/test_runtimes.py**
- `test_boundary_tick_runs_once_per_planning_date` — **REWRITE:** drop the `load_plan(...)
  is None` and `boundary_runs` assertions; update the two report `==` assertions to the new
  shape `{"planning_date": ..., "ran": True/False, "judgment": "ok"}` (no `"replan"` key).
- `test_replan_latest_wins_discards_stale_result` — **REMOVE**.
- `test_replan_child_splices_only_target_node` — **REMOVE**.
- `test_replan_failure_emits_boundary_failed_and_consumes` — **REMOVE**.
- Remove the `reset_replan_queue()` fixture calls, the `ResubmittingBoundary` helper class,
  and imports: `load_plan`, `store_plan`, `PlanTree`/`PlanNode`/`PlanRoot`/`NodeStatus`,
  `ReplanChild`/`ReplanRoot`, `tree_to_dict`, `submit_replan`, `process_pending_replan`,
  `reset_replan_queue`. (Dispatcher/`runs` tests in this file are W3 — leave.)

**tests/unit/test_seed.py / test_chat_seed.py / test_seed_e2e.py** — see §4.
**tests/unit/test_sprints.py / test_authctx_routes.py** — see §6.
**tests/unit/test_tickets_engine.py / test_value_edit_logic.py** — see §5.

**tests/e2e/test_flows_b.py**
- `_tick_boundary` callers `test_e28_day_boundary_accept_all` and
  `test_e31_refresh_restores_state` each assert `rep == {"planning_date": DAY_CUR, "ran":
  True, "judgment": "ok", "replan": None}` — **update both** to drop the `"replan": None`
  key: `{"planning_date": DAY_CUR, "ran": True, "judgment": "ok"}`. `test_e28` already
  asserts `.plan-tree is None` (stays true). No other change to these tests.

**tests/e2e/test_dogfood.py + scripts/dogfood_cli.py** — see §14 (collateral, flagged).

---

## 12. W3 pressure flags (flag only — no change here)

- The `runs` table, `tickets.claim_lock`/`claim_expires`/`auto_blocked`/
  `consecutive_failures`/`alias`, the `run` CLI group, `X-Plan-*` headers, and all of
  `src/planner/dispatch/*` are **left exactly as-is.** No W2 deletion forces a change to any
  of them (verified): the seed importer's ticket INSERT keeps `auto_blocked`/
  `consecutive_failures`; `tickets/data.py` keeps `claim_lock`/`claim_expires`; the
  dispatcher report and its tests are untouched.
- `core/loops.py` and `core/testmode.py` drive the **dispatcher** tick (`run_tick`, from
  `dispatch/runtime.py`) alongside the boundary tick — leave the dispatcher path entirely.
  In `days/scheduler.py` only the boundary tick + replan queue are edited; `scheduler.py`
  has no dispatcher code.
- Budget/breaker/timeout config keys in `core/config.py` (`max_runs`, `failure_limit`,
  `run_max_seconds`, `claim_ttl_seconds`, `tick_seconds`, `dispatch_enabled`,
  `dispatcher_lock_path`, etc.) are **not** touched — only `title_max_chars` is removed (§5).
- No W2 deletion appears to force a dispatcher change. If the implementer hits one, STOP and
  flag it rather than reaching into W3.

---

## 13. Verification expectations (post-implementation)

- `.venv/bin/ruff check .` — clean. Watch for orphaned imports after each deletion (the big
  ones: `days/api.py`, `days/scheduler.py`, `days/data.py`, the three adapters, the sprint
  files, and every edited test). Ruff's F401 is the primary net for dangling imports.
- `.venv/bin/mypy src/` — clean. Risk points: `Day` (plan field removed) consumers,
  `BoundaryAdapter` protocol (replan methods removed) vs the fakes/real, `Sprint`/`SprintItem`
  (dropped fields) consumers, `run_boundary` return type change (`None → str | None`).
- Full `./verify` — green. Fresh DB builds at SCHEMA_VERSION 2; the skip-scan stays clean
  (no empty/commented test bodies left behind after the surgical edits); both suites still
  collect tests.
- Standalone importer smoke: `python -m planner.seed --source migration/source-snapshot`
  against a fresh DB path exits 0 and prints a report.

**Tests that disappear (names):** `test_a17_plan_tree`;
`test_replan_latest_wins_discards_stale_result`, `test_replan_child_splices_only_target_node`,
`test_replan_failure_emits_boundary_failed_and_consumes`; `test_seed_demo_requires_empty_db`,
`test_seed_demo_dataset_shape`; `test_seed_demo_counts`, `test_seed_demo_twice_is_db_not_empty`,
`test_seed_source_dir_imports_fixture`, `test_seed_missing_dir_is_validation`,
`test_seed_file_not_dir_is_validation`, `test_seed_neither_key_is_validation`,
`test_seed_both_keys_is_validation`; `test_addendum_agent_is_forbidden_human_succeeds`;
whole files `test_e33_seed_fixture_ui`, `test_e34_snapshot_migration` (test_seed_e2e.py) and
`test_e35_dogfood_level_a` (test_dogfood.py — §14).
**Tests reworked in place (kept names):** `test_a18_boundary_job`,
`test_boundary_tick_runs_once_per_planning_date`, `test_a20_freeze_rules_and_overlap`
(→ overlap-only), `test_e28_day_boundary_accept_all`, `test_e31_refresh_restores_state`.

---

## 14. Flagged: scope the ticket did not fully anticipate

1. **Dogfood harness collateral (largest item — needs owner/integrator confirmation).**
   `tests/e2e/test_dogfood.py::test_e35_dogfood_level_a` runs `cli(server, "seed", "--demo")`
   and then `scripts/dogfood_cli.py`, which itself depends on **both** removed subsystems:
   the demo dataset (its 13 assertions are demo-entity-specific) **and** the day-plan-tree
   endpoint (`POST /api/day/{date}/plan/accept {node:1}`). It cannot stay green after this
   ticket. Rewriting it to seed via the fixture and drop the plan step is real build/design
   work (the fixture has different entities than the demo), which is out of character for a
   pure-deletion ticket. **Recommendation: delete `tests/e2e/test_dogfood.py` and
   `scripts/dogfood_cli.py`** as collateral of the demo + plan-tree removals. Flagged because
   it drops a dogfood harness the ticket never mentioned; the owner may prefer a rebuilt
   dogfood instead — if so, that is a follow-up, not this ticket.
   (Note: `dogfood_cli.py`'s `accept/plan` step at the ticket level — `POST
   /api/tickets/{id}/accept/plan` — is the ticket's plan **field**, unrelated; only its
   `POST /api/day/{date}/plan/accept` day-plan step is affected.)

2. **Sprint freeze + addenda removal is forced, larger than "drop dormant columns."** The
   ticket frames item 4 as columns only, but dropping `weekly_addenda` requires deleting the
   addenda writer + route + `Addendum`/`AddendumBody` + `addendum_added` event, and dropping
   the `*_frozen_at` columns strands the freeze routes/writers/events (and the already-dead
   `freeze.py`). An explicit prior comment in `sprints/api.py` kept freeze/addenda "reversible";
   dropping the columns overrides that. Both features are rev6-retired dormant, so removal is
   coherent — but it expands the blast radius into the sprint subsystem. Recommend full
   removal (as mapped in §6); flag for sign-off.

3. **Boundary report shape changes** (`replan` key removed; `judgment` preserved by having
   `run_boundary` return its outcome). This is a direct consequence of removing the replan
   queue + `boundary_runs`, not an unrelated change, and the e2e assertions are updated in
   §11. Called out because it is observable behavior on `/api/test/tick-boundary`.

4. **Materialize-on-read nuance in the new boundary guard.** The replacement guard ("next day
   already materialized") can be tripped early if the new planning day is read-materialized
   (e.g. an explicit `GET /api/day/<that-ISO>`, which materializes an empty day) before the
   first post-boundary tick — the boundary would then skip its deterministic pass
   (`day_closed`/carryover/judgment) for that date. This edge is inherent to the DECIDED
   replacement in notes.md ("check whether the next day is already planned/materialized") and
   is accepted; recorded here as a known behavior edge, not a blocker. In the normal tick
   flow (planning_date only flips at/after the boundary hour; the boundary loop runs every
   `tick_seconds`) the boundary materializes the day first.

---

## 15. Suggested implementation ordering (all after W1 lands)

1. Frontend dead code (`components.js`, `app.css`) — isolated, no backend coupling.
2. Plan-tree/replan backend (item 1): delete `tree.py`/`effects.py`; edit `days/*`,
   adapters, `EventKind`; then `boundary.py`/`scheduler.py` guard + report (item 2).
3. Seed surface (item 3): delete `seed/api.py`/`demo.py`; edit `server.py`/`cli/main.py`/
   `EventKind`/`ErrorCode`; add `seed/__main__.py`; edit `importer.py` INSERT.
4. Sprint dormant columns + freeze/addenda (item 4): contracts → data → api → views →
   `logic/__init__` → delete `freeze.py`.
5. `title_max_chars` (item 5): `contracts` constant → `config.py` → `config.yaml` (A1) →
   `server.py` → `api.py`.
6. Schema (`db.py`) DDL + SCHEMA_VERSION bump.
7. Tests (§11) + collateral (§14) + standalone smoke.
8. `ruff` / `mypy` / full `./verify`.

---

## 16. Binding amendments (post codex plan-review — see plan-review.md)

Codex plan-review returned: A (missed references), B (tests going red), C (schema/migration),
D (W3 breaches), F (verify instrument) — **all clean**. Four E-category (plan-vs-ticket delta)
findings, dispositioned by the orchestrator. These amendments bind the implementer:

**A1 — `config.yaml` (ACCEPTED gap; the one hard miss).** The checked-in `config.yaml` carries
`title_max_chars: 200`. Add `config.yaml` to the owned-files edit list (§1) and delete that
line as part of item 5 (§5). Verified: it is the only checked-in config key tied to W2-removed
machinery — every `boundary_*` key stays (boundary remains functional) and every dispatcher
key is W3.

**A2 — `freeze.py` deletion reclassified (kept, labeled).** Deleting
`sprints/logic/freeze.py` + its `logic/__init__` re-exports is *collateral dead-code cleanup*,
not a forced consequence of dropping the `*_frozen_at` columns (its functions take scalars, no
callers in `src/`). The deletion stays in the plan as the recommended action; integrator
sign-off at greenlight covers it (surfaced in the orchestrator report).

**A3 — forced vs collateral split inside item 4 (recorded).**
*Forced by the column drops:* everything touching `weekly_addenda` (the `add_addendum`
writer + `POST /sprints/{id}/addenda` route + `Addendum`/`AddendumBody` +
`EventKind.addendum_added`), the `_row_to_sprint`/`create_sprint`/views references to all
dropped columns, and the `test_a20` legs asserting the dropped `Sprint` fields.
*Collateral (recommended, sign-off with A2):* the inert `freeze_kickoff`/`freeze_review`
routes + data writers + `kickoff_frozen`/`review_frozen` event kinds — they never touch the
dropped columns but exist only to (someday) latch them; keeping them buys nothing since the
affected tests are edited either way.

**A4 — dogfood collateral made explicit (sign-off required).** `tests/e2e/test_dogfood.py`
cannot stay green (seeds via `seed --demo`) and `scripts/dogfood_cli.py` calls the removed
`POST /api/day/{date}/plan/accept`. Binding recommendation pending integrator sign-off: delete
both as broken-harness collateral (deleting the test alone would leave the script a dangling
reference to removed machinery). A rebuilt dogfood harness, if wanted, is a follow-up ticket —
not W2.

Post-amendment status: **implementable as planned.** No re-plan — the removal map's reference
inventory, test inventory, schema account, and W3 boundary all passed review unchallenged.
