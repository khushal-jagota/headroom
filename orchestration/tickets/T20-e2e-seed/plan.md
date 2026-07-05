# T20 plan — E2E items 33–34: seed fixture UI + snapshot migration

Deliverables: `tests/e2e/test_seed_e2e.py` (new) and one additive parameter on
`server_factory.make()` in `tests/e2e/conftest.py`. Nothing else is touched.

Every pinned value below was produced by running `seed_from_source` in-process against both
sources on a throwaway DB (twice each) — not transcribed from memory. The snapshot skipped-list
block in §2.4 was additionally round-trip verified: the exact Python block below compared `==`
against the importer's output, byte-for-byte, including the U+2019 (’) in the workspace excerpt
and the U+2013 (–) in the deferred excerpt.

Ground truth verified on disk:

- `tests/e2e/conftest.py` — `server_factory.make(gateway=None)` hardcodes
  `PLAN_FAKE_NOW = FAKE_NOW = "2026-07-04T12:00:00"`; `cli(...)` appends `--json`, runs with
  `cwd=REPO_ROOT`, asserts rc 0, returns parsed stdout JSON; `open_page(..., settled=True)`
  waits ready selector → `wsOpens >= 1` → `flushes >= 1` → ready selector again; `api.get`
  asserts < 300 and returns JSON.
- `src/planner/cli/main.py` seed command — posts `{"source_dir": os.path.abspath(source)}` to
  `POST /api/seed`; with `--json` stdout is the MigrationReport dict (`asdict`, so `skipped` is
  a list of plain dicts with keys `source_file/heading/reason/excerpt`, `heading` null → None).
- `src/planner/tickets/views.py` — `list_tickets` serializes via `ticket_json`, which includes
  `alias` and `chat_session_key` → `GET /api/tickets` alone covers the item-34 ticket audit; no
  per-ticket detail fetches needed. Response shape `{"tickets": [...]}`.
- `src/planner/sprints/views.py` — `/api/sprint/current` → `{sprint, groups, loose_tickets}`;
  groups keyed by all five statuses, each item sorted by (priority rank, created_at, id);
  loose tickets `ORDER BY created_at, id`. `/api/items?sprint_id=null` → `{"items": [...]}`
  sorted by (priority rank, created_at, id). `/api/ideas` → `{"ideas": [...]}` ordered
  `created_at DESC, id`. `/api/sprints` → `{"sprints": [...]}`.
- Screens — Board: `section[data-screen="board"]` appended only after the `/api/board` fetch
  resolves; cards `[data-column="<state>"] [data-card][data-ticket-id]`, title in
  `.entity-row-title`. Sprint: root div gets `data-screen="sprint"` synchronously; content
  (`.sprint-header` with `h1.screen-title` = name and `.sprint-dates` =
  `date_start + " – " + date_end`, en dash; `[data-status-group="<status>"]` with
  `[data-item-id]` rows; `[data-loose]` with `[data-ticket-id]` rows) appears only after the
  fetch. Backlog: sections `[data-backlog-items]` / `[data-ideas]` exist synchronously; rows
  (`[data-item-id]` / `[data-idea-id]`, titles in `.entity-row-title`) land per independent
  fetch. Empty screens show quiet-lines, cards/rows never render placeholders.
- `scripts/verify_lib.py` — anchors `test_e33` / `test_e34`; PASS iff exactly one collected
  bare test name starts with `test_e33_` / `test_e34_` across the whole e2e junit and it
  passed. Skip-scan forbids the substrings `@pytest.mark.skip`, `pytest.skip(`, `xfail`,
  `.only` anywhere in a line, commented-out `def test_`, and empty test bodies. The test file
  must not contain those substrings even in comments/docstrings.
- Only `tests/e2e/test_flows_a.py` (e22–e27) exists in the e2e suite today; no `test_e33_` /
  `test_e34_` name exists anywhere (grepped). T19 adds e28–e32 in its own file.

---

## 1. File-by-file blueprint

### 1.1 `tests/e2e/conftest.py` — one additive parameter, two targeted Edits

