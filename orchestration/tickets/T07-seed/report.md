# T07 report — seed: parsers, importer, report, demo, fixtures (stage 3, item 19)

## What was built

- `src/planner/seed/logic/` — pure parsers (stdlib + contracts only, no I/O, no clock):
  `blocks.py` (H2 section splitter, 2-space bullet tokenizer returning bullets + orphan prose,
  body re-emission, `Label:` field detection with digit-excluding labels, 120-char normalized
  excerpts, preamble residual helpers, the skip reason constants), `fieldmap.py`
  (Priority→Urgency→P3 resolution chain, project parsing, `PN:` prefix split), `kickoff.py`
  (kickoff/review section splitters, `Date range:` extraction, deterministic sprint name),
  `tracking.py` (five §12 status mappings via ITEM_STATUS_MAP; Priority/Urgency/Project consumed,
  Mode recognized-dropped, rest → body with order + nesting preserved), `workspace.py` (Tickets
  section only; READINESS_MAP; Ticket ID→alias, Chat ID→chat_session_key, Success/Approach→field
  values, Body + unrecognized sub-bullets → body; exact-match unambiguous title linking),
  `deferred.py` (project headings, PN labels, preamble = one skip entry, `deferred=True`),
  `ideas.py` (optional `Project:` sub-bullet), `latest.py` (lexicographically-greatest daily
  folder with workspace.md, everything else enumerated).
- `src/planner/seed/importer.py` — `seed_from_source(conn, source_dir) -> MigrationReport`:
  parse → single BEGIN IMMEDIATE transaction → direct SQL inserts + creation events → report.
  Idempotent: sprints by name, items/ideas by title, tickets by alias-else-title; re-run hits
  skip entirely (no updates, no events) and count in `duplicates_skipped`; links only for newly
  inserted tickets.
- `src/planner/seed/demo.py` — `seed_demo(conn)`: empty-DB guard over every table
  (`PlannerError(db_not_empty)` otherwise); deterministic-content dataset: current sprint
  (today−3 → today+10), 3 items, 8 tickets spanning all 7 states with varied
  priorities/deadlines/ceilings (never `dropped`), one `blocks` link, one parent item with 2
  belongs_to children (§3.3 columns), one planned day with PlanTree JSON and contiguous
  day_tickets.
- `tests/fixtures/planning-md/` — 11 files mirroring the snapshot layout (daily folders nested at
  `sprints/current/daily/`), exercising every mapping, both daily folders with tracker/overview,
  a decoy ticket in the older day, a Necessary Calls section, all four Readiness values, one
  Chat ID, one exact title match, rules preambles.
- `tests/unit/test_seed.py` — exactly one `test_a19_*` function covering the full §18.3 item-19
  fence, plus 8 supporting tests (demo guard/shape, pure-function edges, two review-driven
  regression tests).

## Test results (fresh, post-review fixes)

```
$ .venv/bin/pytest tests/unit/test_seed.py -q
.........                                                                [100%]
9 passed
```

`.venv/bin/ruff check src/planner/seed tests/unit/test_seed.py` → All checks passed!
`.venv/bin/mypy src/` → Success: no issues found in 74 source files.
Top-level orchestrator confirmed `./verify` run 002 shows item 19 PASS.

## Review outcomes

- **Plan review** (plan-review.md): 9/10 areas CLEAN; one violation on the `Body:` destination,
  resolved and bound as plan amendment A1 (see Concerns below).
- **Impl review** (impl-review.md): 8 CLEAN, 1 violation (two silent-drop edge paths — orphan
  prose in recognized sections; field-bullet continuation lines), both fixed with regression
  tests; 1 concern (sibling untracked files) refuted as the concurrent-wave design.

## Snapshot sanity-run (orchestrator, not wired into tests — item 34 is stage 6)

`seed_from_source` over `migration/source-snapshot/` on a fresh temp DB:

- run 1: **sprints=1** (`Sprint 2026-07-01 to 2026-07-12`, dates 2026-07-01/2026-07-12),
  **sprint_items=12** split **6 todo / 5 active / 1 done** (done = `Ship waitlist mechanics.`),
  **tickets=4** (mic-publish + app-typography → needs_plan; landing-gate-1 + durable-personas →
  in_progress; `20260702_114500_0ec57a` preserved on landing-gate-1), **deferred_items=9**
  (Vylo 4 / Tribe 2 / Learning 2 / Other 1), **ideas=20**, links=0, duplicates_skipped=0,
  **skipped list length 7** (2 historical daily folders, latest overview.md + tracker.md,
  Necessary Calls, deferred.md preamble, ideas.md preamble).
- run 2: 0 new entities, duplicates_skipped=46, skip list identical, events unchanged (46).

Matches the §12 ground truth exactly; the team lead's independent check agrees.

## Deviations from ticket

None. All owned files only; contracts and seed/api.py untouched; no test reads the snapshot.

## Concerns for the integrator

1. **`Body:` destination (spec-wording tension, resolved as A1).** §12 says "body/success/approach
   text into the matching fields' values" but §4.2 fixes tickets.fields to exactly four keys and
   there is no body column — `Body:` has no matching value slot. Resolution: Body + unrecognized
   sub-bullets → `fields.success.notes` (legal in every state, uniform, queryable, keeps
   `result.notes` free for review notes). Recorded in plan-review.md and plan.md amendment A1.
2. **Contract gap — ticket `Project:`.** `ParsedTicket` has no project attribute, so standalone
   imported tickets carry `project` NULL; the value is preserved verbatim inside the body text
   (`- Project: X` in success.notes). If the contract later grows a slot, only workspace.py + one
   INSERT change.
3. **Canonical writers.** At implementation time `tickets/data.py` / `sprints/data.py` did not
   exist (built concurrently by T04/T06), so the importer/demo do direct SQL inserts + creation
   events. "Seeding routes through canonical writers wherever one exists" can be satisfied at
   integration by rerouting creation through the landed writers if the top-level orchestrator
   wants; the seams are the private insert helpers in importer.py/demo.py.
4. **Blocked tracking items** import with `blocked_by = '[]'` (the markdown names no blocker
   ticket ids); §3.2's non-empty-list rule is a writer-level invariant the migration source cannot
   satisfy. Flag if a different posture is wanted.
5. Deferred.md items import as status `todo` (backlog-neutral; `deferred_next_sprint` is reserved
   for the tracking file's Deferred section) — codex judged this consistent with §12/§3.2.
