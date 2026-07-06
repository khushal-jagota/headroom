"""E2E seed and snapshot migration — SPEC §18.3 items 33-34.

Item 33 seeds the synthetic fixture (tests/fixtures/planning-md) and asserts the
Board / Sprint / Backlog surfaces render the fixture's pinned titles and counts.
Item 34 seeds the frozen in-repo snapshot (migration/source-snapshot), asserts the
§12 ground-truth report and API state, checks two Board titles, then re-runs to prove
idempotency (zero new entities, every prior row re-matched). Standalone: imports
nothing from tests/unit; every expected value is a hardcoded oracle. Every Playwright
wait carries WAIT_MS; no bare sleeps.
"""

from __future__ import annotations

from playwright.sync_api import Page

WAIT_MS = 10_000
FIXTURE_SRC = "tests/fixtures/planning-md"          # cli cwd is REPO_ROOT; CLI abspaths it
SNAPSHOT_SRC = "migration/source-snapshot"

# --- item 33: fixture UI expectations (verified in-process, twice) ---------

BOARD_33 = [
    ("needs_success", ["Sketch the onboarding survey."]),
    ("needs_approach", ["Shape the export format."]),
    ("needs_plan", ["Cut the release branch."]),
    ("in_progress", ["Build the import pipeline."]),
    ("needs_review", []),
    ("done", []),
]

SPRINT_GROUPS_33 = [
    ("todo", ["Write the parser design note.", "Refit the garden shed."]),
    ("active", ["Build the import pipeline."]),
    ("done", ["Ship the fixture pack."]),
    ("blocked", ["Publish the beta changelog."]),
    ("deferred_next_sprint", ["Automate the weekly digest."]),
]

LOOSE_33 = {
    "Sketch the onboarding survey.",
    "Shape the export format.",
    "Cut the release branch.",
}

IDEAS_33 = {
    "Voice memo inbox for quick capture.",
    "Weekly print digest of the learning wiki.",
    "Pair the boundary brief with a spoken audio version.",
}

# --- item 34: snapshot report ground truth (verified in-process, twice) -----

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

SNAPSHOT_TICKET_STATES = {
    "ticket-20260703-mic-prod-publish": "needs_plan",
    "ticket-20260701-app-typography-pass": "needs_plan",
    "ticket-20260702-landing-page-gate-1": "in_progress",
    "ticket-20260702-durable-personas-first-exploration": "in_progress",
}

SNAPSHOT_DEFERRED = {
    ("Look into temporal processing for real-life interaction dates and assignment.", "Vylo"),
    ("Onboarding analytics / observability follow-ons.", "Vylo"),
    ("Cornell pilot and advisor maintenance.", "Vylo"),
    ("Finish design leftovers.", "Vylo"),
    ("Shape the Tribe founding-member loop.", "Tribe"),
    ("Tribe onboarding one-pager.", "Tribe"),
    ("SQLite internals deep-dive writeup.", "Learning"),
    ("Spaced-repetition pass over the negotiation notes.", "Learning"),
    ("Home office ergonomics batch.", "Other"),
}

SNAPSHOT_GROUP_SIZES = {
    "todo": 6,
    "active": 5,
    "done": 1,
    "blocked": 0,
    "deferred_next_sprint": 0,
}


def _report_counts(report: dict) -> tuple:
    """The MigrationReport count fields as a 7-tuple, in report-field order."""
    return (
        report["sprints"],
        report["sprint_items"],
        report["deferred_items"],
        report["tickets"],
        report["ideas"],
        report["links"],
        report["duplicates_skipped"],
    )


def _texts(page: Page, selector: str) -> list[str]:
    """textContent of every match — exact, so title equality holds (chips are siblings)."""
    return page.eval_on_selector_all(selector, "els => els.map(e => e.textContent)")


def _wait_count(page: Page, selector: str, n: int) -> None:
    page.wait_for_function(
        "a => document.querySelectorAll(a.sel).length === a.n",
        arg={"sel": selector, "n": n},
        timeout=WAIT_MS,
    )


