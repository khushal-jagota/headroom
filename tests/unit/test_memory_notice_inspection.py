"""Memory inspection exposes permission and evidence through existing read authority."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.cli import http
from planner.cli.main import main
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.storage import ConversationStore
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime import worker_memory
from planner.tickets import data, views


def _seed(conn: sqlite3.Connection) -> str:
    ticket = data.create_ticket(
        conn,
        worker_type="coding",
        title="Synthetic inspection",
        brief="Synthetic task",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    for conversation_id in ("former", "current", "other"):
        conn.execute(
            "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, "
            "access, created_at) VALUES (?, 'codex', '/synthetic', 'full', 1)",
            (conversation_id,),
        )
    for conversation_id in ("former", "current"):
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket.id),
        )
    conn.execute("UPDATE tickets SET conversation_id='current' WHERE id=?", (ticket.id,))
    for owner, conversation_id, sender in (
        (ticket.id, "former", "worker_step_memory_notice_original"),
        ("t_other", "other", "worker_step_memory_notice_other"),
    ):
        conn.execute(
            "INSERT INTO worker_memory_notice_reservations VALUES (?, ?, 7, ?, 10)",
            (owner, conversation_id, sender),
        )
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES (?, 8, 'prompt', ?, 11)",
            (conversation_id, json.dumps({"sender_message_id": sender, "text": "SECRET"})),
        )
    conn.execute(
        "INSERT INTO worker_memory_notice_holds VALUES (?, 'current', NULL, 9, "
        "'legacy_missing_event')",
        (ticket.id,),
    )
    conn.commit()
    return ticket.id


def test_detail_inspection_is_read_only_and_retains_original_associations(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket_id = _seed(tmp_db)
    before = tuple(tmp_db.iterdump())
    record_hash = data.ticket_record_sha256(tmp_db, ticket_id)
    denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
    tmp_db.set_authorizer(
        lambda action, _one, _two, _db, _source: (
            sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        )
    )
    detail = views.ticket_detail(tmp_db, ticket_id, 12)
    inspection = detail["memory_notice_inspection"]
    assert inspection == worker_memory.inspect_memory_notices(tmp_db, ticket_id).model_dump(
        mode="json"
    )
    assert inspection["reservations"][0]["key"]["conversation_id"] == "former"
    assert inspection["holds"][0]["context_compacted_sequence"] is None
    assert inspection["evidence"] == [
        {
            "conversation_id": "former",
            "sequence": 8,
            "kind": "prompt",
            "sender_message_id": "worker_step_memory_notice_original",
        }
    ]
    serialized = json.dumps(inspection)
    assert "SECRET" not in serialized
    assert "t_other" not in serialized
    for inferred_fact in ("delivered", "answered", "admission", "running"):
        assert inferred_fact not in serialized
    assert detail["record_sha256"] == record_hash
    assert tuple(tmp_db.iterdump()) == before
    assert not tmp_db.in_transaction


@pytest.mark.parametrize("actor", ["owner", "worker", "other_worker", "chief"])
@pytest.mark.parametrize("worker_self", [False, True])
def test_inspection_preserves_existing_ticket_read_authority(
    tmp_path: Path, actor: str, worker_self: bool
) -> None:
    db_path = tmp_path / "inspection.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket_id = _seed(conn)
    conn.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    app.state.conversation = SimpleNamespace(store=ConversationStore(str(db_path)))
    app.state.conversation_system = InMemoryConversationSystem()
    headers = {} if actor == "owner" else {"X-Plan-Actor": actor}
    if actor in ("worker", "other_worker"):
        headers = {
            "X-Plan-Actor": "worker",
            "X-Plan-Ticket-ID": ticket_id if actor == "worker" else "t_other",
        }
    client = TestClient(app)
    response = client.get(
        f"/api/tickets/{ticket_id}/worker-self" if worker_self else "/api/tickets",
        params={} if worker_self else {"detail": "full", "id": ticket_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["memory_notice_inspection"]["reservations"][0]["key"] == {
        "ticket_id": ticket_id,
        "conversation_id": "former",
        "context_compacted_sequence": 7,
    }


@pytest.mark.parametrize(
    "arguments,path",
    [
        (["ticket", "show", "t_example"], "/api/tickets"),
        (["ticket", "show"], "/api/tickets/t_example/worker-self"),
        (["worker", "my-ticket"], "/api/tickets/t_example/worker-self"),
    ],
)
@pytest.mark.parametrize("expand", [False, True])
def test_cli_inspection_part_is_optional_and_read_only(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], path: str, expand: bool
) -> None:
    monkeypatch.setenv("PLAN_TICKET_ID", "t_example")
    inspection: dict[str, list[Any]] = {"reservations": [], "holds": [], "evidence": []}
    calls: list[tuple[str, str]] = []

    def send(method: str, request_path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path))
        if request_path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        assert method == "GET" and request_path == path
        return {
            "id": "t_example",
            "worker_type": "coding",
            "field_values": {},
            "memory_notice_inspection": inspection,
        }

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(
        main, [*arguments, *(["memory_notice_inspection"] if expand else []), "--json"]
    )
    assert result.exit_code == 0, result.output
    projection = json.loads(result.output)
    if expand:
        assert projection["parts"] == {
            "memory_notice_inspection": {"value": inspection, "proposal": None}
        }
    else:
        assert "parts" not in projection
        assert "memory_notice_inspection" in projection["manifest"]
    assert all(method == "GET" for method, _path in calls)