T19 works on this file concurrently: apply exactly these two `Edit` operations (both old
strings are unique in the file today — verified), never a full-file rewrite. Default behavior
stays byte-for-byte identical, so no existing T18/T19 test changes meaning.

Edit 1 — signature:

```python
# old (unique)
    def make(gateway: str | None = None) -> ServerHandle:
# new
    def make(gateway: str | None = None, fake_now: str | None = None) -> ServerHandle:
```

Edit 2 — env value:

```python
# old (unique)
                "PLAN_FAKE_NOW": FAKE_NOW,
# new
                "PLAN_FAKE_NOW": fake_now if fake_now is not None else FAKE_NOW,
```

Why: `/api/sprint/current` picks the sprint whose date range contains the planning date derived
from `PLAN_FAKE_NOW` (boundary hour 5). The synthetic fixture's sprint is 2026-06-08 →
2026-06-21; under the harness default (2026-07-04) the Sprint screen renders "No current
sprint.". Item 33 boots its server with `fake_now="2026-06-11T12:00:00"` (12:00 > 05:00 →
planning date 2026-06-11, inside the window). Item 34's snapshot sprint is 2026-07-01 →
2026-07-12, which contains the default 2026-07-04 → item 34 uses the plain `server` fixture
untouched.

### 1.2 `tests/e2e/test_seed_e2e.py` — new file, exactly two tests

Layout (all helpers non-`test_` named; no parametrize; every wait carries `timeout=WAIT_MS`):

```
module docstring (SPEC §18.3 items 33–34; no forbidden substrings)
from __future__ import annotations
from playwright.sync_api import Page

WAIT_MS = 10_000
FIXTURE_SRC = "tests/fixtures/planning-md"        # cli cwd is REPO_ROOT; CLI abspaths it
SNAPSHOT_SRC = "migration/source-snapshot"

<pinned constants from §2: reason strings, SNAPSHOT_SKIPPED, board/sprint/backlog
 expectations for e33, ticket/deferred expectations for e34>

def _report_counts(report: dict) -> tuple: ...     # 7-tuple in report-field order
def _texts(page: Page, selector: str) -> list[str]: ...   # eval_on_selector_all textContent
def _wait_count(page: Page, selector: str, n: int) -> None: ...  # wait_for_function + WAIT_MS

def test_e33_seed_fixture_ui(server_factory, context_factory, open_page, cli): ...
def test_e34_snapshot_migration(server, context_factory, open_page, cli, api): ...
```

- `_texts`: `page.eval_on_selector_all(selector, "els => els.map(e => e.textContent)")` —
  textContent, not `inner_text`, so title equality is exact (`.entity-row-title` holds the bare
  title string; chips live in a sibling span).
- `_wait_count`: `page.wait_for_function("a => document.querySelectorAll(a.sel).length === a.n",
  arg={"sel": selector, "n": n}, timeout=WAIT_MS)`.
- `test_e33_` deliberately does **not** request the `server` fixture (it would boot an unused
  July-clock server); it builds its own via `server_factory(fake_now="2026-06-11T12:00:00")`.

No other files. No asset changes, no unit-suite changes, no new fixtures beyond the one
parameter.

---

## 2. Pinned ground truth (verified in-process, twice per source)

### 2.1 Item 33 — fixture report (run 1)

| field | value |
|---|---|
| sprints | 1 |
| sprint_items | 6 |
| deferred_items | 3 |
| tickets | 4 |
| ideas | 3 |
| links | 1 |
| duplicates_skipped | 0 |

(For reference only, not asserted in e33: the fixture's skipped list is the 6 entries unit
test `test_a19_...` pins; a fixture re-run reports `duplicates_skipped == 17`. e33 runs seed
once — the idempotency e2e proof belongs to item 34.)

### 2.2 Item 33 — UI expectations

Board `/#/board` (per-column card title lists; each pinned column has exactly one card, the
two tail columns are empty; `dropped` has no column):

