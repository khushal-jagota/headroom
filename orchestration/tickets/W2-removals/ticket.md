# W2 — Pure deletions (no replacement)

## Scope

Remove the dead / over-built machinery that has **no replacement** and is **not part of the
dispatcher rewire** (that rewire is Wave 3 — see the exclusion list below). These deletions reduce
surface before W3 and must leave `./verify` fully green. Every removal here is already **DECIDED**
in `orchestration/runtime-redesign/notes.md` (Removal & rework ledger + the linked sections) — read
those sections; do not re-litigate, do not remove anything not on this list.

This is mechanical, reference-heavy surgery: delete the machinery **and** repair every reference,
route, event, config key, and test that touched it, plus bump `SCHEMA_VERSION` and handle the
schema change. A half-removal that leaves dangling imports or red tests is a failure.

## Contracts (read before planning — law)

- `orchestration/runtime-redesign/notes.md` → **Removal & rework ledger**, and the linked
  **Days & rollover** (plan-tree, `boundary_runs`), **Data model** (dormant columns, seed surface,
  config knobs), **Sprints & sprint items** (`current_state_note`) sections. These name each
  removal + its rationale.
- The code as it exists on disk (authoritative for exact call sites / tests).

## What to REMOVE (this wave)

1. **Plan-tree / day-plan / replan subsystem** — the whole thing: `src/planner/days/logic/tree.py`,
   `src/planner/days/logic/effects.py` (verify it's plan-tree-only before deleting), the replan
   queue, the plan endpoints in `src/planner/days/api.py`, plan-related event types, and the
   `days.plan` column. Its job (an agent placing the day's tickets) is covered by the future
   rollover rebuild. Reconcile: if `days/logic/carryover.py` is a *separate* concern (not plan-tree),
   KEEP it; only remove what the plan-tree/replan subsystem owns — flag your call in the plan.
2. **`boundary_runs` table + its dedup guard.** Drop the table from `core/db.py`; replace the
   "did rollover run for this date" guard (in `days/boundary.py` / `days/scheduler.py`) with a
   direct check of whether the next day is already materialized. `days/boundary.py` otherwise
   **stays** functional (its staged-rollover rebuild is a later wave, not this one).
3. **Seed importer permanent surface** — remove the `/api/seed` route (`src/planner/seed/api.py`),
   the CLI seed verb (`src/planner/cli/main.py`), `src/planner/seed/demo.py` (`--demo`), the
   dedicated seed error code, and the idempotence / skip-audit machinery. **KEEP** the importer
   itself (`src/planner/seed/importer.py` + `src/planner/seed/logic/*`) as a one-shot standalone
   **script** for the cutover migration (give it a clean standalone entrypoint; it must not depend
   on the removed route). Remove/adjust the tests bound to the route/CLI (`tests/e2e/test_seed_e2e.py`,
   `tests/unit/test_seed.py`, seed parts of `tests/unit/test_chat_seed.py`) — keep any tests that
   still cover the retained importer/parser.
4. **Dormant columns** (`core/db.py`): `sprints.weekly_addenda`, `sprints.kickoff_frozen_at`,
   `sprints.review_frozen_at`, `sprint_items.current_state_note` (+ `days.plan` from item 1). Drop
   from the schema and from any serializer / writer that references them.
5. **Made-up config knobs** (`src/planner/core/config.py`): the `title_max_chars`
   config-vs-DDL-vs-startup-assert triple, and config keys for machinery removed **in this wave**
   only. (Leave dispatcher/breaker/budget config alone — that's W3.)

## Explicitly OUT of scope (do NOT touch — these are Wave 3's remove-and-replace)

- The `runs` table, `claim_lock` / `claim_expires` columns, `X-Plan-Run-Id` / `X-Plan-Claim`
  headers, the `run` CLI group.
- `src/planner/dispatch/*` — the breaker (`logic/breaker.py`), claims, eligibility, ordering,
  `runtime.py`, the dispatch API, `auto_blocked` / `consecutive_failures` columns, budget /
  concurrency cap, timeout-as-failure, full-config-reparse-per-tick.
- Anything the dispatcher reads. If a W2 deletion appears to force a dispatcher change, STOP and
  note it in the plan rather than reaching into W3's territory.

## Files likely owned (planner confirms exact set)

`src/planner/core/db.py` (schema + `SCHEMA_VERSION` bump), `src/planner/days/{api.py,boundary.py,
scheduler.py, logic/tree.py, logic/effects.py}`, `src/planner/seed/{api.py,demo.py,importer.py,
contracts.py}`, `src/planner/core/config.py`, `src/planner/cli/main.py`, and the affected tests in
`tests/unit/` + `tests/e2e/`. Any event-type registry that declared plan/boundary events.

## Acceptance for integration

- `.venv/bin/ruff check .` clean; `.venv/bin/mypy src/` clean — **no dangling imports / dead refs**.
- Full `./verify` **green** (run by the integrator): the removed features' tests are gone/adjusted;
  all remaining tests pass; the schema migrates (fresh DB builds at the new `SCHEMA_VERSION`).
- The retained seed importer runs as a standalone script (smoke it once).
- No behavior change to anything NOT on the removal list.

## Pipeline note (dispatch instruction)

**Planning phase only, then STOP.** Produce the removal-map plan (Opus planner: exact files, every
reference/route/event/config key to repair, every test to remove/adjust, the schema bump + migration
approach, the tree/effects/carryover boundary call), run the codex plan-review, sense-check it, and
report the plan. Do **NOT** implement yet — the integrator greenlights implementation only after W1
has landed (serialized writes). Implementation will use **codex at xhigh, write-enabled** (thorough
at mechanical deletion + reference repair), with an independent diff review.

## Boundaries

Work only inside the repo. Never run git. Never modify `~/.hermes`, PROGRESS.md, decisions.md,
CLAUDE.md, notes.md, or this ticket. Do not touch the Wave-3 exclusion list.
