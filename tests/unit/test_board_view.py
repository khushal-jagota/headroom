"""Board view regressions."""

from __future__ import annotations

from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    NEEDS_BETA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core import links as core_links
from planner.core.contracts import LinkKind, Priority
from planner.days.data import add_day_ticket
from planner.projects.data import create_project
from planner.sprints.data import create_item
from planner.tickets.contracts import AtCap
from planner.tickets.conversation_projection import TicketConversationProjection
from planner.tickets.data import accept_proposal, create_ticket
from planner.tickets.views import board_view
from planner.worker_types.contracts import WorkerTypeDefinition

# The 7 coding columns, in order — a coding-only board reproduces exactly these,
# even the empty ones, with no appended column.
_CODING_COLUMN_ORDER = [
    "needs_kickoff",
    "needs_success",
    "needs_approach",
    "needs_plan",
    "needs_implementation",
    "needs_closeout",
    "done",
]

# The card keys present before t_tt04a, in order — the enrichment keys are APPENDED
# after these, so these keep their exact positions (payload superset).
_PRE_EXISTING_CARD_KEYS = [
    "id",
    "title",
    "priority",
    "deadline",
    "project_id",
    "project",
    "group_project_id",
    "group_project",
    "activity_at",
    "has_pending_proposal",
    "ticket_status",
]

_ENRICHMENT_CARD_KEYS = [
    "backend_error",
    "worker_type",
    "employee_backend",
    "stage",
    "stage_label",
    "gating_field",
    "gating_field_label",
    "is_done",
    "is_dropped",
    "workspace_dot_state",
    "blocked",
]


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def _ticket(
    conn: Connection,
    title: str,
    now: int,
    *,
    project_id: str | None = None,
    sprint_item_id: str | None = None,
) -> str:
    ticket = create_ticket(
        conn,
        worker_type="coding",
        title=title,
        actor="human",
        now=now,
        title_max_chars=200,
        project_id=project_id,
        priority=Priority.P3,
        sprint_item_id=sprint_item_id,
    )
    return ticket.id