| column (`data-column`) | `.entity-row-title` texts |
|---|---|
| needs_success | `["Sketch the onboarding survey."]` |
| needs_approach | `["Shape the export format."]` |
| needs_plan | `["Cut the release branch."]` |
| in_progress | `["Build the import pipeline."]` |
| needs_review | `[]` |
| done | `[]` |

Sprint `/#/sprint`:

- Header: `h1.screen-title` textContent `"Sprint 2026-06-08 to 2026-06-21"`; `.sprint-dates`
  textContent `"2026-06-08 – 2026-06-21"` (U+2013 en dash, spaces on both sides).
- Status groups (`[data-status-group="<s>"] [data-item-id] .entity-row-title`), ordered lists —
  the todo order is deterministic because the sort key is (priority rank, created_at, id) and
  the two todo items have distinct priorities (P1 then P3):

| status group | titles (ordered) |
|---|---|
| todo | `["Write the parser design note.", "Refit the garden shed."]` |
| active | `["Build the import pipeline."]` |
| done | `["Ship the fixture pack."]` |
| blocked | `["Publish the beta changelog."]` |
| deferred_next_sprint | `["Automate the weekly digest."]` |

- Loose section (`[data-loose] [data-ticket-id] .entity-row-title`): exactly 3 rows, **set**
  comparison (order is `created_at, id`; created_at is one frozen fake-clock tick for every
  seeded row, ids carry random suffixes → order non-deterministic):
  `{"Sketch the onboarding survey.", "Shape the export format.", "Cut the release branch."}`.
  ("Build the import pipeline." is parented to its sprint item via the one belongs_to link, so
  it is not loose.)

Backlog `/#/backlog`:

- `[data-backlog-items] [data-item-id]`: exactly 3 rows, **ordered** (distinct priorities →
  deterministic): titles `["Rotate the leaked staging key.", "Tune the retrieval cache.",
  "Read the WAL internals paper."]`; row textContent additionally contains `"P0"`, `"P2"`,
  `"P3"` respectively (priority chip).
- `[data-ideas] [data-idea-id]`: exactly 3 rows, **set** of titles (created_at DESC ties on the
  frozen tick → id order → non-deterministic): `{"Voice memo inbox for quick capture.",
  "Weekly print digest of the learning wiki.",
  "Pair the boundary brief with a spoken audio version."}`.

### 2.3 Item 34 — snapshot report

Run 1: `(sprints, sprint_items, deferred_items, tickets, ideas, links, duplicates_skipped) ==
(1, 12, 9, 4, 20, 0, 0)`.

Run 2: `(0, 0, 0, 0, 0, 0, 46)` — the exact 46 = 1 sprint + 12 items + 9 deferred + 4 tickets
+ 20 ideas, every prior row re-matched by alias/title; `skipped` identical to run 1 (verified).

### 2.4 Item 34 — the seven skipped entries, verbatim (ready-to-paste block)

The implementer copies this block into the test file **unchanged** — it was verified `==`
against the importer's output in-process (both runs). Excerpts collapse newlines to spaces and
truncate at 120 chars; entry 5's excerpt contains U+2019 (’), entry 6's contains U+2013 (–)
and backticks. All lines are ruff-safe at line-length 100. Do not retype the strings.