def test_e33_seed_fixture_ui(server_factory, context_factory, open_page, cli):
    # Boot inside the fixture's sprint window (2026-06-08 -> 2026-06-21): 12:00 > 05:00
    # boundary hour -> planning date 2026-06-11, so the Sprint screen resolves a current
    # sprint instead of "No current sprint.".
    server = server_factory(fake_now="2026-06-11T12:00:00")
    report = cli(server, "seed", "--source", FIXTURE_SRC)
    assert _report_counts(report) == (1, 6, 3, 4, 3, 1, 0)

    ctx = context_factory()

    # Board — seed events preceded page-open, so settled=True; the board section only
    # exists after the /api/board fetch resolves.
    page = open_page(ctx, server, "#/board", 'section[data-screen="board"]', settled=True)
    for state, titles in BOARD_33:
        assert _texts(page, f'[data-column="{state}"] [data-card] .entity-row-title') == titles
    assert page.query_selector('[data-column="dropped"]') is None

    # Sprint — ready selector is the post-fetch header; its presence also proves the
    # fake_now override worked ("No current sprint." never rendered).
    page = open_page(
        ctx, server, "#/sprint", '[data-screen="sprint"] .sprint-header', settled=True
    )
    assert _texts(page, ".sprint-header .screen-title") == ["Sprint 2026-06-08 to 2026-06-21"]
    assert _texts(page, ".sprint-header .sprint-dates") == ["2026-06-08 – 2026-06-21"]
    for status, titles in SPRINT_GROUPS_33:
        assert (
            _texts(page, f'[data-status-group="{status}"] [data-item-id] .entity-row-title')
            == titles
        )
    loose = _texts(page, '[data-loose] [data-ticket-id] .entity-row-title')
    assert len(loose) == 3
    assert set(loose) == LOOSE_33

    # Backlog — ready selector anchors a landed backlog-item row. Rows are server-sorted
    # (priority → created_at), grouped by priority; the row itself no longer repeats the
    # priority (the group states it), so the P-label lives on the group label only.
    page = open_page(
        ctx,
        server,
        "#/backlog",
        '[data-screen="backlog"] [data-backlog-items] [data-item-id]',
        settled=True,
    )
    assert _texts(page, "[data-backlog-items] [data-item-id] .entity-row-title") == [
        "Rotate the leaked staging key.",
        "Tune the retrieval cache.",
        "Read the WAL internals paper.",
    ]
    for prio, title in [
        ("P0", "Rotate the leaked staging key."),
        ("P2", "Tune the retrieval cache."),
        ("P3", "Read the WAL internals paper."),
    ]:
        assert (
            _texts(page, f'[data-priority-group="{prio}"] [data-item-id] .entity-row-title')
            == [title]
        )
    # The row states project, not priority (priority is the group).
    p0_row = _texts(page, '[data-priority-group="P0"] [data-item-id]')
    assert len(p0_row) == 1
    assert "P0" not in p0_row[0]

    # Ideas — now its own top-level screen (#/ideas), a second independent fetch, so gate
    # its row count before reading.
    page = open_page(
        ctx,
        server,
        "#/ideas",
        '[data-screen="ideas"] [data-ideas] [data-idea-id]',
        settled=True,
    )
    _wait_count(page, "[data-ideas] [data-idea-id]", 3)
    ideas = _texts(page, "[data-ideas] [data-idea-id] .entity-row-title")
    assert len(ideas) == 3
    assert set(ideas) == IDEAS_33


def test_e34_snapshot_migration(server, context_factory, open_page, cli, api):
    # Run 1 — report audit.
    report = cli(server, "seed", "--source", SNAPSHOT_SRC)
    assert _report_counts(report) == (1, 12, 9, 4, 20, 0, 0)
    assert report["skipped"] == SNAPSHOT_SKIPPED

    # API audit — sprint (default clock 2026-07-04 sits inside 2026-07-01 -> 2026-07-12).
    cur = api.get(server, "/api/sprint/current")
    assert cur["sprint"] is not None
    assert cur["sprint"]["name"] == "Sprint 2026-07-01 to 2026-07-12"
    assert cur["sprint"]["date_start"] == "2026-07-01"
    assert cur["sprint"]["date_end"] == "2026-07-12"
    groups = cur["groups"]
    assert {k: len(v) for k, v in groups.items()} == SNAPSHOT_GROUP_SIZES
    assert [i["title"] for i in groups["done"]] == ["Ship waitlist mechanics."]

    # API audit — tickets (list view carries alias + chat_session_key).
    tickets = api.get(server, "/api/tickets")["tickets"]
    assert len(tickets) == 4
    by_alias = {t["alias"]: t for t in tickets}
    assert set(by_alias) == set(SNAPSHOT_TICKET_STATES)
    for alias, state in SNAPSHOT_TICKET_STATES.items():
        assert by_alias[alias]["state"] == state
    assert (
        by_alias["ticket-20260702-landing-page-gate-1"]["chat_session_key"]
        == "20260702_114500_0ec57a"
    )

    # API audit — deferred + ideas.
    items = api.get(server, "/api/items?sprint_id=null")["items"]
    assert len(items) == 9
    assert {(i["title"], i["project"]) for i in items} == SNAPSHOT_DEFERRED
    assert len(api.get(server, "/api/ideas")["ideas"]) == 20

    # UI presence check — one Playwright open, membership not a re-audit.
    page = open_page(
        context_factory(), server, "#/board", 'section[data-screen="board"]', settled=True
    )
    assert "Publish microphone support." in _texts(
        page, '[data-column="needs_plan"] [data-card] .entity-row-title'
    )
    assert "Landing page Gate 1: shippable enough to push `main`." in _texts(
        page, '[data-column="in_progress"] [data-card] .entity-row-title'
    )

    # Run 2 — idempotency, exact.
    report2 = cli(server, "seed", "--source", SNAPSHOT_SRC)
    assert _report_counts(report2) == (0, 0, 0, 0, 0, 0, 46)
    assert report2["skipped"] == SNAPSHOT_SKIPPED

    # Post-run-2 API recount — nothing changed.
    cur2 = api.get(server, "/api/sprint/current")
    assert {k: len(v) for k, v in cur2["groups"].items()} == SNAPSHOT_GROUP_SIZES
    assert len(api.get(server, "/api/tickets")["tickets"]) == 4
    assert len(api.get(server, "/api/items?sprint_id=null")["items"]) == 9
    assert len(api.get(server, "/api/ideas")["ideas"]) == 20
    assert len(api.get(server, "/api/sprints")["sprints"]) == 1
