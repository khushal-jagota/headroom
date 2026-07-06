# W2 — Pure deletions: implementation report

Implemented by `w2-impl` (Opus) from the codex-reviewed plan. Delivered to the integrator as a
message (the harness blocked the agent from writing report files); persisted here by the
integrator. Codex raw diff-review output is durable at `impl-review.out`.

## Checks — integrator ran full `./verify` → **PASS**

- `ruff check .` clean · `mypy src/` clean (87 files) · `pytest tests/unit` 120 passed ·
  `pytest tests/e2e` 14 passed (was 17; −3 = deleted seed/dogfood e2e) · skip-scan clean.
- Fresh DB builds at `SCHEMA_VERSION` 2 · `python -m planner.seed --source migration/source-snapshot`
  exits 0 (1 sprint / 21 items / 4 tickets / 20 ideas); `--help` works editable-install (no PYTHONPATH).
- **Integrator `./verify`: PASS** (ruff / mypy / unit / build / e2e all green).

## What landed

**Deleted (whole files):** `days/logic/tree.py`, `days/logic/effects.py`, `seed/api.py`,
`seed/demo.py`, `sprints/logic/freeze.py`, `tests/e2e/test_seed_e2e.py`, `tests/e2e/test_dogfood.py`,
`scripts/dogfood_cli.py`.
**Created:** `src/planner/seed/__main__.py` — standalone `python -m planner.seed` (argparse; no
`cli.main` import).
**Edited (highlights):** `days/{contracts,data,api,scheduler,boundary}.py` — plan-tree/replan gone;
boundary guard → `_next_day_materialized`; `run_boundary` → `str|None`; tick report →
`{planning_date, ran, judgment}` (replan key gone). `core/adapters/{base,real,fakes}.py` — replan
methods + plan-tree imports gone. `core/contracts.py` — 9 plan/freeze EventKinds + `db_not_empty` +
`frozen_write` removed. `seed/importer.py` — dropped `current_state_note` INSERT. `cli/main.py` —
seed verb gone. `sprints/{contracts,data,api,views,logic/__init__}.py` — dormant columns +
freeze/addenda gone. `tickets/{contracts,api}.py` — `TITLE_MAX_CHARS` constant. `config.py` +
`config.yaml` + `server.py` — `title_max_chars` triple collapsed. `core/db.py` — dropped `days.plan`,
`boundary_runs`, `weekly_addenda`/`kickoff_frozen_at`/`review_frozen_at`, `current_state_note`;
`SCHEMA_VERSION` 1→2.
**Tests reworked:** `test_a18_boundary_job`, `test_boundary_tick_runs_once_per_planning_date`,
`test_a20_sprint_overlap` (renamed), `test_e28`/`test_e31`. **Removed:** `test_a17_plan_tree`, 3
replan, 2 seed-demo, 7 seed-route, `test_addendum_agent_is_forbidden_human_succeeds`.

## W3 exclusion list — verified untouched

`runs` table; `claim_lock`/`claim_expires`/`alias`/`auto_blocked`/`consecutive_failures` +
`idx_tickets_alias`; `X-Plan-*` headers; `run` CLI group; all of `dispatch/*` (zero diff);
budget/dispatch config keys; the title `CHECK(200)` DDL backstop — all intact. Seed importer still
INSERTs `auto_blocked`/`consecutive_failures`.

## Codex diff-review — verdict FAIL, all dispositioned (→ `impl-review.md` / `impl-review.out`)

Clean on B (tests) / C (schema) / D (W3 breaches) / E (boundary guard). Three A-items: A3
(`frozen_write` orphan) FIXED; A1/A2 (stale skill docs) accepted-valid, DEFERRED to the
rollover-rebuild wave.

## Integrator notes

Boundary-math (`boundary.py` / `scheduler.py` / `test_a18`) spot-checked — sound; `_TICK_MUTEX`
gives TOCTOU safety; the §14.4 edge (pre-planning the next day trips the top guard so its
deterministic pass is skipped; `_human_planned` "skipped" path now effectively dead) is accepted +
documented. Full `./verify` PASS. Committed to `main`.