```python
_R6 = "not the latest daily folder; only the latest day's workspace.md is imported (R6)"
_NO_FILE_MAP = "file has no migration mapping (only workspace.md is imported from a daily folder)"
_NO_SECTION_MAP = "section has no migration mapping (SPEC 12)"
_PREAMBLE = "preamble/rules prose; not importable items"

SNAPSHOT_SKIPPED = [
    {
        "source_file": "sprints/current/daily/2026-07-01",
        "heading": None,
        "reason": _R6,
        "excerpt": "",
    },
    {
        "source_file": "sprints/current/daily/2026-07-02",
        "heading": None,
        "reason": _R6,
        "excerpt": "",
    },
    {
        "source_file": "sprints/current/daily/2026-07-03/overview.md",
        "heading": None,
        "reason": _NO_FILE_MAP,
        "excerpt": (
            "# Overview Date: 2026-07-03 ## Brief Take Microphone support is functionally done: "
            "built, device-tested, and committed l"
        ),
    },
    {
        "source_file": "sprints/current/daily/2026-07-03/tracker.md",
        "heading": None,
        "reason": _NO_FILE_MAP,
        "excerpt": (
            "# Daily Date: 2026-07-03 ## Focus Close the microphone-support ship gap, then only "
            "return to the Vylo carryover that sti"
        ),
    },
    {
        "source_file": "sprints/current/daily/2026-07-03/workspace.md",
        "heading": "Necessary Calls",
        "reason": _NO_SECTION_MAP,
        "excerpt": (
            "- Choose today’s actual lane from the failsafe scaffold. - [assumption] "
            "No user-shaped Stage 2 direction was found after"
        ),
    },
    {
        "source_file": "deferred.md",
        "heading": None,
        "reason": _PREAMBLE,
        "excerpt": (
            "Important work not in the current sprint. Rules: - Group by project. - Use "
            "`P0`–`P3` priorities for strategic importance"
        ),
    },
    {
        "source_file": "ideas.md",
        "heading": None,
        "reason": _PREAMBLE,
        "excerpt": (
            "Interesting concepts, product ideas, and things worth exploring later. Rules: "
            "- Use this for ideas that are not yet conc"
        ),
    },
]
```

Assert `report["skipped"] == SNAPSHOT_SKIPPED` on **both** runs — dict equality covers order
and all four fields of every entry.

### 2.5 Item 34 — API ground truth

`GET /api/sprint/current`:

- `sprint` non-null; `name == "Sprint 2026-07-01 to 2026-07-12"`;
  `date_start == "2026-07-01"`; `date_end == "2026-07-12"`.
- Group sizes: `{"todo": 6, "active": 5, "done": 1, "blocked": 0, "deferred_next_sprint": 0}`.
- `groups["done"]` titles `== ["Ship waitlist mechanics."]` (trailing period included).

