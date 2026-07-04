# T07 — Seed: implementation plan

Scope per `orchestration/tickets/T07-seed/ticket.md`. Contracts are law: `src/planner/seed/contracts.py`,
`core/contracts.py`, `core/errors.py`, `sprints/contracts.py`, `tickets/contracts.py`, `days/contracts.py`.
No contract file is modified. No file outside the ticket's owned set is touched.

Public entry points delivered:

```python
# src/planner/seed/importer.py
def seed_from_source(conn: sqlite3.Connection, source_dir: str | Path) -> MigrationReport: ...

# src/planner/seed/demo.py
def seed_demo(conn: sqlite3.Connection) -> None: ...   # raises PlannerError(ErrorCode.db_not_empty)
```

Concurrency constraint (binding): `src/planner/tickets/data.py` and `src/planner/sprints/data.py` are being
built by sibling tickets right now and MUST NOT be imported. The importer is self-contained: direct SQL
inserts against the `core/db.py` DDL, `planner.core.events.append_event` for events, `planner.core.ids.new_id`
for ids. **Later integration step (recorded, not done here):** once the canonical creation writers land,
seeding can be rerouted through them; the ticket.md sentence "seeding routes through canonical writers
wherever one exists" is satisfied at integration time by the orchestrator, not by this ticket, because none
exist at implementation time.

Timestamps: `int(time.time())` inside `importer.py` / `demo.py` only. The `logic/` layer is pure — stdlib
only, no I/O, no clock, functions over text or passed-in name lists. Contract imports (`planner.seed.contracts`,
`planner.core.contracts`, `planner.sprints.contracts`, `planner.tickets.contracts`, `planner.core.errors`)
are stdlib-only modules and are allowed inside `logic/`.

---

## 1. Module map — `src/planner/seed/logic/`

### `logic/__init__.py`
Docstring only.

### `logic/blocks.py` — markdown tokenization shared by every parser

```python
@dataclass
class Bullet:
    text: str                      # bullet text after "- ", stripped of trailing whitespace
    children: list["Bullet"]       # nested bullets (one indent level deeper)
    extra_lines: list[str]         # non-bullet continuation lines that followed this bullet, verbatim

def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]: ...
def parse_bullets(text: str) -> list[Bullet]: ...
def emit_body(bullets: Sequence[Bullet]) -> list[str]: ...
def field_of(text: str) -> tuple[str, str] | None: ...
def excerpt_of(text: str) -> str: ...
```

Behavior, pinned:

- `split_sections`: split on lines matching `^## ` exactly (H2 only; `#`/`###` lines are content).
  Returns `(preamble_text, [(heading, section_body), ...])` where `heading` is the text after `"## "`,
  stripped; `section_body` is the verbatim text up to the next `## ` line or EOF. `preamble_text` is
  everything before the first `## ` line. Heading matching everywhere is case-sensitive exact string
  comparison (source headings are stable; the snapshot uses exact strings).
- `parse_bullets`: a bullet line matches `^( *)- (.*)$`; depth = `len(spaces) // 2` (2-space indents, as in
  every snapshot file). A depth-0 bullet starts a top-level bullet; deeper bullets attach to the most recent
  bullet one level up. A line matching `^( *)-\s*$` (placeholder dash, e.g. the snapshot's empty `Blocked`
  bullets) is ignored. A non-blank, non-bullet line inside a bullet block is appended verbatim to
  `extra_lines` of the nearest preceding bullet (information-preserving; no silent drop). Blank lines are
  structural separators only.
- `emit_body`: re-emit bullets as markdown, top-level at `"- "`, each nesting level indented two more
  spaces: depth-relative re-emission `("  " * depth) + "- " + text`, children recursively, `extra_lines`
  appended verbatim after their bullet. Returns lines; callers join with `"\n"`.
- `field_of`: matches `^([A-Za-z][A-Za-z ]*?):\s?(.*)$` against a bullet's text; returns
  `(label, value.strip())` or `None`. Digits are excluded from labels on purpose: `"P2: Title"` and
  `"Gate 1: ..."` never field-match (PN prefixes are handled by `split_pn_prefix`; `Gate 1` stays body).
  Field recognition applies at depth 1 only (direct sub-bullets of an item); a `Priority:`-looking bullet
  at depth 2 is body content.
- `excerpt_of`: `" ".join(text.split())[:120]` — whitespace-normalized first 120 chars. Every
  `SkippedSection.excerpt` in the system is produced by this function (deterministic, test-assertable).

Reason-string constants (module-level `Final[str]` in `blocks.py`; tests hard-code the literals as an
independent oracle):

```python
REASON_DAILY    = "not the latest daily folder; only the latest day's workspace.md is imported (R6)"
REASON_FILE     = "file has no migration mapping (only workspace.md is imported from a daily folder)"
REASON_SECTION  = "section has no migration mapping (SPEC 12)"
REASON_PREAMBLE = "preamble/rules prose; not importable items"
REASON_NO_READINESS = "ticket bullet has no recognized Readiness value; not imported"
REASON_TITLE_LONG   = "ticket title exceeds 200 characters; not imported"
```

### `logic/fieldmap.py` — field-value mapping helpers

```python
def resolve_priority(priority_raw: str | None, urgency_raw: str | None) -> Priority: ...
def parse_project(raw: str | None) -> Project | None: ...
def split_pn_prefix(text: str) -> tuple[Priority | None, str]: ...
```

- `resolve_priority` (contracts.py comment on `URGENCY_MAP` is the law): if `priority_raw` is a
  `PRIORITY_MAP` key → that priority. Else if `urgency_raw` (case-insensitively) is a `URGENCY_MAP` key →
  that priority. Else `Priority.P3` (DB default; empty/unknown values ignored). An unrecognized Priority
  label (e.g. `"P9"`) is treated as absent and falls through the chain.
- `parse_project`: `PROJECT_MAP.get(raw.strip())` → `Project | None`.
- `split_pn_prefix`: regex `^(P[0-3]): (.*)$`; on match returns `(Priority, rest)`, else `(None, text)`.

### `logic/kickoff.py` — kickoff + review section splitters

```python
KICKOFF_HEADINGS: Final[dict[str, str]] = {
    "Limiting Factor": "limiting_factor", "Primary Bet": "primary_bet",
    "Supports": "supports", "Pre-mortem": "premortem",       # snapshot spells "Pre-mortem"
}
REVIEW_HEADINGS: Final[dict[str, str]] = {
    "Outcomes": "outcomes", "Solo Reflection": "solo_reflection",
    "Joint Discussion": "joint_discussion", "Updates to Thinking": "updates_to_thinking",
    "Carry-forward": "carry_forward",                        # snapshot spells "Carry-forward"
}

def sprint_name(date_start: str, date_end: str) -> str: ...   # f"Sprint {date_start} to {date_end}"
def extract_date_range(text: str) -> tuple[str, str]: ...
def parse_kickoff(text: str, source_file: str) -> tuple[ParsedSprint, list[SkippedSection]]: ...
def parse_review(text: str, source_file: str) -> tuple[dict[str, str], list[SkippedSection]]: ...
```