def test_board_view_only_returns_tickets_on_requested_day(tmp_db: Connection) -> None:
    today = _ticket(tmp_db, "Today board ticket", 1)
    other_day = _ticket(tmp_db, "Other day ticket", 2)
    _ticket(tmp_db, "Backlog ticket", 3)
    add_day_ticket(tmp_db, "day_2026-07-04", today, 10)
    add_day_ticket(tmp_db, "day_2026-07-03", other_day, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    titles = [card["title"] for column in board["columns"] for card in column["cards"]]

    assert titles == ["Today board ticket"]


def test_board_view_groups_parented_ticket_by_parent_item_project(
    tmp_db: Connection, fake_clock
) -> None:
    standalone_project = create_project(tmp_db, name="Client Work", now=0)
    item = create_item(
        tmp_db,
        title="Parent item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    parented = _ticket(tmp_db, "Parented ticket", 1, sprint_item_id=item.id)
    standalone = _ticket(tmp_db, "Standalone ticket", 2, project_id=standalone_project.id)
    unprojected = _ticket(tmp_db, "Unprojected ticket", 3)
    for ticket_id in (parented, standalone, unprojected):
        add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    cards = {card["title"]: card for column in board["columns"] for card in column["cards"]}

    assert cards["Parented ticket"]["project_id"] is None
    assert cards["Parented ticket"]["project"] is None
    assert cards["Parented ticket"]["group_project_id"] == "project_vylo"
    assert cards["Parented ticket"]["group_project"] == "Vylo"
    assert cards["Standalone ticket"]["group_project_id"] == "project_client_work"
    assert cards["Standalone ticket"]["group_project"] == "Client Work"
    assert cards["Unprojected ticket"]["group_project_id"] is None
    assert cards["Unprojected ticket"]["group_project"] is None


def test_board_coding_card_keys_superset_and_columns_unchanged(tmp_db: Connection) -> None:
    ticket_id = _ticket(tmp_db, "Coding board ticket", 1)
    add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")

    # A coding-only board reproduces the 7 coding columns, in order, no appended column.
    assert [column["stage"] for column in board["columns"]] == _CODING_COLUMN_ORDER

    card = board["columns"][0]["cards"][0]
    keys = list(card.keys())
    # Pre-existing keys keep their exact order and positions; enrichment is appended.
    assert keys[: len(_PRE_EXISTING_CARD_KEYS)] == _PRE_EXISTING_CARD_KEYS
    assert keys[len(_PRE_EXISTING_CARD_KEYS) :] == _ENRICHMENT_CARD_KEYS

    # The coding enrichment values for a fresh needs_kickoff card.
    assert card["worker_type"] == "coding"
    assert card["employee_backend"] == "codex"
    assert card["stage"] == "needs_kickoff"
    assert card["stage_label"] == "Kickoff"
    assert card["gating_field"] == "kickoff"
    assert card["gating_field_label"] == "Kickoff"
    assert card["is_done"] is False
    assert card["is_dropped"] is False
    assert card["workspace_dot_state"] == "needs_attention"
    assert card["blocked"] is False


def test_board_mixed_coding_probe_does_not_throw(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    coding_id = _ticket(tmp_db, "Coding board ticket", 1)
    probe = create_ticket(
        tmp_db,
        title="Probe board ticket",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="probe",
    )
    # Advance probe off needs_kickoff into needs_alpha (a state coding never has).
    accept_proposal(
        tmp_db,
        probe.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling=NEEDS_BETA,
        at_cap=AtCap.propose,
    )
    add_day_ticket(tmp_db, "day_2026-07-04", coding_id, 10)
    add_day_ticket(tmp_db, "day_2026-07-04", probe.id, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")

    states = [column["stage"] for column in board["columns"]]
    # Coding's 7 columns first, in order; probe's needs_alpha appended after.
    assert states[: len(_CODING_COLUMN_ORDER)] == _CODING_COLUMN_ORDER
    assert states[len(_CODING_COLUMN_ORDER) :] == ["needs_alpha"]

    probe_cards = [
        card
        for column in board["columns"]
        for card in column["cards"]
        if card["worker_type"] == "probe"
    ]
    assert len(probe_cards) == 1
    probe_card = probe_cards[0]
    # Probe card decoded against PROBE_WORKER_TYPE_DEFINITION — enrichment is probe-correct.
    assert probe_card["stage"] == "needs_alpha"
    assert probe_card["stage_label"] == "Alpha"
    assert probe_card["gating_field"] == "alpha"
    assert probe_card["gating_field_label"] == "Alpha"
    assert probe_card["is_done"] is False
    assert probe_card["is_dropped"] is False


def test_board_card_uses_the_canonical_workspace_dot_result(tmp_db: Connection) -> None:
    ticket_id = _ticket(tmp_db, "Active projection", 1)
    add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    TicketConversationProjection(db_path, now=lambda: 2).record_activity(ticket_id, "thinking")

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["workspace_dot_state"] == "active"


def test_board_exception_comes_only_from_canonical_ticket_backend_error(
    tmp_db: Connection,
) -> None:
    ticket_id = _ticket(tmp_db, "Backend failure", 1)
    add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)
    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'errored', backend_error = 'Provider exploded' "
        "WHERE id = ?",
        (ticket_id,),
    )
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    TicketConversationProjection(db_path, now=lambda: 2).record_activity(ticket_id, "interrupted")

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["backend_error"] == "Provider exploded"
    assert card["workspace_dot_state"] == "exceptional"

    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'empty', backend_error = NULL WHERE id = ?",
        (ticket_id,),
    )
    board = board_view(tmp_db, 21, day_id="day_2026-07-04")
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["workspace_dot_state"] == "needs_attention"


def test_board_cards_expose_active_incoming_blocking_without_changing_real_stage(
    tmp_db: Connection,
) -> None:
    blocker = _ticket(tmp_db, "Shared blocker", 1)
    kickoff_dependent = _ticket(tmp_db, "Kickoff dependent", 2)
    later_dependent = _ticket(tmp_db, "Later dependent", 3)
    accept_proposal(
        tmp_db,
        later_dependent,
        field="kickoff",
        actor="human",
        now=4,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )
    core_links.add_link(tmp_db, blocker, kickoff_dependent, LinkKind.blocks, 5)
    core_links.add_link(tmp_db, blocker, later_dependent, LinkKind.blocks, 5)
    for ticket_id in (kickoff_dependent, later_dependent):
        add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 6)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    cards = {card["id"]: card for column in board["columns"] for card in column["cards"]}

    assert cards[kickoff_dependent]["stage"] == "needs_kickoff"
    assert cards[kickoff_dependent]["blocked"] is True
    assert cards[later_dependent]["stage"] == "needs_success"
    assert cards[later_dependent]["blocked"] is True


def test_completed_board_card_uses_settled_workspace_dot(tmp_db: Connection) -> None:
    ticket_id = _ticket(tmp_db, "Completed projection", 1)
    add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)
    tmp_db.execute(
        "UPDATE tickets SET stage = 'done', ticket_status = 'empty' WHERE id = ?",
        (ticket_id,),
    )

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["workspace_dot_state"] == "settled"