(Reference only — the full group membership, in case a failure needs diagnosing. todo:
"Complete onboarding in two stages: simple first, magical second." / "Extract more information
from the real-life pipeline." / "Get the welcome message working properly." / "Hook extra
meeting recorders into the plugin system." / "Re-evaluate stale-token sign-out recovery." /
"Shape Google Calendar integration as a major campaign." — active: "Add microphone support for
Vylo." / "Build durable personas into a compounding people / CRM-like layer." / "Build
notifications as its own system." / "Properly review and finish the PWA." / "Ship a small
product-accurate landing page." All 12 are project Vylo.)

`GET /api/tickets` → exactly 4; keyed by `alias` (list view carries `alias` and
`chat_session_key` — verified in `ticket_json`):

| alias | state | chat_session_key asserted |
|---|---|---|
| ticket-20260703-mic-prod-publish | needs_plan | — |
| ticket-20260701-app-typography-pass | needs_plan | — |
| ticket-20260702-landing-page-gate-1 | in_progress | `"20260702_114500_0ec57a"` exactly |
| ticket-20260702-durable-personas-first-exploration | in_progress | — |

Assert `set(by_alias) ==` these four aliases and `len(tickets) == 4` (no fifth ticket).

`GET /api/items?sprint_id=null` → exactly 9; assert the **set** of `(title, project)` pairs
(within-priority order is non-deterministic — see §4.3; priorities listed for reference):

| title | project | prio |
|---|---|---|
| Look into temporal processing for real-life interaction dates and assignment. | Vylo | P2 |
| Onboarding analytics / observability follow-ons. | Vylo | P2 |
| Cornell pilot and advisor maintenance. | Vylo | P3 |
| Finish design leftovers. | Vylo | P2 |
| Shape the Tribe founding-member loop. | Tribe | P2 |
| Tribe onboarding one-pager. | Tribe | P3 |
| SQLite internals deep-dive writeup. | Learning | P2 |
| Spaced-repetition pass over the negotiation notes. | Learning | P3 |
| Home office ergonomics batch. | Other | P3 |

`GET /api/ideas` → exactly 20.

UI presence (Board): needs_plan column titles include `"Publish microphone support."`;
in_progress column titles include
`"Landing page Gate 1: shippable enough to push `main`."` (backticks are literal characters in
the title — textContent comparison, membership not equality, so this stays a presence check
and not a duplicate of the API audit).

After run 2, re-assert via API: group sizes again `6/5/1/0/0` (12 items), `/api/tickets` len 4,
`/api/items?sprint_id=null` len 9, `/api/ideas` len 20, `/api/sprints` len 1.

---

## 3. Test skeletons — assertion order and selectors

### 3.1 `test_e33_seed_fixture_ui(server_factory, context_factory, open_page, cli)`

```
1. server = server_factory(fake_now="2026-06-11T12:00:00")
2. report = cli(server, "seed", "--source", FIXTURE_SRC)      # cli appends --json, asserts rc 0
3. assert _report_counts(report) == (1, 6, 3, 4, 3, 1, 0)
4. ctx = context_factory()

# Board — seed events preceded page-open → settled=True; the ready selector is data-bearing
# (the board section only exists after the fetch resolves).
5. page = open_page(ctx, server, "#/board", 'section[data-screen="board"]', settled=True)
6. for state, titles in BOARD_33:                              # 6 pairs from §2.2, incl. []
       assert _texts(page, f'[data-column="{state}"] [data-card] .entity-row-title') == titles

# Sprint — ready selector is the post-fetch header (also proves "No current sprint." did NOT
# render, i.e. the fake_now override worked).
7. page = open_page(ctx, server, "#/sprint",
                    '[data-screen="sprint"] .sprint-header', settled=True)
8. assert _texts(page, ".sprint-header .screen-title") == ["Sprint 2026-06-08 to 2026-06-21"]
9. assert _texts(page, ".sprint-header .sprint-dates") == ["2026-06-08 – 2026-06-21"]
10. for status, titles in SPRINT_GROUPS_33:                    # 5 ordered lists from §2.2
        assert _texts(
            page, f'[data-status-group="{status}"] [data-item-id] .entity-row-title'
        ) == titles
11. loose = _texts(page, '[data-loose] [data-ticket-id] .entity-row-title')
    assert len(loose) == 3 and set(loose) == LOOSE_33          # set: order nondeterministic

# Backlog — ready selector anchors on a landed backlog-item row; the ideas fetch is
# independent, so wait for its row count explicitly before reading.
12. page = open_page(ctx, server, "#/backlog",
                     '[data-screen="backlog"] [data-backlog-items] [data-item-id]',
                     settled=True)
13. _wait_count(page, "[data-ideas] [data-idea-id]", 3)
14. assert _texts(page, "[data-backlog-items] [data-item-id] .entity-row-title") == [
        "Rotate the leaked staging key.", "Tune the retrieval cache.",
        "Read the WAL internals paper.",
    ]                                                          # server-sorted by priority
15. rows = _texts(page, "[data-backlog-items] [data-item-id]")  # full row text incl. chips
    for row_text, prio in zip(rows, ["P0", "P2", "P3"]):
        assert prio in row_text
16. ideas = _texts(page, "[data-ideas] [data-idea-id] .entity-row-title")
    assert len(ideas) == 3 and set(ideas) == IDEAS_33
```

All expected values are hardcoded module constants — an independent oracle mirroring
`tests/unit/test_seed.py`; nothing is imported from tests/unit.

### 3.2 `test_e34_snapshot_migration(server, context_factory, open_page, cli, api)`

```
# Run 1 — report audit
1. report = cli(server, "seed", "--source", SNAPSHOT_SRC)
2. assert _report_counts(report) == (1, 12, 9, 4, 20, 0, 0)
3. assert report["skipped"] == SNAPSHOT_SKIPPED               # order + all 4 fields, 7 entries

# API audit — sprint
4. cur = api.get(server, "/api/sprint/current")
   assert cur["sprint"] is not None
   assert cur["sprint"]["name"] == "Sprint 2026-07-01 to 2026-07-12"
   assert cur["sprint"]["date_start"] == "2026-07-01"
   assert cur["sprint"]["date_end"] == "2026-07-12"
   groups = cur["groups"]
   assert {k: len(v) for k, v in groups.items()} == {
       "todo": 6, "active": 5, "done": 1, "blocked": 0, "deferred_next_sprint": 0}
   assert [i["title"] for i in groups["done"]] == ["Ship waitlist mechanics."]

# API audit — tickets (list view carries alias + chat_session_key)
5. tickets = api.get(server, "/api/tickets")["tickets"]
   assert len(tickets) == 4
   by_alias = {t["alias"]: t for t in tickets}
   assert set(by_alias) == set(SNAPSHOT_TICKET_STATES)
   for alias, state in SNAPSHOT_TICKET_STATES.items():
       assert by_alias[alias]["state"] == state
   assert (by_alias["ticket-20260702-landing-page-gate-1"]["chat_session_key"]
           == "20260702_114500_0ec57a")

# API audit — deferred + ideas
6. items = api.get(server, "/api/items?sprint_id=null")["items"]
   assert len(items) == 9
   assert {(i["title"], i["project"]) for i in items} == SNAPSHOT_DEFERRED   # §2.5 table
7. assert len(api.get(server, "/api/ideas")["ideas"]) == 20

# UI presence check (one Playwright open; membership, not a re-audit)
8. page = open_page(context_factory(), server, "#/board",
                    'section[data-screen="board"]', settled=True)
   assert "Publish microphone support." in _texts(
       page, '[data-column="needs_plan"] [data-card] .entity-row-title')
   assert "Landing page Gate 1: shippable enough to push `main`." in _texts(
       page, '[data-column="in_progress"] [data-card] .entity-row-title')

# Run 2 — idempotency, exact
9. report2 = cli(server, "seed", "--source", SNAPSHOT_SRC)
   assert _report_counts(report2) == (0, 0, 0, 0, 0, 0, 46)
   assert report2["skipped"] == SNAPSHOT_SKIPPED

# Post-run-2 API recount — nothing changed
10. cur2 = api.get(server, "/api/sprint/current")
    assert {k: len(v) for k, v in cur2["groups"].items()} == {
        "todo": 6, "active": 5, "done": 1, "blocked": 0, "deferred_next_sprint": 0}
    assert len(api.get(server, "/api/tickets")["tickets"]) == 4
    assert len(api.get(server, "/api/items?sprint_id=null")["items"]) == 9
    assert len(api.get(server, "/api/ideas")["ideas"]) == 20
    assert len(api.get(server, "/api/sprints")["sprints"]) == 1
```

---

## 4. Risk notes

1. **WS settled timing.** Both tests seed before opening any page, so every `open_page` uses
   `settled=True`: the fresh socket's `since=0` catch-up replays the seed events as one batch →
   one flush → screen re-render; open_page then re-anchors on the ready selector. Two
   refinements on top of the harness pattern: (a) ready selectors are chosen data-bearing
   (board section and sprint header exist only post-fetch; the backlog ready selector anchors
   on a landed item row), so no assertion can read a pre-fetch skeleton; (b) the backlog ideas
   list is a second, independent fetch — `_wait_count` (explicit `timeout=WAIT_MS`) gates it
   before reading. `__plannerDebug` counters are per-page (per `window`), so `settled=True`
   works for the second and third `open_page` on the same server. Data staleness is impossible
   here: seeding finished before any page opened, so pre-flush and post-flush renders fetch
   identical state — the waits only guard DOM-replacement races, and no test interacts with
   the page (reads only).

2. **T19 conftest concurrency.** The conftest change is exactly the two targeted Edits in §1.1
   (unique anchor strings, additive keyword-only-in-practice parameter, default path
   byte-identical). If an Edit's old string no longer matches at implementation time (T19
   landed a change on the same line), stop and re-read the file, then re-target the same
   minimal two-string edit — never rewrite the file, never touch any other line.