- `extract_date_range`: first line matching `^Date range:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s*$`.
  Missing/malformed in the kickoff → `PlannerError(ErrorCode.validation, "sprint-kickoff.md has no parseable 'Date range:' line")`
  — a source without sprint dates cannot anchor anything (SPEC §12 maps kickoff → sprint; §3.1 requires dates).
- `parse_kickoff`: sections matched against `KICKOFF_HEADINGS`; the field value is the section body with
  leading/trailing blank lines stripped, inner markdown verbatim (SPEC §3.1: kickoff fields are markdown
  text). `ParsedSprint.name = sprint_name(...)`; review fields left `""`. Skips: any section under an
  unrecognized heading **with non-whitespace content** → one `SkippedSection(source_file, heading,
  REASON_SECTION, excerpt_of(body))`. An unrecognized heading whose section is empty (the snapshot's
  `## Weekly Check Addenda`) produces nothing: nothing to import and nothing dropped. Preamble handling:
  the H1 line (`# ...`) and `Date range:` / `Date:` lines are recognized structural material (parsed
  material per SPEC §12's file shapes, never skip entries); any *other* non-blank preamble content → one
  skip entry `(source_file, None, REASON_SECTION, excerpt)`.
- `parse_review`: same mechanics against `REVIEW_HEADINGS`; returns a dict containing all five field names,
  `""` where the section is empty or the heading absent (the snapshot review is exactly this). Its
  `Date range:` line is recognized-ignored (the kickoff owns the dates).

### `logic/tracking.py` — sprint-tracking item parser

```python
def parse_tracking(text: str, source_file: str) -> tuple[list[ParsedItem], list[SkippedSection]]: ...
```

- Sections matched against `ITEM_STATUS_MAP` (the five SPEC §12 statuses: Todo→todo, In Progress→active,
  Done→done, Blocked→blocked, Deferred→deferred_next_sprint). Empty recognized sections (snapshot Blocked /
  Deferred) yield no items and **no skip entry** (nothing to import, nothing dropped). Unrecognized
  non-empty sections → skip entry (`REASON_SECTION`). Preamble: H1 + `Date range:` recognized-ignored,
  anything else → skip entry.
- Each depth-0 bullet is one item. Title = the bullet text, verbatim (trailing period included — titles are
  exact strings, they are the idempotency and link keys).
- Recognized depth-1 fields (consumed, never body): `Priority:`, `Urgency:`, `Project:`, and `Mode:`
  (recognized, deliberately discarded per the `seed/contracts.py` comment and SPEC §12 "Mode dropped" —
  never body, never a skip entry). Priority = `resolve_priority(priority_raw, urgency_raw)`. Project =
  `parse_project(...)`; a missing or unrecognized `Project:` → `Project.Other` (DDL `sprint_items.project`
  is NOT NULL; `Other` is the enum's catch-all — flagged in Risks).
- Everything else (including `Current state:`, `Settled so far:`, nested bullets, continuation lines) →
  `body = "\n".join(emit_body(unrecognized_sub_bullets))`, source order and nesting preserved.
- `ParsedItem(deferred=False, deadline=None)` always: the tracking parser never sets `deadline` (decision 10
  — no §12 source field maps to it; dates inside sub-bullets stay prose in the body), and Deferred-section
  items keep their sprint (status `deferred_next_sprint`, `deferred=False`; the `deferred=True` flag is
  exclusively the deferred.md parser's, per the contract comment "True -> sprint_id stays NULL").

### `logic/workspace.py` — workspace ticket parser

```python
def parse_workspace(
    text: str, source_file: str, item_titles: Sequence[str],
) -> tuple[list[ParsedTicket], list[SkippedSection]]: ...

def match_item_title(title: str, item_titles: Sequence[str]) -> str | None: ...
```

- Only the `## Tickets` section produces tickets (SPEC §12: "the latest daily/YYYY-MM-DD/workspace.md
  tickets → tickets"). Every *other* non-empty section (e.g. `Necessary Calls`) → one skip entry
  `(source_file, heading, REASON_SECTION, excerpt_of(section_body))`. Preamble: H1 + `Date:` line
  recognized-ignored; other content → skip entry.
- Each depth-0 bullet in `## Tickets` is one ticket. Title = bullet text verbatim.
- Recognized depth-1 fields: `Ticket ID:` → `alias`; `Chat ID:` → `chat_session_key`; `Readiness:` →
  `state` via `READINESS_MAP`; `Priority:` → `resolve_priority(raw, None)`; `Success:` → `success`;
  `Approach:` → `approach`; `Body:` → starts the body; `Mode:` → recognized-dropped (same rule as items).
  If a recognized field's sub-bullet has nested children (not present in fixture or snapshot), the children
  are re-emitted with `emit_body` and appended to that field's value on new lines (information-preserving).
- **`Project:` is NOT a recognized ticket field** (decision 2): `ParsedTicket` has no project attribute and
  the contract is law, so `Project:` flows into the body reconstruction like every other unrecognized
  sub-bullet — information-preserving and queryable. Consequence: standalone imported tickets carry
  `project` NULL in the DB (see Risks — contract-gap flag, not worked around here).
- Body reconstruction (decision 1 mechanics), pinned exactly: `body` = the `Body:` value first (if present),
  then every unrecognized depth-1 sub-bullet (`Project:`, `Current state:`, `Next:`, `Boundary:`,
  `Assumption:`, `Carryover:`, ...) re-emitted via `emit_body` in source order with nesting preserved,
  all joined with `"\n"`. Empty → `""`.
- A ticket bullet with no `Readiness:` or an unmapped Readiness value is not importable (state is
  mandatory): skip entry `(source_file, "Tickets", REASON_NO_READINESS, excerpt_of(bullet text))`, ticket
  not emitted. A ticket title over 200 chars (DDL CHECK would abort the transaction): skip entry
  `(source_file, "Tickets", REASON_TITLE_LONG, excerpt)`, not emitted. Neither occurs in fixture/snapshot.
- `match_item_title` (decision 9): exact string equality against `item_titles`; returns the title iff
  **exactly one** occurrence exists in the list, else `None` (0 candidates → standalone; 2+ same-title
  candidates → ambiguous → no link, standalone). `parse_workspace` sets `ParsedTicket.item_title` from it.
  `item_titles` is the list of titles parsed from *this run's* sprint-tracking.md only — never deferred
  items, never pre-existing DB rows (SPEC §12: tickets are "linked to sprint items by title match when
  unambiguous"; the sprint items in scope are the ones this source defines). Note: title-keyed idempotency
  makes duplicate titles within one run dedupe hits at import time, so the ambiguity branch is unreachable
  through imported rows — it exists as a pure-function branch with its own non-a19 unit test.

### `logic/deferred.py` — deferred backlog parser

```python
def parse_deferred(text: str, source_file: str) -> tuple[list[ParsedItem], list[SkippedSection]]: ...
```

- Preamble (everything before the first `## ` heading, minus the H1 line and blank lines): if any content
  remains (the rules block) → **exactly one** skip entry `(source_file, None, REASON_PREAMBLE,
  excerpt_of(preamble_minus_h1))`. One entry for the whole contiguous block, not per-line (decision 4:
  entry granularity is one contiguous skipped block).
- Sections whose heading is a `PROJECT_MAP` key: each depth-0 bullet is one item; `split_pn_prefix` gives
  `(priority, title)` — no PN prefix → `Priority.P3` and the whole text as title. Heading → `project`.
  Sub-bullets → body via `emit_body` (nesting preserved; per-item dates stay prose in the body — decision 10,
  `deadline=None` always). `status = ItemStatus.todo`, `deferred = True` (SPEC §12: "deferred.md → sprint
  items with sprint_id = NULL"; §3.2: NULL sprint means backlog work — `todo` is the neutral backlog
  status, and `deferred_next_sprint` would misstate them as this-sprint review outcomes).
- Unrecognized non-empty section headings → skip entry (`REASON_SECTION`).

### `logic/ideas.py` — ideas parser

```python
def parse_ideas(text: str, source_file: str) -> tuple[list[ParsedIdea], list[SkippedSection]]: ...
```

- Preamble: same one-entry rule as deferred.md (`REASON_PREAMBLE`).
- `## Ideas` section: each depth-0 bullet is one idea; title = bullet text verbatim. A depth-1
  `Project: X` sub-bullet with `X` in `PROJECT_MAP` → `project` (consumed); a `Project:` with an
  unrecognized value stays in the body and `project` stays `None`. All other sub-bullets → body via
  `emit_body`. `project=None` when no `Project:` sub-bullet (SPEC §12 "optional `Project:` sub-bullet →
  project, else NULL"). The snapshot's `## Ideas` has its first bullet on the line directly after the
  heading (no blank line) — `parse_bullets` doesn't care; the fixture mirrors this quirk.
- Unrecognized non-empty sections → skip entry.

### `logic/latest.py` — latest-daily selection (pure)

```python
def pick_latest_daily(folders: Sequence[tuple[str, bool]]) -> tuple[str | None, list[str]]: ...
```

- Input: `(folder_name, has_workspace_md)` pairs. Candidates = names matching `^\d{4}-\d{2}-\d{2}$` with
  `has_workspace_md=True`. Chosen = lexicographically greatest candidate (SPEC ticket.md: "lexicographically
  greatest daily/YYYY-MM-DD/ containing workspace.md"), or `None`. Returns `(chosen, skipped)` where
  `skipped` = every other folder name, ascending — non-date names and workspace-less folders included
  (they are enumerated, never silently ignored).

---

## 2. `src/planner/seed/importer.py` — pipeline

```python
def seed_from_source(conn: sqlite3.Connection, source_dir: str | Path) -> MigrationReport: ...
```

Stdlib imports: `json`, `sqlite3`, `time`, `pathlib`. Planner imports: contracts, `logic/*`,
`planner.core.events.append_event`, `planner.core.ids` (`new_id`, `ID_PREFIXES`), `planner.core.errors`.
All `source_file` strings in the report are POSIX relative paths from the source root
(`path.relative_to(root).as_posix()`).

Pipeline order (also the skip-list emission order and the insert/event order — pinned):

1. `root = Path(source_dir)`; `now = int(time.time())`. Missing `root` or missing
   `root/"sprints/current/sprint-kickoff.md"` → `PlannerError(ErrorCode.validation, ...)` (a source
   without a kickoff has no sprint to anchor to). Every other file/dir (`sprint-tracking.md`,
   `sprint-review.md`, `daily/`, `deferred.md`, `ideas.md`) is optional: absent → contributes nothing and
   no skip entry (a file that does not exist is not a "skipped section"; nothing was dropped).
2. Parse (collecting skips in this order): kickoff → tracking → review → daily-folder selection →
   latest-folder non-workspace files → workspace → deferred.md → ideas.md.
   - Daily: `daily_dir = root/"sprints/current/daily"`; folders = sorted subdirectory names with a
     `has workspace.md` flag; `pick_latest_daily` → chosen + skipped names. Each skipped folder → one
     **folder-granularity** skip entry `("sprints/current/daily/<name>", None, REASON_DAILY, "")` —
     excerpt `""` because a folder has no single skipped text (decision 4). Files inside a skipped folder
     produce no entries of their own (the folder entry covers them).
   - In the chosen folder: every file that is not `workspace.md` (i.e. `tracker.md`, `overview.md`,
     anything else), ascending filename → one entry per file
     `("sprints/current/daily/<name>/<file>", None, REASON_FILE, excerpt_of(file text))`.
   - `parse_workspace(text, rel, item_titles=[i.title for i in tracking_items])`.
3. Open **one transaction** for all writes: `conn.execute("BEGIN IMMEDIATE")` (the connection is in
   autocommit mode per `core/db.connect`); `COMMIT` on success, `ROLLBACK` and re-raise on any exception.
   All-or-nothing: a half-imported source is worse than a failed run.
4. **Sprint** (idempotency key: `name` — decision 3/5): `SELECT id FROM sprints WHERE name = ?`.
   - Hit → `duplicates_skipped += 1`; `sprint_id` = existing id; **no updates, no events** (a re-run hit is
     a skip, §12 demands no duplicates, nothing more).
   - Miss → `INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, supports,
     premortem, outcomes, solo_reflection, joint_discussion, updates_to_thinking, carry_forward,
     created_at, updated_at)` with `id = new_id(ID_PREFIXES["sprint"])`, kickoff fields from
     `ParsedSprint`, review fields from `parse_review`'s dict, both timestamps `now` (remaining columns
     take DDL defaults: `weekly_addenda '[]'`, freeze timestamps NULL). Event:
     `append_event(conn, sprint_row_id, EventKind.sprint_created, {"name":…, "date_start":…, "date_end":…,
     "source":"seed"}, now)`. `report.sprints += 1`.
5. **Tracking items** (key: `title`, global across `sprint_items` — §12 "idempotent by alias/title"):
   for each `ParsedItem` in file order, `SELECT id FROM sprint_items WHERE title = ?`.
   - Hit → `duplicates_skipped += 1`; record `items_by_title[title] = existing_id` (still a link target).
   - Miss → `INSERT INTO sprint_items (id, title, body, status, priority, deadline, project,
     current_state_note, sprint_id, created_at, updated_at)` = `(new_id(ID_PREFIXES["sprint_item"]),
     title, body, status.value, priority.value, NULL, project.value, '', <sprint_id>, now, now)`
     (`blocked_by`/`status_proposal` take DDL defaults). Event `sprint_item_created`
     `{"title":…, "status":…, "sprint_id":…, "source":"seed"}` (entity = item id). `report.sprint_items += 1`;
     record in `items_by_title`.
6. **Tickets** (key: `alias` when present, else `title` — decision 5): for each `ParsedTicket` in file order:
   - Dedupe: `alias is not None` → `SELECT id FROM tickets WHERE alias = ?`; else
     `SELECT id FROM tickets WHERE title = ?`. Hit → `duplicates_skipped += 1`, **no link attempt**, next.
   - Miss → resolve parent: `item_id = items_by_title.get(ticket.item_title)` when `item_title` set.
     `INSERT INTO tickets (id, title, state, priority, deadline, project, sprint_item_id, sprint_id,
     recap, ceiling, at_cap, auto_blocked, consecutive_failures, chat_session_key, alias, fields,
     created_at, updated_at)` with:
     - `id = new_id(ID_PREFIXES["ticket"])`, `deadline` NULL, `recap ''`, `auto_blocked 0`,
       `consecutive_failures 0`, `claim_lock`/`claim_expires` omitted (DDL NULL).
     - `project` NULL always (decision 2). Parented (`item_id` found): `sprint_item_id = item_id`,
       `sprint_id` NULL (§3.3: derived from the parent item; writable only when `sprint_item_id` IS NULL).
       Standalone: `sprint_item_id` NULL, `sprint_id = <sprint row id>` (§12: "else standalone with sprint
       assignment").
     - `ceiling = state`, `at_cap = 'propose'` (decision: R2's conservative spirit — agents may draft the
       current gating field for approval, nothing advances — while keeping `state <= ceiling` coherent for
       migrated mid-pipeline tickets; the R2 literal default `needs_success` would put every imported
       `in_progress` ticket above its own ceiling). Imported states are the four Readiness targets, so
       `ceiling` is never `dropped`.
     - `fields` JSON: build the full four-key shape mirroring the DDL default exactly —
       `{"success": {"value": v, "proposal": None, "notes": n}, "approach": {...}, "plan": all-null,
       "result": all-null}` serialized with `json.dumps`; `success.value = parsed.success`,
       `approach.value = parsed.approach`, `success.notes = parsed.body or None` (decision 1), everything
       else null.
   - Event `ticket_created` `{"title":…, "state":…, "alias":…, "source":"seed"}`. `report.tickets += 1`.
   - If parented: `INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, 'belongs_to')` + event
     `link_added` (entity = ticket id, payload `{"from_id":…, "to_id":…, "kind":"belongs_to",
     "source":"seed"}`). `report.links += 1`. Links are created only when the ticket row is newly inserted,
     so a re-run creates zero link rows (and the `links` PK + one-belongs-to index back this structurally).
     `links` never counts in `duplicates_skipped` (that counter counts entities: sprints, items, tickets,
     ideas — decision 5).
7. **Deferred items** (key: `title`, same `sprint_items` table): hit → `duplicates_skipped += 1`; miss →
   insert as step 5 but `sprint_id` NULL, `status 'todo'` + `sprint_item_created` event
   (`"sprint_id": None`). `report.deferred_items += 1`.
8. **Ideas** (key: `title` against `ideas`): hit → `duplicates_skipped += 1`; miss →
   `INSERT INTO ideas (id, title, body, project, created_at, updated_at)` with
   `new_id(ID_PREFIXES["idea"])`, `project` = enum value or NULL + `idea_created` event
   `{"title":…, "source":"seed"}`. `report.ideas += 1`.
9. `COMMIT`; assemble `MigrationReport` (skips already in pinned order). **No events are emitted for any
   duplicate skip** (decision 6) — re-running leaves the events table untouched.

---

## 3. `src/planner/seed/demo.py`

```python
def seed_demo(conn: sqlite3.Connection) -> None: ...
```

1. Empty-DB guard: `SELECT COUNT(*)` over every DDL table — `sprints, sprint_items, tickets, days,
   day_tickets, ideas, links, events, runs, boundary_runs`. Any count > 0 →
   `PlannerError(ErrorCode.db_not_empty, "seed --demo requires an empty database", {"table": <first non-empty>})`.
2. `now = int(time.time())`; `today = datetime.now().astimezone().date()` (local date; demo is explicitly
   not under test-clock control, and no unit fence asserts its calendar values — content stays assertable
   for e2e item 35 via titles/states/relative dates).
3. Single transaction (`BEGIN IMMEDIATE` … `COMMIT`), inserts via the same direct SQL patterns as the
   importer. Ids via `new_id()` / `day_id(today)` — **not** hand-pinned ids: `ticket.md` demands a
   deterministic *dataset*, which is determinism of content (titles, states, priorities, links, day
   membership), not of random slugs; canonical id generation keeps the unique-index and id-shape guarantees
   uniform, and e2e assertions key off titles. Events: `sprint_created`, `sprint_item_created` ×3,
   `ticket_created` ×8, `link_added` ×3, `day_created` (entity = day id, `{"source":"demo"}`),
   `day_ticket_added` ×3 (`{"ticket_id":…, "position":…, "cause":"demo"}`) — all payloads carry
   `"source":"demo"`.

**Sprint** (range contains today — pinned formula): `date_start = today - 3 days`,
`date_end = today + 10 days` (14-day sprint), `name = "Demo Sprint"`, kickoff fields short non-empty
strings (pinned: limiting_factor `"Demo: one clear slice must ship."`, primary_bet
`"Demo: shipping the feature slice unblocks the review queue."`, supports
`"- Demo support line."`, premortem `"- Demo premortem line."`), review fields `""`, no freeze timestamps.

**Sprint items** (all on the demo sprint):

| title | status | priority | project | body |
|---|---|---|---|---|
| `Ship the demo feature end to end.` | active | P1 | Vylo | `Parent item for the demo ticket pair.` |
| `Research the search index options.` | todo | P2 | Learning | `` |
| `Retire the legacy export job.` | done | P3 | Other | `` |

**Tickets** — 8 spanning every state; the 8th is a second `in_progress` (pinned: it is the most useful
duplicate for dogfooding the day view and dispatcher surfaces, and it lets the demo carry an at-cap
`stop` ticket sitting exactly at its ceiling):

| # | title | state | priority | deadline | ceiling | at_cap | parent item | sprint_id | project |
|---|---|---|---|---|---|---|---|---|---|
| t1 | `Draft the onboarding email success criteria.` | needs_success | P2 | today+7 | needs_success | propose | — | demo sprint | Vylo |
| t2 | `Choose the search indexing approach.` | needs_approach | P1 | today+3 | needs_plan | propose | — | demo sprint | Learning |
| t3 | `Plan the demo feature rollout.` | needs_plan | P1 | NULL | in_progress | propose | item 1 | NULL | NULL |
| t4 | `Implement the demo feature slice.` | in_progress | P0 | today+1 | needs_review | propose | item 1 | NULL | NULL |
| t5 | `Review the analytics dashboard numbers.` | needs_review | P2 | today | done | propose | — | demo sprint | Vylo |
| t6 | `Write the release notes.` | done | P3 | NULL | done | stop | — | demo sprint | Tribe |
| t7 | `Prototype the voice input toggle.` | dropped | P3 | NULL | needs_approach | stop | — | demo sprint | Vylo |
| t8 | `Fix the flaky login test.` | in_progress | P0 | today | in_progress | stop | — | demo sprint | Vylo |

Every ceiling ≥ state (done/dropped rows carry sensible terminal grants); ceilings never `dropped`.
Parented tickets: `belongs_to` link + `sprint_item_id` set + `project` NULL + `sprint_id` NULL (derived) —
§3.3. Standalone tickets carry a real project (demo has no contract gap — values are ours to pin).

Ticket `fields` values — for every state a ticket has already passed, the gating-field `value` is set
(pinned formula: `f"Demo {field} for {title}"`): t1 none; t2 success; t3 success+approach;
t4/t8 success+approach+plan; t5/t6 all four; t7 success only (dropped from needs_approach). Additionally
`t5.fields.result.notes = "Check the conversion query joins before approving."` (the §4.2 review-notes
slot, so Review has demo content).

**Links**: two `belongs_to` (t3→item1, t4→item1) + exactly one `blocks`: `(t8, t5, 'blocks')` —
t8 is `in_progress` (not done/dropped) so t5 reads as blocked per §3.6.

**Planned day**: `days` row `id = day_id(today)`, `brief = "Demo day: ship the feature slice and clear
the review queue."`, `notes ''`, `chat_session_key` NULL. `plan` = JSON of a minimal `PlanTree`
(days/contracts.py shape, stored as `{"root": {"focus": …, "status": …}, "children": [{"ticket_id": …,
"note": …, "status": …, "position": …}, …]}`):
root `{focus: "Ship the demo feature slice.", status: "accepted"}`; children
`[{ticket_id: t4, note: "Land the slice behind the flag.", status: "accepted", position: 0},
{ticket_id: t8, note: "Stabilise the login test before review.", status: "proposed", position: 1}]`.
`day_tickets`: `(day, t4, 0), (day, t8, 1), (day, t5, 2)` — contiguous from 0.

---

## 4. Fixture — `tests/fixtures/planning-md/` (complete file texts)

Layout mirrors the snapshot exactly (daily folders NESTED at `sprints/current/daily/`):

```
tests/fixtures/planning-md/
├── deferred.md
├── ideas.md
└── sprints/current/
    ├── sprint-kickoff.md
    ├── sprint-tracking.md
    ├── sprint-review.md
    └── daily/
        ├── 2026-06-10/{workspace.md, tracker.md, overview.md}
        └── 2026-06-11/{workspace.md, tracker.md, overview.md}
```

Both daily folders carry `tracker.md`/`overview.md` (decision 8: mirrors the snapshot, proves
folder-granularity skips for 06-10 — no per-file entries — and per-file skips for the latest folder).

### `sprints/current/sprint-kickoff.md`

```markdown
# Sprint Kickoff

Date range: 2026-06-08 to 2026-06-21

## Limiting Factor

The importer has no fixture coverage, so every parser change is a guess.

## Primary Bet

If the fixture pack mirrors the real layout, the migration lands without surprises.

## Supports

- A synthetic tree that mirrors the live planning folder.
- One decoy day folder to prove latest-only selection.

## Pre-mortem

- The fixture drifts from the real shapes and the tests prove nothing.

## Weekly Check Addenda
```

### `sprints/current/sprint-tracking.md`

```markdown
# Sprint Tracking

Date range: 2026-06-08 to 2026-06-21

## Todo

- Write the parser design note.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Learning
  - Cover the tokenizer first.
    - Then the emitter.
  - Keep examples from the real files.

- Refit the garden shed.
  - Priority: P3
  - Project: Other
  - Clear the bench before winter.

## In Progress

- Build the import pipeline.
  - Priority: P0
  - Urgency:
  - Mode: Campaign
  - Project: Vylo
  - Parse first, insert second, report third.

## Done

- Ship the fixture pack.
  - Priority: P2
  - Mode: Supervised
  - Project: Vylo
  - Landed with the first importer slice.

## Blocked

- Publish the beta changelog.
  - Urgency: high
  - Project: Tribe
  - Waiting on the release notes ticket.

## Deferred

- Automate the weekly digest.
  - Urgency:
  - Project: Vylo
  - Next sprint once the event feed settles.
```

(Non-identity paths exercised: `Learning`/`Other`/`Tribe` projects; empty `Urgency:` lines; `Mode:` lines
dropped on three items and absent on three; the Blocked item has **no** `Priority:` and `Urgency: high` →
P1 via `URGENCY_MAP`; the Deferred item has no `Priority:` and empty `Urgency:` → P3 default; nested
sub-bullets on the first item prove body order + nesting.)

### `sprints/current/sprint-review.md`

```markdown
# Sprint Review

Date range: 2026-06-08 to 2026-06-21

## Outcomes

The fixture pack shipped and the importer ran clean twice.

## Solo Reflection

Parsing was calmer once the tokenizer was pinned.

## Joint Discussion

Agreed the skip list is the real safety net.

## Updates to Thinking

Idempotency by title is enough for v1.

## Carry-forward

Wire the CLI in the next stage.
```

### `sprints/current/daily/2026-06-10/workspace.md` (decoy — must NOT be imported)

```markdown
# Workspace

Date: 2026-06-10

## Tickets

- Decoy ticket from an older day.
  - Ticket ID: ticket-20260610-decoy
  - Readiness: Ready
  - Priority: P1
  - Project: Vylo
  - Body: Must never be imported; only the latest day feeds tickets.
```

### `sprints/current/daily/2026-06-10/tracker.md`

```markdown
# Daily

Date: 2026-06-10

## Focus

Old tracker content that must never be imported.
```

### `sprints/current/daily/2026-06-10/overview.md`

```markdown
# Overview

Date: 2026-06-10

## Brief Take

Old overview content that must never be imported.
```

### `sprints/current/daily/2026-06-11/workspace.md`

```markdown
# Workspace

Date: 2026-06-11

## Necessary Calls

- Pick one lane for the day and hold it.
  - Recommended call: finish the import pipeline before touching polish.

## Tickets

- Sketch the onboarding survey.
  - Ticket ID: ticket-20260611-onboarding-survey
  - Readiness: Concepts
  - Mode: Manual
  - Priority: P2
  - Project: Vylo
  - Body: Work out what the survey must learn before any UI is sketched.
  - Current state: nothing exists yet.
  - Next: list the three decisions the survey feeds.

- Shape the export format.
  - Ticket ID: ticket-20260611-export-format
  - Chat ID: 20260611_090000_abc123
  - Readiness: Needs Shaping
  - Priority: P1
  - Project: Tribe
  - Success: a one-page format note that a second reader can implement from.

- Cut the release branch.
  - Ticket ID: ticket-20260611-release-branch
  - Readiness: Ready
  - Priority: P0
  - Project: Vylo
  - Success: the branch exists and CI is green on it.
  - Approach: branch from main after the fixture tests pass, then tag.

- Build the import pipeline.
  - Ticket ID: ticket-20260611-import-pipeline
  - Readiness: In Progress
  - Mode: Paired
  - Priority: P0
  - Project: Vylo
  - Body: Parse the fixture tree and land rows behind one transaction.
  - Success: the fixture import passes twice with zero duplicates.
  - Boundary: importer only; no CLI wiring yet.
```

(All four Readiness values; every ticket has `Ticket ID:`; exactly one `Chat ID:` (export-format); exactly
one title — `Build the import pipeline.` — exactly matching a tracking item title → belongs_to; the other
three standalone.)

### `sprints/current/daily/2026-06-11/tracker.md`

```markdown
# Daily

Date: 2026-06-11

## Focus

Latest tracker content; enumerated as skipped, never imported.
```

### `sprints/current/daily/2026-06-11/overview.md`

```markdown
# Overview

Date: 2026-06-11

## Brief Take

Latest overview content; enumerated as skipped, never imported.
```

### `deferred.md`

```markdown
# Deferred

Important work not in the current sprint.

Rules:
- Group by project.
- Priority is not urgency.

## Vylo

- P2: Tune the retrieval cache.
  - Watch the hit rate for a week first.

- P0: Rotate the leaked staging key.

## Learning

- P3: Read the WAL internals paper.
  - Take notes for the wiki.
    - File under storage engines.
```

### `ideas.md`

```markdown
# Ideas

Interesting concepts worth exploring later.

Rules:
- Keep entries readable standalone.

## Ideas
- Voice memo inbox for quick capture.
  - Project: Vylo
  - Transcribe on arrival and file into the day note.
- Weekly print digest of the learning wiki.
- Pair the boundary brief with a spoken audio version.
  - Record it right after the plan is accepted.
```

---

## 5. Expected fixture outcome (pinned, test-asserted)

**First-run report**: `sprints=1, sprint_items=6, deferred_items=3, tickets=4, ideas=3, links=1,
duplicates_skipped=0`, and `skipped` is EXACTLY this list, in this order (full `SkippedSection`
equality — excerpts are whole normalized texts, all under 120 chars):

| # | source_file | heading | reason | excerpt |
|---|---|---|---|---|
| 1 | `sprints/current/daily/2026-06-10` | None | REASON_DAILY | `` (empty) |
| 2 | `sprints/current/daily/2026-06-11/overview.md` | None | REASON_FILE | `# Overview Date: 2026-06-11 ## Brief Take Latest overview content; enumerated as skipped, never imported.` |
| 3 | `sprints/current/daily/2026-06-11/tracker.md` | None | REASON_FILE | `# Daily Date: 2026-06-11 ## Focus Latest tracker content; enumerated as skipped, never imported.` |
| 4 | `sprints/current/daily/2026-06-11/workspace.md` | `Necessary Calls` | REASON_SECTION | `- Pick one lane for the day and hold it. - Recommended call: finish the import pipeline before touching polish.` |
| 5 | `deferred.md` | None | REASON_PREAMBLE | `Important work not in the current sprint. Rules: - Group by project. - Priority is not urgency.` |
| 6 | `ideas.md` | None | REASON_PREAMBLE | `Interesting concepts worth exploring later. Rules: - Keep entries readable standalone.` |

No entries for: the empty `Weekly Check Addenda` section, the empty-none tracking sections, H1 titles,
`Date range:`/`Date:` lines (recognized non-item preamble is parsed material), or `Mode:` lines
(recognized, deliberately discarded field).

**DB truth after first run** (spot-check targets):

- Sprint: name `Sprint 2026-06-08 to 2026-06-21`, dates `2026-06-08`/`2026-06-21`,
  `limiting_factor = "The importer has no fixture coverage, so every parser change is a guess."`,
  `outcomes = "The fixture pack shipped and the importer ran clean twice."`.
- 9 `sprint_items` rows; the 6 tracking items all have `sprint_id` = the sprint row, `deadline` NULL:

| title | status | priority | project |
|---|---|---|---|
| `Write the parser design note.` | todo | P1 | Learning |
| `Refit the garden shed.` | todo | P3 | Other |
| `Build the import pipeline.` | active | P0 | Vylo |
| `Ship the fixture pack.` | done | P2 | Vylo |
| `Publish the beta changelog.` | blocked | P1 | Tribe |
| `Automate the weekly digest.` | deferred_next_sprint | P3 | Vylo |

  Body of `Write the parser design note.` is exactly:
  `- Cover the tokenizer first.\n  - Then the emitter.\n- Keep examples from the real files.`
  The blocked item has `blocked_by = '[]'` (source names no blocker tickets; accepted looseness, see Risks).
- 4 tickets (and ONLY 4 — the decoy alias `ticket-20260610-decoy` and title `Decoy ticket from an older
  day.` are absent):

| alias | state | priority | chat_session_key | sprint_item_id | sprint_id | project |
|---|---|---|---|---|---|---|
| `ticket-20260611-onboarding-survey` | needs_success | P2 | NULL | NULL | sprint | NULL |
| `ticket-20260611-export-format` | needs_approach | P1 | `20260611_090000_abc123` | NULL | sprint | NULL |
| `ticket-20260611-release-branch` | needs_plan | P0 | NULL | NULL | sprint | NULL |
| `ticket-20260611-import-pipeline` | in_progress | P0 | NULL | item `Build the import pipeline.` | NULL | NULL |

  All four: `ceiling = state`, `at_cap = 'propose'`, `recap ''`, `deadline` NULL. Fields JSON:
  - onboarding-survey: `success.value` None, `success.notes` exactly
    `Work out what the survey must learn before any UI is sketched.\n- Project: Vylo\n- Current state: nothing exists yet.\n- Next: list the three decisions the survey feeds.`
  - export-format: `success.value = "a one-page format note that a second reader can implement from."`,
    `success.notes = "- Project: Tribe"`.
  - release-branch: `success.value = "the branch exists and CI is green on it."`,
    `approach.value = "branch from main after the fixture tests pass, then tag."`,
    `success.notes = "- Project: Vylo"`.
  - import-pipeline: `success.value = "the fixture import passes twice with zero duplicates."`,
    `success.notes` exactly
    `Parse the fixture tree and land rows behind one transaction.\n- Project: Vylo\n- Boundary: importer only; no CLI wiring yet.`
  - `plan`/`result` slots all-null everywhere; `proposal` null everywhere.
- 1 links row: `(import-pipeline ticket id, item id, 'belongs_to')`.
- 3 deferred items, `sprint_id` NULL, status todo, deadline NULL:
  `Tune the retrieval cache.` P2 Vylo (body `- Watch the hit rate for a week first.`);
  `Rotate the leaked staging key.` P0 Vylo (body ``);
  `Read the WAL internals paper.` P3 Learning (body `- Take notes for the wiki.\n  - File under storage engines.`).
- 3 ideas: `Voice memo inbox for quick capture.` project Vylo, body
  `- Transcribe on arrival and file into the day note.`; `Weekly print digest of the learning wiki.`
  project NULL, body ``; `Pair the boundary brief with a spoken audio version.` project NULL, body
  `- Record it right after the plan is accepted.`
- Events: exactly 18 rows (1 sprint_created + 9 sprint_item_created + 4 ticket_created + 3 idea_created +
  1 link_added).

**Re-run (same conn, same source)**: `sprints=0, sprint_items=0, deferred_items=0, tickets=0, ideas=0,
links=0, duplicates_skipped=17` (1 sprint + 6 items + 4 tickets + 3 deferred + 3 ideas), `skipped` ==
the identical 6-entry list (skips describe the source, not the DB). Row counts in every table unchanged;
events count still 18.

---

## 6. `tests/unit/test_seed.py`

Header: `from pathlib import Path`; `FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "planning-md"`.
Uses the `tmp_db` fixture from `tests/unit/conftest.py`. **No test reads `migration/source-snapshot/`.**
No skip/xfail/only patterns, no commented-out tests, no empty bodies (verify's skip-scan is binding).

### The single item-19 test (the ONLY function whose bare name starts with `test_a19_`)

`def test_a19_seed_fixture_import_counts_mappings_idempotency_and_skip_list(tmp_db: Connection) -> None:`

Assertion list, in order (every fence clause of §18.3 item 19 / ticket.md):

1. `report = seed_from_source(tmp_db, FIXTURE)` — counts exactly
   `(1, 6, 3, 4, 3, 1, 0)` for (sprints, sprint_items, deferred_items, tickets, ideas, links,
   duplicates_skipped).
2. DB row counts: sprints 1, sprint_items 9, tickets 4, ideas 3, links 1, events 18.
3. Sprint row: name/date_start/date_end + `limiting_factor` and `outcomes` exact strings (§5 table above).
4. All five status mappings by title (table in §5): todo/active/done/blocked/deferred_next_sprint; the six
   tracking items all have `sprint_id` = the sprint id and `deadline IS NULL`; priorities
   P1/P3/P0/P2/P1/P3 (P1-from-`Urgency: high` and P3-default asserted explicitly); projects
   Learning/Other/Vylo/Vylo/Tribe/Vylo; body of `Write the parser design note.` == pinned nested markdown;
   blocked item `blocked_by == '[]'`.
5. All four Readiness mappings by alias (table in §5).
6. `chat_session_key == "20260611_090000_abc123"` on export-format; NULL on the other three.
7. Fields JSON per ticket == the pinned values in §5 (json.loads then compare value/notes slots; `plan`
   and `result` all-null; proposals null) — this pins decision 1 (Body → `fields.success.notes`) and
   decision 2 (`Project:` preserved inside that notes text).
8. Decoy not imported: no row with alias `ticket-20260610-decoy`; tickets count is 4.
9. Link: import-pipeline ticket has `sprint_item_id` = the `Build the import pipeline.` item id,
   `project IS NULL`, `sprint_id IS NULL`; links row `(ticket_id, item_id, 'belongs_to')` exists; the
   three standalone tickets have `sprint_id` = sprint id, `sprint_item_id IS NULL`.
10. Deferred: 3 rows `sprint_id IS NULL`, status `todo`, priorities/projects/bodies per §5.
11. Ideas: project Vylo on exactly the voice-memo idea, NULL on the other two; bodies per §5.
12. Skip list: build the six expected `SkippedSection(...)` literals (hard-coded strings from §5) and
    assert `report.skipped == expected` (full dataclass equality, order included).
13. Re-run: `report2 = seed_from_source(tmp_db, FIXTURE)` — all entity counts 0, `links == 0`,
    `duplicates_skipped == 17`, `report2.skipped == expected`; row counts and events count unchanged
    (zero new rows anywhere).

### Supporting tests (none prefixed `test_a19_`)

- `test_seed_demo_requires_empty_db(tmp_db)` — insert one bare idea row, then
  `pytest.raises(PlannerError)` around `seed_demo`; assert `.code == ErrorCode.db_not_empty`.
- `test_seed_demo_dataset_shape(tmp_db)` — on empty DB: runs; 8 tickets whose state multiset is
  {needs_success, needs_approach, needs_plan, in_progress×2, needs_review, done, dropped}; no ceiling is
  `dropped`; exactly one `blocks` link and it targets the needs_review ticket; the parent item has exactly
  2 belongs_to children, both with `sprint_item_id` set, `project` NULL, `sprint_id` NULL; sprint range
  contains today's local date; day row `day_<today>` exists; day_tickets positions == [0, 1, 2]; plan JSON
  parses with a root focus and 2 children; calling `seed_demo` again raises `db_not_empty`.
- `test_match_item_title_ambiguity` — pure: 1 candidate → title; 0 → None; 2 identical candidates → None.
- `test_resolve_priority_fallback_chain` — pure: `P0` label wins over urgency; no label + `high` → P1;
  no label + empty urgency → P3; unknown label `P9` + `LOW` → P3... (`low`→P3 case-insensitive) and
  unknown label + no urgency → P3.
- `test_pick_latest_daily_selection` — pure: picks lexicographic max with workspace; a newer folder
  without workspace.md is not chosen but is listed as skipped; non-date names listed as skipped; empty
  input → (None, []).
- `test_workspace_ticket_missing_readiness_is_enumerated` — pure `parse_workspace`: a ticket bullet
  without `Readiness:` yields no ticket and one skip entry with `REASON_NO_READINESS`; a >200-char title
  yields `REASON_TITLE_LONG`.

Gates: `.venv/bin/ruff check .`, `.venv/bin/mypy src/` (strict), `.venv/bin/pytest tests/unit/test_seed.py -q`.

---

## 7. Predicted snapshot outcome (stage-6 sanity target — NOT wired into tests here)

`seed_from_source(conn, "migration/source-snapshot")` on a fresh DB must yield:

- `sprints=1` — `Sprint 2026-07-01 to 2026-07-12`, kickoff fields filled, review fields all `""`.
- `sprint_items=12` — 6 todo, 5 active, 1 done (`Ship waitlist mechanics.`); Blocked and Deferred sections
  are empty → no items, no skip entries.
- `tickets=4` — `Publish microphone support.` and `App typography pass.` → needs_plan (Ready);
  `Landing page Gate 1: shippable enough to push `main`.` and `First durable-personas exploration.` →
  in_progress; `Chat ID: 20260702_114500_0ec57a` preserved on landing-gate-1 (the other two Chat IDs in
  that file are preserved on their tickets too); aliases = the four `ticket-2026070…` strings.
- `links=0` — no workspace ticket title exactly equals a tracking item title.
- `deferred_items=9` — Vylo 4 / Tribe 2 / Learning 2 / Other 1, priorities from the PN prefixes
  (P2,P2,P3,P2 / P2,P3 / P2,P3 / P3), `sprint_id` NULL, status todo.
- `ideas=20`, all `project` NULL (no idea carries a `Project:` sub-bullet).
- `duplicates_skipped=0` first run; re-run: 46 (1+12+4+9+20) and zero new rows.
- Predicted skip list, exactly, in order (excerpts = normalized 120-char prefixes of the named texts):

| # | source_file | heading | reason |
|---|---|---|---|
| 1 | `sprints/current/daily/2026-07-01` | None | REASON_DAILY |
| 2 | `sprints/current/daily/2026-07-02` | None | REASON_DAILY |
| 3 | `sprints/current/daily/2026-07-03/overview.md` | None | REASON_FILE |
| 4 | `sprints/current/daily/2026-07-03/tracker.md` | None | REASON_FILE |
| 5 | `sprints/current/daily/2026-07-03/workspace.md` | `Necessary Calls` | REASON_SECTION |
| 6 | `deferred.md` | None | REASON_PREAMBLE |
| 7 | `ideas.md` | None | REASON_PREAMBLE |

(The empty `Weekly Check Addenda`, the empty review sections, the empty Blocked/Deferred sections, and
every `Mode:` line produce no entries. The `Two gates:`/`Gate 1:` nested bullets in the landing item stay
in its body with nesting preserved; `field_of`'s letters-only label rule keeps `Gate 1:` from
field-matching.)

---

## 8. Risks / notes for the implementer

- **Contract gap — ticket `Project:`** (flag for the orchestrator/report, do not work around): imported
  standalone tickets carry `project` NULL because `ParsedTicket` has no project slot; the value is
  preserved verbatim inside `fields.success.notes` (`- Project: X`). If the contract later grows a slot,
  only `workspace.py` + the importer INSERT change.
- **Blocked tracking items** import with `blocked_by = '[]'` — §3.2 says blocked carries a non-empty
  blocker list, but the markdown source names no ticket ids; migration preserves source truth and the DDL
  permits it. Enumerate in decisions.md at integration if the orchestrator wants a different posture.
- **`in_progress` tickets have `plan.value` null** — §12 maps only body/success/approach; there is no
  `Plan:` source field. Expected migration artifact.
- **`Current state:` sub-bullets stay in the body**, not `current_state_note` — §12 defines no such
  mapping; do not invent one.
- **No updates on dedupe hits** — a re-run with edited kickoff/review text changes nothing; §12 only
  demands "no duplicates". Re-running is not a sync.
- **fields JSON**: build the dict and `json.dumps` it; consumers parse JSON, byte-identity with the DDL
  default string is not required (key order should still mirror success/approach/plan/result for
  readability).
- mypy strict: annotate everything; `sqlite3.Row` indexing returns `Any` — cast/assert at the few points
  the importer reads back ids. Ruff: line length 100, `E/F/W/I/UP/B` — keep imports sorted, no mutable
  defaults (Bullet uses explicit list construction, or `field(default_factory=list)`).
- The importer never reads any path outside the passed `source_dir`; tests pass the fixture dir only.
- `seed/api.py` is stage-4 wiring — do not touch it.

---

## Decisions made (for codex review, numbered per the open list)

1. **Ticket `Body:` → `fields.success.notes`, always** (plus the reconstructed unrecognized sub-bullets,
   Body text first). §4.2 makes `notes` free-guidance text writable in any state, so it is legal for all
   four imported states with one uniform rule; `recap` would breach §3.3's past-needs_success invariant
   for Concepts tickets (forcing a hybrid), and gating-field-dependent notes would pollute `result.notes`,
   which §4.2 reserves as review notes. Queryable via the fields JSON; no enumerated skip needed.
2. **Ticket `Project:` → body reconstruction** (unrecognized sub-bullet, information-preserving);
   consequence: standalone imported tickets have `project` NULL. Flagged as a contract-gap concern, not
   worked around.
3. **Sprint name = `f"Sprint {date_start} to {date_end}"`** — deterministic pure function of the kickoff
   date range, so the idempotency key is stable across re-runs. Snapshot: `Sprint 2026-07-01 to 2026-07-12`.
4. **Skips**: one entry per contiguous skipped block; folder-granularity for non-latest daily folders
   (excerpt `""`); per-file entries for non-workspace files in the latest folder; one entry per
   unrecognized non-empty section; one entry per preamble rules block (deferred.md, ideas.md). Empty
   sections/headings and recognized structural preamble (H1, `Date range:`/`Date:`) and `Mode:` produce
   nothing: nothing imported and nothing dropped. Exact reason strings, order, and excerpt rule
   (`normalize[:120]`) pinned in §1/§5/§7.
5. **Idempotency**: tickets by `alias`, falling back to `title` when a ticket has no `Ticket ID:`; items
   and ideas by `title` (items global across `sprint_items`, covering tracking + deferred); sprints by
   `name`. A hit skips entirely — no updates, no events — and increments `duplicates_skipped` (entities
   only; links never count and are only attempted for newly inserted tickets, so re-runs add zero link
   rows; the links PK backs this structurally). Fixture re-run: `duplicates_skipped = 17`.
6. **Events**: `sprint_created`, `sprint_item_created`, `ticket_created`, `idea_created`, `link_added`
   with pinned minimal payloads carrying `"source": "seed"` (demo: `"demo"` plus `day_created` /
   `day_ticket_added`); none on duplicate skips.
7. **Demo**: full table pinned in §3 — 8 tickets over all 7 states with the 8th a second `in_progress`;
   ceilings ≥ state, never `dropped`; one `blocks` link; one parent item with 2 children (links + columns
   per §3.3); sprint `today-3 .. today+10`; one planned day with a minimal `PlanTree` JSON and contiguous
   `day_tickets`. `new_id()` ids (dataset determinism is content determinism; e2e asserts by title).
8. **Fixture**: complete texts in §4; both daily folders carry tracker/overview; expected numbers
   `1/6/3/4/3/1/0`, re-run `duplicates_skipped 17`, six-entry skip list pinned to the excerpt.
9. **Title-match linking**: exact equality, exactly-one-candidate, against this run's
   sprint-tracking.md titles only (never deferred/DB items); ambiguity → standalone, kept as a pure
   function branch with a non-a19 test.
10. **`ParsedItem.deadline` is never set by any parser** (always `None`; dates in sub-bullets stay prose
    in the body). Also pinned nearby: deferred.md items get `status=todo` (backlog-neutral;
    `deferred_next_sprint` is the tracking-Deferred section's status), and imported tickets get
    `ceiling = state`, `at_cap = propose` (R2's conservative spirit without putting migrated tickets above
    their own ceiling).

---

## Binding amendments (post codex plan review — see plan-review.md)

Codex reviewed this plan against ticket.md, SPEC §12/§18.3(19)/§3/§4.2–4.4, the contracts, and the real
snapshot. Nine of ten areas CLEAN; one finding, resolved as follows and now binding:

**A1 — Ticket `Body:` destination stays `fields.success.notes` (decision 1 upheld).** SPEC §12's
"body/success/approach text into the matching fields' values" has no implementable value target for
`Body:`: §4.2 fixes `tickets.fields` to exactly four keys and the tickets table has no body column, so
only Success: and Approach: have matching fields. Every value slot is semantically wrong for Body
(collides with real Success: data on mic-publish; would present a pending gate as passed). `success.notes`
is the single home that is legal in every imported state, uniform, queryable, and keeps `result.notes`
free for its §4.2 review-notes role. The implementer builds exactly what §1 (workspace.py), §2 step 6,
and §5's pinned fields JSON say — no change. The implementation report MUST flag this as a resolved
spec-wording tension + contract-gap concern for the integrator (alongside the `Project:`-in-body note in
Risks). Everything else in this plan is unchanged and binding as written.
