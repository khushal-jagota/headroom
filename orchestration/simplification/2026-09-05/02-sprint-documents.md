# Sprint documents: three documents and the useful summary

Status: implemented on `codex/panels-simplification-sprints`; focused gates passed; awaiting
independent implementation review and serial integration by root.

## Problem and intended result

Sprint planning currently stores twelve separate prose fields. Eleven only supply headings
inside the documents screen; no rule interprets their contents. The twelfth, `primary_bet`,
also supplies the short orientation above Sprint tracking. Preserve that useful summary.
Replace the other eleven fields with three editable Markdown documents: `kickoff`,
`checkpoint`, and `review`. The final sprint has four prose values, down from twelve.

The user edits a Primary bet summary once, plus one coherent document for each planning
moment. Existing written work survives under its original headings. There is no parser
that tries to extract a summary from Markdown, no legacy read fallback, and no duplicate
primary-bet copy inside the migrated kickoff document.

Keep dates, inclusive overlap rules, current-sprint selection, Item identities and placement,
planning schedules, permissions, and every conversation/backend interface and implementation
unchanged. Do not redesign planning-worker stages in this ticket.

## Evidence and decision about the primary bet

`web/src/routes/SprintRoute.svelte:83,205-223` renders `primary_bet` separately above tracking,
using the existing expandable summary. This is a real navigational/orientation consumer,
not merely another entry in the document field catalogue. Source searches found no Home
consumer. `src/planner/cli/main.py` supports explicitly reading `primary_bet`, and
`docs/cli.md` uses that command as its concise sprint example.

The read-only live snapshot on 2026-09-05 contains seven sprints. All seven have kickoff
fields and a primary bet; five have review fields; none have checkpoint text. These counts
establish migration relevance, not usefulness. They do not prove whether an agent filled
fields because it had to. No personal text from that snapshot belongs in this tracked plan.

The actual structural evidence is stronger: `sprints/contracts.py` says the old kickoff,
checkpoint, and review field groups no longer gate anything; `data.py`, `api.py`, `views.py`,
and the CLI only enumerate/store/return prose. The planning skill asks for one coherent
approved review and then artificially maps it into five fields.

## Contract edits owned by the root

In `src/planner/sprints/contracts.py`:

- Replace `KICKOFF_FIELDS`, `MID_SPRINT_FIELDS`, and `REVIEW_FIELDS` with
  `SPRINT_DOCUMENT_FIELDS: Final[tuple[str, ...]] = ("kickoff", "checkpoint", "review")`.
- `Sprint` retains `id`, `name`, `date_start`, `date_end`, `primary_bet`, `created_at`, and
  `updated_at`. Replace its other eleven prose members with `kickoff`, `checkpoint`, and
  `review`, each a string defaulting to `""`. Give `primary_bet` the same empty-string
  default. Required identity/date fields remain first for dataclass ordering.
- `CreateSprintBody` retains name/date fields and `primary_bet`; remove `limiting_factor`,
  `supports`, and `premortem`; add optional `kickoff`, `checkpoint`, and `review` strings,
  each defaulting to empty at ingress. Accepting all final prose fields keeps create and
  update coherent without a special checkpoint/review initialization rule.
- Sprint PATCH recognizes name, dates, primary_bet, and the three documents. Existing
  null/wrong-type/atomic-write semantics remain. Removed keys are rejected by PATCH.
  Creation rejects unknown keys using the same allowed-field vocabulary as PATCH. This
  prevents silently dropping stale prose without keeping a catalogue of retired names.
  Root approved this broader, simpler rule during implementation review on 2026-09-05.

In `web/src/lib/types.ts`, replace the permissive `CurrentSprint = AnyRecord & {...}`
with the explicit final Sprint wire shape: id, name, date_start, date_end, primary_bet,
kickoff, checkpoint, review, created_at, updated_at. No new frontend-only data model.
The existing CurrentSprintResponse continues referring to CurrentSprint.

The implementation imports these settled shapes; it does not invent or change contracts.

## Data migration

Observed single Alembic head: `planning_day_direction`, at audited revision `b7ca8e0`.
New file: `src/planner/core/migrations/versions/sprint_documents.py`.
New revision: `sprint_documents`; parent: `planning_day_direction`. Before serial integration,
root reconciles this parent if another accepted migration has advanced the shared head.
Do not create a second head or rewrite historical migration files.

Add three `TEXT NOT NULL DEFAULT ''` columns. Read the eleven old columns and populate
new documents using the following fixed ordered mappings. Include a section only when
its old value is nonempty. Preserve its original bytes inside the section, including
whitespace-only values; use `value != ""`, not `value.strip()`, for migration inclusion.
Each section is `## <heading>\n\n<original value>` and sections are separated by `\n\n`.
An entirely empty group becomes `""`.