3. **Ideas / loose / deferred ordering.** Verified in-process: every seeded row shares a single
   `created_at` (the importer stamps rows with the frozen `PLAN_FAKE_NOW` clock tick), and ids
   carry random suffixes. Consequences: `/api/ideas` (`created_at DESC, id`) and the sprint
   loose list (`created_at, id`) degrade to random-id order → **set + count** assertions only.
   Backlog items sort by (priority rank, created_at, id): the fixture's three deferred items
   have distinct priorities (P0/P2/P3) → fully deterministic ordered assertion; the snapshot's
   nine are 5×P2 then 4×P3 — the block order is deterministic but within-block order is not →
   set of `(title, project)` pairs. Board columns in these tests never need intra-column order
   (e33 columns hold ≤ 1 card; e34 uses membership).

4. **Byte-exact excerpts.** The §2.4 block must be copied, not retyped: entry 5 contains a
   Unicode right single quote (U+2019), entry 6 an en dash (U+2013) plus backticks; excerpts
   are the importer's newline-collapsed, 120-char-truncated strings, several ending
   mid-word ("committed l", "that sti", "was found after", "strategic importance", "not
   conc"). The sprint-dates assertion in e33 likewise contains an en dash. The block was
   verified `==` against a live importer run; any re-wrap must keep implicit-concatenation
   boundaries exactly as given.

5. **Fixture-window clock.** Without the `fake_now` override, e33's Sprint screen renders
   "No current sprint." and the ready selector `.sprint-header` times out — a loud failure,
   not a silent skip. e34 must keep the default clock: 2026-07-04 sits inside the snapshot
   sprint's 2026-07-01 → 2026-07-12 window.

6. **Scorer fences.** Exactly one `test_e33_`-named and one `test_e34_`-named test across the
   whole e2e suite (today only test_flows_a.py exists, e22–e27; T19 adds e28–e32 — no
   collision). Helper names start with `_`. The file must not contain the skip-scan substrings
   listed in the ground-truth section, anywhere, including comments.

7. **CLI budget.** The `cli` fixture's 30 s subprocess timeout is ample: the snapshot import
   measured sub-second in-process, and the seed endpoint runs synchronously in the request.

---

## 5. Constraints (carried verbatim) and gate

- Only files touched: tests/e2e/test_seed_e2e.py (new) + the one additive conftest parameter.
  Nothing else.
- No pytest.skip/xfail/parametrize-on-anchored-names/empty tests/commented tests; helper
  functions get non-test names.
- Every Playwright wait carries an explicit timeout (follow test_flows_a.py's WAIT_MS pattern);
  no bare sleeps.
- The `cli` fixture already asserts rc=0 and parses JSON — use it as-is; the seed CLI resolves
  --source to an abspath in the CLI cwd (REPO_ROOT), which is correct for both sources.
- ruff line-length 100.
- Gate: `.venv/bin/pytest tests/e2e/test_seed_e2e.py -q` fully green headless twice.

Also: `migration/source-snapshot/` is read-only frozen data — the tests only pass its path to
the CLI; nothing writes into either source tree. Syntax/lint pass (`ruff check` on the two
touched files) before the gate; then the two green headless runs, then integration hands off
to full `./verify` to flip items 33–34.

---

## 6. Binding amendments (post codex plan review — orchestrator)

1. **Risk note 3 rationale corrected.** The importer stamps every row of a run with
   `now = int(time.time())` computed once at `src/planner/seed/importer.py:37` — wall clock,
   NOT the PLAN_FAKE_NOW frozen clock (the fake clock never reaches `seed_from_source`).
   The consequences and the assertion strategy in §4.3 are unchanged and remain binding:
   all rows of a run share one `created_at`, ids carry random suffixes, so `/api/ideas`,
   the sprint loose list, and within-priority deferred blocks are order-nondeterministic →
   set + count assertions exactly as specified; ordered assertions only where priorities
   are distinct. `created_at` itself is never asserted in either test.
   Everything else in the plan stands as written (codex: no ground-truth, selector, fence,
   or missing-assertion findings).
