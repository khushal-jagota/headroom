"""Drive the distinct ``new_worker`` workflow through the real data writers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
)
from planner.tickets.logic import fields_codec


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    conn = connect(str(tmp_path / "new-worker-type.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


def _worker_session_exists(conn: Connection, tid: str) -> bool:
    """Whether a worker was ever started on this Ticket. Naming a conversation is the
    whole of it: a Ticket names one when its first step runs, and never before."""
    row = conn.execute("SELECT conversation_id FROM tickets WHERE id = ?", (tid,)).fetchone()
    return row["conversation_id"] is not None


def test_new_worker_drives_to_done_via_real_writers(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Design a research worker",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="new_worker",
    )
    tid = ticket.id

    # Created at needs_kickoff scoped to the leading needs_kickoff ceiling, kickoff parked.
    assert ticket.stage == "needs_kickoff"
    assert ticket.ceiling == "needs_kickoff"
    assert ticket.worker_type == "new_worker"
    assert fields_codec.get_slot(ticket.fields, "kickoff").proposal is not None

    # Accept kickoff, expanding the ceiling all the way to needs_closeout -> advances to
    # needs_understanding (the first novel stage).
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_understanding"
    assert t.ceiling == "needs_closeout"

    # Understanding is paired, so its proposal parks for human approval even below the
    # ceiling. Approval advances to the existing worker-owned sequence.
    t = tickets_data.file_proposal(
        tmp_db,
        tid,
        field="understanding",
        body="understanding body",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_understanding"
    assert fields_codec.get_slot(t.fields, "understanding").proposal is not None
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="understanding",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_stages"
    assert fields_codec.get_slot(t.fields, "understanding").value == "understanding body"

    # Drive the worker-owned stages before Runtime Defaults.
    for field, next_state in (("stages", "needs_thinking"), ("thinking", "needs_runtime_defaults")):
        t = tickets_data.file_proposal(
            tmp_db, tid, field=field, body=f"{field} body", actor="agent", now=now
        )
        assert t.stage == next_state
        assert fields_codec.get_slot(t.fields, field).value == f"{field} body"
        assert fields_codec.get_slot(t.fields, field).proposal is None

    # Runtime Defaults is paired, so its proposal parks below the ceiling and approval
    # advances to Drafting. The approved value round-trips through the normal field slot.
    t = tickets_data.file_proposal(
        tmp_db,
        tid,
        field="runtime_defaults",
        body="codex / gpt-5.6-sol / medium",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_runtime_defaults"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").proposal is not None
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="runtime_defaults",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_drafting"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").value == (
        "codex / gpt-5.6-sol / medium"
    )

    t = tickets_data.file_proposal(
        tmp_db, tid, field="drafting", body="drafting body", actor="agent", now=now
    )
    assert t.stage == "needs_closeout"
    assert fields_codec.get_slot(t.fields, "drafting").value == "drafting body"

    # At needs_closeout (ceiling ==): propose closeout -> parks; then accept -> done.
    tickets_data.file_proposal(
        tmp_db, tid, field="closeout", body="closeout body", actor="agent", now=now
    )
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="closeout",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )

    # Exact final state: done, every worker field settled to its accepted value.
    assert t.stage == "done"
    assert fields_codec.get_slot(t.fields, "understanding").value == "understanding body"
    assert fields_codec.get_slot(t.fields, "stages").value == "stages body"
    assert fields_codec.get_slot(t.fields, "thinking").value == "thinking body"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").value == (
        "codex / gpt-5.6-sol / medium"
    )
    assert fields_codec.get_slot(t.fields, "drafting").value == "drafting body"
    assert fields_codec.get_slot(t.fields, "closeout").value == "closeout body"
    assert fields_codec.get_slot(t.fields, "closeout").proposal is None

    # No worker turn / session was created by the DATA-layer drive.
    assert _worker_session_exists(tmp_db, tid) is False


def test_new_worker_drives_to_dropped(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Abandon",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="new_worker",
    )
    # drop is the universal reserved bookend; it terminates a new_worker ticket at dropped.
    t = tickets_data.drop_ticket(tmp_db, ticket.id, actor="human", now=now)
    assert t.stage == "dropped"
    assert _worker_session_exists(tmp_db, ticket.id) is False