| New document | Old fields and headings, in order |
| --- | --- |
| kickoff | limiting_factor → Limiting factor; supports → Supports; premortem → Premortem |
| checkpoint | mid_where_we_stand → Where we stand; mid_whats_changed → What's changed; mid_what_to_adjust → What to adjust |
| review | outcomes → Outcomes; solo_reflection → Solo reflection; joint_discussion → Joint discussion; updates_to_thinking → Updates to thinking; carry_forward → Carry forward |

Keep `primary_bet` byte-for-byte as its own column. Do not change ids, dates, timestamps,
Item references, or any other table. Drop the eleven replaced columns after copying,
using the existing project's SQLite ALTER TABLE DROP COLUMN style. This avoids rebuilding
the referenced sprints table and its relationships. The migration includes no imports of
mutable runtime contracts and is irreversible, matching other removal migrations.

## Implementation and file ownership

One implementation agent owns this complete ticket after plan review; shared-file overlap
means the root must reserve these files before dispatch, especially CLI/types/tests. It
may parallelize only disjoint subsets if a slot and actual independent work are available.

Production files allowed:

- `src/planner/core/migrations/versions/sprint_documents.py` (new migration).
- `src/planner/sprints/data.py`, `api.py`, `views.py`: replace prose plumbing and create
  arguments; derive writable prose fields from the settled contract constant plus
  primary_bet/name. Preserve the canonical compound writer and planning authorization.
- `src/planner/cli/main.py`: sprint field map, `_sprint_record`, and create command only.
  Keep `--primary-bet`; replace old create flags with `--kickoff`, `--checkpoint`,
  `--review`. `sprint set` continues using its existing value/file mechanism; show
  manifests list primary_bet, kickoff, checkpoint, review. No aliases for retired fields.
- `src/planner/environments/fake_fixture.py`: express its old limiting-factor fixture
  inside kickoff Markdown; retain its useful primary bet.
- `web/src/routes/SprintRoute.svelte`: remove the twelve-field arrays and nested field
  editors. Show one Primary bet editor above the three document disclosures, and one
  existing InlineEdit per document. Keep primary-bet tracking rendering. Headings and
  editor placeholders identify the three moments without prescribing subheadings.
  Keep normal disclosure behavior, driven by each document's content instead of arrays.
- `src/planner/skills/panels-worker-planning-sprint/SKILL.md`: read the summary/documents;
  preserve useful planning/review questions as suggestions, not a compulsory questionnaire;
  write one approved review document and primary bet plus kickoff for the next sprint.
  Keep strategic user agreement and Closeout's write ownership.
- `docs/sprints.md`, `docs/cli.md`: document the four-value result and current commands.

Tests allowed:

- New `tests/unit/test_sprint_documents.py`: meaningful migration plus HTTP/CLI behavioral
  acceptance, grouped into a few tests rather than per-heading enumeration.
- Existing `tests/unit/test_authctx_routes.py`, `test_bounded_list_reads.py`,
  `test_cli_verbs_in_process.py`, `test_scheduled_tickets.py`: repair obsolete field
  expectations and raw INSERT fixtures only; retain their original purpose.
- New `web/tests/sprint-documents-browser.test.mjs`: one frontend test using the existing
  Vite/mock-fetch harness pattern; mount the route, edit documents and primary bet,
  assert intended PATCH values and readback, then verify tracking shows primary bet.
  `web/package.json`: register this focused frontend test in the existing suite.

Root-owned `contracts.py` and `types.ts` are read-only to implementation. Historical
migrations and `tests/fixtures/schema_v37.sql` remain frozen. No conversation, backend,
Item, schedule, or generic editor files are writable. If integration needs another file,
report the concrete reason to root before expanding the ownership list.

## Focused acceptance and verification

The migration proof starts a database at the parent revision, inserts synthetic populated,
empty, and whitespace-sensitive sprint prose plus referenced Item/Ticket rows, then upgrades.
It asserts exact new document text, unchanged primary bet/identity/dates/timestamps,
absence of retired columns, and preserved references with foreign_key_check. A fresh-db
read/create case proves the resulting schema is usable. Personal live prose is never used
in a committed test fixture.

The HTTP/CLI proof creates and reads all four prose values, changes two together, preserves
all values on an invalid mixed request, and rejects obsolete field names rather than
pretending to save them. Keep existing planning authorization, inclusive overlap, and
current-sprint proofs; adapt the old field-type test to checkpoint.

Run focused gates once the full ticket implementation settles:

