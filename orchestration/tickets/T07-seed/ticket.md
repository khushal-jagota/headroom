# T07 — Seed: markdown parsers, import pipeline, report, demo dataset, fixtures (stage 3)

## Scope

The seed domain's parsing logic, import pipeline, migration report, and demo dataset, plus the synthetic fixture directory and the unit test for acceptance item 19 (`test_a19_*`). The CLI/API wiring of `plan seed` is stage 4; this ticket delivers a callable `seed_from_source(conn, source_dir) -> MigrationReport` and `seed_demo(conn) -> None` (errors if DB non-empty).

Contracts implemented against (never modified): `src/planner/seed/contracts.py`, `sprints/contracts.py`, `tickets/contracts.py`, `days/contracts.py`, `core/contracts.py`, `core/errors.py`; infrastructure `core/db.py`, `core/events.py`, `core/ids.py`, `core/clock.py`. Writers from other domains' data layers may be imported and used — seeding routes through canonical writers wherever one exists.

## Files owned

- `src/planner/seed/logic/` — pure parsers (stdlib only): kickoff/review section splitter; sprint-tracking item parser (sections Todo/In Progress/Done/Blocked/Deferred → statuses per §12; `Priority:`/`Urgency:`/`Project:` sub-fields; Mode dropped; first line is the title, remaining sub-bullets joined into body); workspace ticket parser (only the `## Tickets` section; Readiness mapping Concepts→needs_success, Needs Shaping→needs_approach, Ready→needs_plan, In Progress→in_progress; `Ticket ID:` → alias; `Chat ID:` → chat_session_key; `Priority:`; `Body:`/`Success:`/`Approach:` text into the matching fields' values; other sub-bullets into the body); deferred parser (items = bullets under the `## Vylo/Tribe/Learning/Other` headings, `PN:` label prefix → priority, heading → project; preamble prose/rules are not items); ideas parser (bullets under `## Ideas`; optional `Project:` sub-bullet → project, else NULL); latest-daily selection (lexicographically greatest `daily/YYYY-MM-DD/` containing workspace.md).
- `src/planner/seed/importer.py` — the pipeline: parse → import via canonical writers → report. Idempotent by alias (tickets) and title (everything else): re-running against the same source creates no duplicates. The report (contract shape): counts imported per entity kind + an explicit list of every skipped/unparseable file or section (e.g. `Necessary Calls`, tracker.md/overview.md, historical daily folders, preamble rule blocks) — silent drops forbidden.
- `src/planner/seed/demo.py` — `plan seed --demo` dataset (§12): requires empty DB, else structured error; deterministic: 8 tickets spanning every state with varied priorities, deadlines, ceilings; one blocking link; one parent sprint item with child tickets; one planned day. Also a current sprint so the day/sprint views have content (the 8 tickets and items attach to it).
- `tests/fixtures/planning-md/` — synthetic fixture exercising every mapping: kickoff with all four fields; tracking with 6 items across all five sections (Todo 2, In Progress 1, Done 1, Blocked 1, Deferred 1) with varied Priority/Project and a Mode line (dropped); filled review file; TWO daily folders (2026-06-10, 2026-06-11) so only the latest feeds tickets; latest workspace has a `Necessary Calls` section (skipped, enumerated) and 4 tickets covering all four Readiness values, each with `Ticket ID:`, one with `Chat ID:`, one whose title exactly matches a tracking item title (→ `belongs_to` link), the rest standalone with sprint assignment; deferred.md with 3 items across ≥2 project headings and a rules preamble; ideas.md with 3 ideas, exactly one carrying `Project:`.
- `tests/unit/test_seed.py` — item 19.

## Test fences (SPEC §18.3 item 19, exact)

Importing `tests/fixtures/planning-md/` yields the exact expected entity counts and spot-checked values: item statuses (all five mappings), ticket states per the Readiness mapping, preserved Chat ID, deferred items with NULL sprint, idea with project; re-running seeds zero duplicates. Assert the report's skipped-sections list is exactly the designed skip set (nothing silent).

## Constraints

- Parsers are pure functions over text; no I/O in logic layer (importer does file reads).
- Never read any path outside the repository. The snapshot directory (`migration/source-snapshot/`) is item 34's target in stage 6 — do not wire tests to it here, but the parser semantics above must hold for it (same shapes).
- ruff + mypy strict clean; the named test green.
