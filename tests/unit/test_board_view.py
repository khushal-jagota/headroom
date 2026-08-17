"""Board view regressions."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.probe import (
    NEEDS_BETA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationStartRequest,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationTurnEnding,
    TurnEndedEventPayload,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core import links as core_links
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.contracts import LinkKind, Priority
from planner.days.data import add_day_ticket
from planner.projects.data import create_project
from planner.sprints.data import create_item, supervisor_agent_key
from planner.tickets.api import board as board_route
from planner.tickets.contracts import AtCap
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
    "blocked",
    "conversation_id",
    "waiting_to_closeout",
    "sprint_item_id",
    "sprint_item_title",
    "sprint_item_priority",
]

# The three signals the async route asks the conversation system for and appends after
# everything the pure view read out of the database.
_ROUTE_SIGNAL_KEYS = ["agent_working", "needs_me", "latest_turn_ended_sequence"]


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


def _put_every_ticket_on_today(conn: Connection) -> str:
    day_id = "day_2026-07-04"
    ticket_ids = conn.execute("SELECT id FROM tickets").fetchall()
    for row in ticket_ids:
        ticket_id = str(row["id"])
        exists = conn.execute(
            "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
            (day_id, ticket_id),
        ).fetchone()
        if exists is None:
            add_day_ticket(conn, day_id, ticket_id, 10)
    return day_id


def _board(conn: Connection) -> dict[str, Any]:
    return board_view(conn, day_id=_put_every_ticket_on_today(conn))


def _link_conversation(conn: Connection, ticket_id: str, conversation_id: str) -> None:
    conn.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?",
        (conversation_id, ticket_id),
    )


def _database_path(conn: Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()[2])


def _enriched_board(
    conn: Connection, conversations: InMemoryConversationSystem
) -> dict[str, Any]:
    _put_every_ticket_on_today(conn)
    clock = SimpleNamespace(now=lambda: datetime(2026, 7, 4, 12, 0))
    config = load_config(path=None, env={"PLAN_BOUNDARY_HOUR": "5"})
    return asyncio.run(
        board_route(
            conn, config, clock, conversations, ConversationStore(_database_path(conn))
        )
    )


def test_board_view_carries_only_the_requested_days_non_dropped_tickets(
    tmp_db: Connection,
) -> None:
    today = _ticket(tmp_db, "Today board ticket", 1)
    other_day = _ticket(tmp_db, "Other day ticket", 2)
    _ticket(tmp_db, "Backlog ticket", 3)
    add_day_ticket(tmp_db, "day_2026-07-04", today, 10)
    add_day_ticket(tmp_db, "day_2026-07-03", other_day, 10)

    board = board_view(tmp_db, day_id="day_2026-07-04")
    titles = {card["title"] for column in board["columns"] for card in column["cards"]}

    assert titles == {"Today board ticket"}


@pytest.mark.parametrize(
    ("now", "expected_day_id"),
    [
        (datetime(2026, 7, 5, 4, 59), "day_2026-07-04"),
        (datetime(2026, 7, 5, 5, 0), "day_2026-07-05"),
    ],
)
def test_board_route_resolves_the_5am_planning_day(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    expected_day_id: str,
) -> None:
    resolved_day_ids: list[str] = []

    def capture_board(_conn: Connection, *, day_id: str) -> dict[str, Any]:
        resolved_day_ids.append(day_id)
        return {"columns": [], "sprint_items": []}

    monkeypatch.setattr("planner.tickets.api.tickets_views.board_view", capture_board)
    clock = SimpleNamespace(now=lambda: now)
    config = load_config(path=None, env={"PLAN_BOUNDARY_HOUR": "5"})
    conversations = InMemoryConversationSystem()

    record = ConversationStore(_database_path(tmp_db))
    assert asyncio.run(board_route(tmp_db, config, clock, conversations, record)) == {
        "columns": [],
        "sprint_items": [],
    }
    assert resolved_day_ids == [expected_day_id]


def test_board_view_groups_parented_ticket_by_parent_item_project(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    standalone_project = create_project(
        tmp_db, name="Client Work", priority=Priority.P2, now=0
    )
    item = create_item(
        tmp_db,
        title="Parent item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    parented = _ticket(tmp_db, "Parented ticket", 1, sprint_item_id=item.id)
    standalone = _ticket(
        tmp_db, "Standalone ticket", 2, project_id=standalone_project.id
    )
    unprojected = _ticket(tmp_db, "Unprojected ticket", 3)
    for ticket_id in (parented, standalone, unprojected):
        add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)

    board = _board(tmp_db)
    cards = {
        card["title"]: card for column in board["columns"] for card in column["cards"]
    }

    assert cards["Parented ticket"]["project_id"] == "project_vylo"
    assert cards["Parented ticket"]["project"] == "Vylo"
    assert cards["Parented ticket"]["group_project_id"] == "project_vylo"
    assert cards["Parented ticket"]["group_project"] == "Vylo"
    assert cards["Parented ticket"]["sprint_item_id"] == item.id
    assert cards["Parented ticket"]["sprint_item_title"] == "Parent item"
    assert cards["Parented ticket"]["sprint_item_priority"] == "P3"
    assert cards["Standalone ticket"]["group_project_id"] == "project_client_work"
    assert cards["Standalone ticket"]["group_project"] == "Client Work"
    assert cards["Unprojected ticket"]["group_project_id"] is None
    assert cards["Unprojected ticket"]["group_project"] is None
    assert cards["Unprojected ticket"]["sprint_item_id"] is None
    assert cards["Unprojected ticket"]["sprint_item_title"] is None
    assert cards["Unprojected ticket"]["sprint_item_priority"] is None


def test_board_coding_card_keys_superset_and_columns_unchanged(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "Coding board ticket", 1)

    board = _board(tmp_db)

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
    assert card["priority"] == "P3"
    assert card["stage"] == "needs_kickoff"
    assert card["stage_label"] == "Kickoff"
    assert card["gating_field"] == "kickoff"
    assert card["gating_field_label"] == "Kickoff"
    assert card["is_done"] is False
    assert card["is_dropped"] is False
    assert card["blocked"] is False
    assert card["conversation_id"] is None
    assert card["waiting_to_closeout"] is False


@pytest.mark.parametrize(
    ("stage", "ticket_status", "ceiling", "at_cap", "expected"),
    [
        ("needs_closeout", "empty", "needs_closeout", "propose", True),
        ("needs_closeout", "empty", "needs_closeout", "stop", False),
        ("needs_closeout", "empty", "done", "stop", True),
        ("needs_success", "empty", "done", "stop", False),
        ("needs_closeout", "agent", "done", "stop", False),
    ],
)
def test_board_card_identifies_only_runnable_empty_closeout_tickets(
    tmp_db: Connection,
    stage: str,
    ticket_status: str,
    ceiling: str,
    at_cap: str,
    expected: bool,
) -> None:
    ticket_id = _ticket(tmp_db, "Closeout classification", 1)
    tmp_db.execute(
        "UPDATE tickets SET stage = ?, ticket_status = ?, ceiling = ?, at_cap = ? "
        "WHERE id = ?",
        (stage, ticket_status, ceiling, at_cap, ticket_id),
    )

    card = next(
        card for column in _board(tmp_db)["columns"] for card in column["cards"]
    )

    assert card["waiting_to_closeout"] is expected


def test_board_mixed_coding_probe_does_not_throw(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    _ticket(tmp_db, "Coding board ticket", 1)
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

    board = _board(tmp_db)

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


def test_board_card_carries_where_its_conversation_last_had_a_turn_end(
    tmp_db: Connection,
) -> None:
    ended = _ticket(tmp_db, "Turn has ended", 1)
    mid_turn = _ticket(tmp_db, "Turn still running", 2)
    unlinked = _ticket(tmp_db, "No conversation", 3)
    _link_conversation(tmp_db, ended, "conv-ended")
    _link_conversation(tmp_db, mid_turn, "conv-mid-turn")
    record = ConversationStore(_database_path(tmp_db))

    async def write_the_record() -> None:
        for conversation_id in ("conv-ended", "conv-mid-turn"):
            await record.create_conversation(
                ResolvedConversationStart(
                    conversation_id=conversation_id,
                    backend_key=ConversationBackendKey.codex,
                    model="a-model",
                    reasoning_effort=None,
                    role_materials=None,
                    workspace_folder=Path("/tmp"),
                    access=ConversationAccess.full,
                )
            )
            await record.append_event(
                conversation_id,
                AgentMessageEventPayload(
                    content=text_message_content("something happened")
                ),
            )
        await record.append_event(
            "conv-ended", TurnEndedEventPayload(ending=ConversationTurnEnding.completed)
        )

    asyncio.run(write_the_record())

    cards = {
        card["id"]: card
        for column in _enriched_board(tmp_db, InMemoryConversationSystem())["columns"]
        for card in column["cards"]
    }
    # The position of the turn's ending, not the position of the last thing written: the
    # agent message before it is at 1 and the ending itself at 2.
    assert cards[ended]["latest_turn_ended_sequence"] == 2
    # A conversation with output but no ending has nothing waiting for anybody yet.
    assert cards[mid_turn]["latest_turn_ended_sequence"] == 0
    # No conversation, no position — 0 is before every real one.
    assert cards[unlinked]["latest_turn_ended_sequence"] == 0


def test_board_card_carries_canonical_ticket_backend_error(
    tmp_db: Connection,
) -> None:
    ticket_id = _ticket(tmp_db, "Backend failure", 1)
    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'errored', backend_error = 'Provider exploded' "
        "WHERE id = ?",
        (ticket_id,),
    )
    board = _board(tmp_db)
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["backend_error"] == "Provider exploded"
    assert card["ticket_status"] == "errored"

    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'empty', backend_error = NULL WHERE id = ?",
        (ticket_id,),
    )
    board = _board(tmp_db)
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["backend_error"] is None
    assert card["ticket_status"] == "empty"


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

    board = _board(tmp_db)
    cards = {
        card["id"]: card for column in board["columns"] for card in column["cards"]
    }

    assert cards[kickoff_dependent]["stage"] == "needs_kickoff"
    assert cards[kickoff_dependent]["blocked"] is True
    assert cards[later_dependent]["stage"] == "needs_success"
    assert cards[later_dependent]["blocked"] is True


def test_board_route_reads_working_and_needs_me_from_the_conversation_system(
    tmp_db: Connection,
) -> None:
    running = _ticket(tmp_db, "Worker mid-turn", 1)
    asking = _ticket(tmp_db, "Worker asking permission", 2)
    questioning = _ticket(tmp_db, "Worker asking questions", 5)
    answered = _ticket(tmp_db, "Ask already answered", 3)
    unlinked = _ticket(tmp_db, "No conversation at all", 4)
    _link_conversation(tmp_db, running, "conv-running")
    _link_conversation(tmp_db, asking, "conv-asking")
    _link_conversation(tmp_db, answered, "conv-answered")
    _link_conversation(tmp_db, questioning, "conv-questioning")

    conversations = InMemoryConversationSystem()

    async def start_turns() -> None:
        for conversation_id in (
            "conv-running",
            "conv-asking",
            "conv-answered",
            "conv-questioning",
        ):
            await conversations.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id, model="a-model"
                )
            )
            await conversations.send(
                conversation_id,
                text_message_content("next step"),
                sender_label="loop",
            )

    asyncio.run(start_turns())
    conversations.raise_permission_ask("conv-asking")
    already_answered = conversations.raise_permission_ask("conv-answered")
    conversations.answer_permission_ask("conv-answered", already_answered, "allow")
    conversations.raise_user_input(
        "conv-questioning",
        (
            UserInputQuestion(
                question_id="q1",
                header="Choice",
                question="Which?",
                options=(UserInputOption(label="One", description="first"),),
                multi_select=False,
                allow_other=True,
            ),
        ),
    )

    board = _enriched_board(tmp_db, conversations)
    cards = {
        card["id"]: card for column in board["columns"] for card in column["cards"]
    }

    # The card carries the link the signals were read against, and the browser keys its
    # reply watermark by.
    assert cards[running]["conversation_id"] == "conv-running"
    assert cards[unlinked]["conversation_id"] is None

    # A running turn is the whole of agent_working; the ask is a separate signal.
    assert cards[running]["agent_working"] is True
    assert cards[running]["needs_me"] is False

    # A turn waiting on an ask is still running, and it needs the owner.
    assert cards[asking]["agent_working"] is True
    assert cards[asking]["needs_me"] is True

    # An answered ask is no longer pending; the turn it belongs to still runs.
    assert cards[answered]["agent_working"] is True
    assert cards[answered]["needs_me"] is False

    assert cards[questioning]["agent_working"] is True
    assert cards[questioning]["needs_me"] is True

    # No link means no conversation to ask about: both signals read false.
    assert cards[unlinked]["agent_working"] is False
    assert cards[unlinked]["needs_me"] is False

    # Both signals are appended after everything the pure view read.
    assert list(cards[running].keys())[-len(_ROUTE_SIGNAL_KEYS) :] == _ROUTE_SIGNAL_KEYS


def test_board_route_stops_working_when_the_conversation_turn_ends(
    tmp_db: Connection,
) -> None:
    ticket_id = _ticket(tmp_db, "Worker finishing", 1)
    _link_conversation(tmp_db, ticket_id, "conv-finishing")
    conversations = InMemoryConversationSystem()

    async def start_turn() -> None:
        await conversations.start_conversation(
            ConversationStartRequest(conversation_id="conv-finishing", model="a-model")
        )
        await conversations.send(
            "conv-finishing",
            text_message_content("next step"),
            sender_label="loop",
        )

    asyncio.run(start_turn())
    assert (
        _enriched_board(tmp_db, conversations)["columns"][0]["cards"][0][
            "agent_working"
        ]
        is True
    )

    conversations.complete_running_turn("conv-finishing")

    card = _enriched_board(tmp_db, conversations)["columns"][0]["cards"][0]
    assert card["agent_working"] is False
    assert card["needs_me"] is False


def test_completed_board_card_is_done_with_quiet_signals(tmp_db: Connection) -> None:
    ticket_id = _ticket(tmp_db, "Completed projection", 1)
    tmp_db.execute(
        "UPDATE tickets SET stage = 'done', ticket_status = 'empty' WHERE id = ?",
        (ticket_id,),
    )

    board = _board(tmp_db)
    card = next(card for column in board["columns"] for card in column["cards"])
    assert card["is_done"] is True


def test_board_sprint_items_carry_the_supervisors_own_conversation(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    spoken_to = create_item(
        tmp_db,
        title="Item with a supervisor mid-turn",
        project_id="project_vylo",
        clock=fake_clock,
    )
    silent = create_item(
        tmp_db,
        title="Item nobody has spoken to",
        project_id="project_vylo",
        clock=fake_clock,
    )
    tmp_db.execute(
        "UPDATE agents SET conversation_id = 'conv-supervisor' WHERE agent_key = ?",
        (supervisor_agent_key(spoken_to.id),),
    )
    for title, item_id in (("Spoken", spoken_to.id), ("Silent", silent.id)):
        add_day_ticket(
            tmp_db,
            "day_2026-07-04",
            _ticket(tmp_db, title, 1, sprint_item_id=item_id),
            10,
        )

    board = board_view(tmp_db, day_id="day_2026-07-04")

    # The Item's link is its own supervisor's, not any of its Tickets' workers'.
    expected: list[dict[str, object]] = [
        {
            "id": spoken_to.id,
            "created_at": spoken_to.created_at,
            "conversation_id": "conv-supervisor",
        },
        {
            "id": silent.id,
            "created_at": silent.created_at,
            "conversation_id": None,
        },
    ]
    assert board["sprint_items"] == sorted(expected, key=lambda entry: str(entry["id"]))


def test_board_route_marks_a_sprint_item_from_its_supervisor_conversation(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    item = create_item(
        tmp_db,
        title="Item with a working supervisor",
        project_id="project_vylo",
        clock=fake_clock,
    )
    _ticket(tmp_db, "Its ticket", 1, sprint_item_id=item.id)
    tmp_db.execute(
        "UPDATE agents SET conversation_id = 'conv-supervisor' WHERE agent_key = ?",
        (supervisor_agent_key(item.id),),
    )
    conversations = InMemoryConversationSystem()

    async def start_turn() -> None:
        await conversations.start_conversation(
            ConversationStartRequest(conversation_id="conv-supervisor", model="a-model")
        )
        await conversations.send(
            "conv-supervisor", text_message_content("look at this"), sender_label="user"
        )

    asyncio.run(start_turn())

    board = _enriched_board(tmp_db, conversations)

    # An Item is a row like a card, and it carries a card's three signals. Its turn end
    # is 0 here, because this conversation has a turn running and has ended none.
    assert board["sprint_items"] == [
        {
            "id": item.id,
            "created_at": item.created_at,
            "conversation_id": "conv-supervisor",
            "agent_working": True,
            "needs_me": False,
            "latest_turn_ended_sequence": 0,
        }
    ]


def test_board_sprint_item_carries_where_its_supervisor_last_had_a_turn_end(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    item = create_item(
        tmp_db,
        title="Item its supervisor has replied to",
        project_id="project_vylo",
        clock=fake_clock,
    )
    _ticket(tmp_db, "Its ticket", 1, sprint_item_id=item.id)
    tmp_db.execute(
        "UPDATE agents SET conversation_id = 'conv-supervisor' WHERE agent_key = ?",
        (supervisor_agent_key(item.id),),
    )
    record = ConversationStore(_database_path(tmp_db))

    async def write_the_record() -> None:
        await record.create_conversation(
            ResolvedConversationStart(
                conversation_id="conv-supervisor",
                backend_key=ConversationBackendKey.codex,
                model="a-model",
                reasoning_effort=None,
                role_materials=None,
                workspace_folder=Path("/tmp"),
                access=ConversationAccess.full,
            )
        )
        await record.append_event(
            "conv-supervisor",
            AgentMessageEventPayload(content=text_message_content("here is what I did")),
        )
        await record.append_event(
            "conv-supervisor",
            TurnEndedEventPayload(ending=ConversationTurnEnding.completed),
        )

    asyncio.run(write_the_record())

    board = _enriched_board(tmp_db, InMemoryConversationSystem())

    # The Item asks the same question a card asks, of its own supervisor's conversation.
    assert board["sprint_items"][0]["latest_turn_ended_sequence"] == 2


def test_board_sprint_items_are_empty_when_no_card_has_an_item(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "Unparented", 1)

    assert _board(tmp_db)["sprint_items"] == []