1. `.venv/bin/python -m pytest -q tests/unit/test_sprint_documents.py tests/unit/test_sprints.py tests/unit/test_sprint_current_api.py tests/unit/test_authctx_routes.py tests/unit/test_bounded_list_reads.py tests/unit/test_cli_verbs_in_process.py tests/unit/test_scheduled_tickets.py`
2. `node web/tests/sprint-documents-browser.test.mjs`
3. `npm --prefix web run check`

The frontend test uses a static fixture server and mocked API, not Panels/FastAPI. No
browser-plus-live-backend E2E is required: the changed boundary is editor field-to-PATCH
mapping, and actual persistence/authorization are proved separately at the HTTP layer.
Services started by this test must select an available port and stop in its cleanup.

Do not run `./verify` for this ticket. The program reserves one final settled-tree run.
Record focused outputs here, obtain independent diff review, address every finding, and
let root integrate serially. This ticket is done when those focused gates and review pass;
repository completeness remains the final program gate.

## Implementation evidence — 2026-09-05

Implemented against root's contract commit `6fa58273` in the isolated sprint worktree.
The resulting schema has primary_bet plus kickoff/checkpoint/review. The migration
preserves every old value under its heading, leaves primary_bet separate, and removes
eleven old columns. The document screen now has four editors; the primary bet remains
visible on tracking. CLI creation/read/edit and the planning skill use the new documents.

Root's implementation spot-check approved rejecting all unknown create keys, using the
same allowed set as PATCH. Applied that decision and added an unknown-key rejection to
the existing behavioral test, then reran only the affected two-test module.

The frontend fixture mounts SprintRoute with the existing QueryClient and mocked fetch;
it uses Node's built-in HTTP server bound to port zero for static assets. It closes that
server and Chromium in cleanup and removes its generated temporary files. It starts no
Panels server and generates no web/dist. No runtime contract or conversation/backend
file was modified by this implementation agent.

### Focused backend gate

Command: `.venv/bin/python -m pytest -q tests/unit/test_sprint_documents.py tests/unit/test_sprints.py tests/unit/test_sprint_current_api.py tests/unit/test_authctx_routes.py tests/unit/test_bounded_list_reads.py tests/unit/test_cli_verbs_in_process.py tests/unit/test_scheduled_tickets.py`

Exit 0; all 79 selected tests passed. Full output:

```text
........................................................................ [ 91%]
.......                                                                  [100%]
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/Coding/planning-v2-worktrees/simplification-sprints/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient  # noqa

src/planner/core/clock.py:27
  /Users/khushaljagota/Coding/planning-v2-worktrees/simplification-sprints/src/planner/core/clock.py:27: PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_sprints.py)
    class TestClock:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

After the approved unknown-key policy change, command:
`.venv/bin/python -m pytest -q tests/unit/test_sprint_documents.py`.
Exit 0. Full output:

```text
..                                                                       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/Coding/planning-v2-worktrees/simplification-sprints/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

### Frontend gate

Command: `node web/tests/sprint-documents-browser.test.mjs`. Exit 0. Full output:

```text
sprint document editing and summary readback passed
```

Its first attempt compiled successfully but could not launch the absent Playwright
Chromium revision 1228. Installed that browser prerequisite using the worktree's
Playwright, then reran this gate successfully. Python/npm dependency trees were unchanged.

Command: `npm --prefix web run check`. Exit 0. Full output:

```text
> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/simplification-sprints/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

Ruff on all modified Python implementation/test files reported `All checks passed!`.
`git diff --check` was clean. No `./verify` was run.

### Integration follow-up

The migration retains parent `planning_day_direction`; root serializes any later program
migration above it. Twelve pre-existing migration test modules pin the old global head;
root was notified to update those together after the final order settles, rather than
rewriting them in competing branches. `docs/cli.md` and CLI implementation overlap with
the separate ticket-guidance change and require normal serial merge review.

## Independent implementation review — root

Reviewed 6fa58273..1c1e3b80 against this plan and the root-owned contracts.
Checked the frozen migration mapping and byte preservation, unchanged identity and
relationships, transactional create/patch validation, CLI projection, four-editor
screen, and focused evidence. No unresolved findings. Root clarified the request-body
comment to name Sprint creation’s strict unknown-field policy. The migration proof
compares whole Ticket rows and will need to compare the existing columns after the
separate guidance migration adds its column; root will resolve that integration overlap.

Accepted gates: 79 focused backend cases; final two-case admission rerun; real editor
fixture; Svelte check with zero errors/warnings; Ruff. No broad rerun needed here.

### Follow-up finding resolved during integration

A spot-check of environment provisioning found three stale Sprint field uses in the
fake fixture: `supports`, `premortem`, and `outcomes`. This would break isolated
environment creation. Root made the small call-site repair: the supporting paragraphs
join the kickoff document and the outcome goes into review. All six existing
`test_environment_fake_fixture.py` cases pass. No new test or mechanism was added.
