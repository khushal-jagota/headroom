from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.chat import service as chat_service
from planner.chat.contracts import (
    ChatPendingClarification,
    ChatTurn,
    GatewayStatus,
    HumanChatObservation,
)
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import SCHEMA_VERSION, connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    EmployeeSessionHistory,
)
from planner.tickets.data import accept_proposal, create_ticket


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Clarify me.",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()
    return ticket.id


def _seed_worker_turn(
    db_path: Path,
    ticket_id: str,
    *,
    session_key: str = "employee-session-1",
    turn_id: str = "run_clarify",
    status: str = "running",
) -> str:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent_running_step', employee_session_id = ? "
            "WHERE id = ?",
            (session_key, ticket_id),
        )
        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'worker', 'worker_step', ?, 'doing', 'Working', "
            "'assistant', '', ?, NULL, 1, 1, NULL)",
            (turn_id, ticket_id, status, session_key),
        )
    finally:
        conn.close()
    return turn_id


def _turn_row(db_path: Path, turn_id: str) -> dict[str, object]:
    conn = connect(str(db_path))
    try:
        row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        assert row is not None
        return dict(row)
    finally:
        conn.close()


def _messages(db_path: Path, ticket_id: str) -> list[tuple[str, str]]:
    conn = connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
            (ticket_id,),
        ).fetchall()
        return [(str(row["role"]), str(row["text"])) for row in rows]
    finally:
        conn.close()


def _events(db_path: Path, entity_id: str, kind: str) -> list[dict[str, object]]:
    conn = connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT payload FROM events WHERE entity_id = ? AND kind = ? ORDER BY id",
            (entity_id, kind),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]
    finally:
        conn.close()


class ClarifyGateway:
    def __init__(self, *, fail: PlannerError | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str, str]] = []

    def status(self) -> GatewayStatus:
        return GatewayStatus(available=True)

    def read_employee_session_history(
        self, employee_session_id: str, ticket_id: str
    ) -> EmployeeSessionHistory:
        return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]:
        raise AssertionError("clarification answers must not start human chat turns")

    def interrupt(self, session_key: str, entity_id: str) -> None:
        raise AssertionError("clarification answers must not interrupt")

    def respond_to_clarification(
        self, session_key: str, entity_id: str, request_id: str, answer: str
    ) -> None:
        self.calls.append((session_key, entity_id, request_id, answer))
        if self.fail is not None:
            raise self.fail

    def catalog(self):
        raise AssertionError("catalog not used")


def test_create_schema_adds_pending_clarification_columns_to_existing_chat_turns(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "legacy.db"))
    conn.executescript(
        """
        CREATE TABLE chat_turns (
          id             TEXT PRIMARY KEY,
          entity_id      TEXT NOT NULL,
          origin         TEXT NOT NULL CHECK (origin IN ('human','worker','system')),
          mode           TEXT NOT NULL CHECK (mode IN ('message','command','worker_step')),
          status         TEXT NOT NULL
                         CHECK (status IN ('running','complete','errored','interrupted')),
          phase          TEXT NOT NULL
                         CHECK (phase IN ('queued','thinking','doing','responding','settled')),
          activity_label TEXT,
          output_role    TEXT NOT NULL CHECK (output_role IN ('assistant','system')),
          output_text    TEXT NOT NULL DEFAULT '',
          session_key    TEXT,
          error          TEXT,
          started_at     INTEGER NOT NULL,
          updated_at     INTEGER NOT NULL,
          completed_at   INTEGER
        );
        INSERT INTO chat_turns (
          id, entity_id, origin, mode, status, phase, activity_label, output_role,
          output_text, session_key, error, started_at, updated_at, completed_at
        ) VALUES (
          'run_existing', 't_existing', 'worker', 'worker_step', 'running',
          'doing', NULL, 'assistant', '', 'employee-session-1', NULL, 1, 1, NULL
        );
        """
    )

    create_schema(conn)

    columns = {
        str(row["name"]): row["notnull"]
        for row in conn.execute("PRAGMA table_info(chat_turns)")
    }
    assert columns["pending_clarification_request_id"] == 0
    assert columns["pending_clarification_question"] == 0
    assert columns["pending_clarification_choices"] == 0
    row = conn.execute(
        "SELECT pending_clarification_request_id, pending_clarification_question, "
        "pending_clarification_choices FROM chat_turns WHERE id = 'run_existing'"
    ).fetchone()
    assert tuple(row) == (None, None, None)
    create_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_worker_clarify_request_observation_is_serialized_on_chat_state(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_service.observe_worker_gateway_event(
            conn,
            ticket_id,
            turn_id,
            {
                "type": "clarify.request",
                "payload": {
                    "request_id": "clarify-1",
                    "question": "Pick a path.\nInclude constraints.",
                    "choices": ["Ship now", "Wait\nfor review"],
                },
            },
            now=2,
        )
        state = chat_service.state(conn, ticket_id)
    finally:
        conn.close()

    assert state.active_turn is not None
    assert state.active_turn.pending_clarification == ChatPendingClarification(
        request_id="clarify-1",
        question="Pick a path.\nInclude constraints.",
        choices=("Ship now", "Wait\nfor review"),
    )
    assert asdict(state)["active_turn"]["pending_clarification"] == {
        "request_id": "clarify-1",
        "question": "Pick a path.\nInclude constraints.",
        "choices": ("Ship now", "Wait\nfor review"),
    }
    assert _events(db_path, ticket_id, "chat_turn_updated")[-1] == {
        "turn_id": turn_id,
        "pending_clarification_request_id": "clarify-1",
    }


def test_clarification_answer_uses_same_live_session_then_mirrors_and_clears(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-1",
                question="Which API should we use?",
                choices=("REST", "GraphQL"),
            ),
            now=2,
        )
    finally:
        conn.close()
    gateway = ClarifyGateway()
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{ticket_id}/clarification-answer",
            json={"request_id": "clarify-1", "answer": "REST, because it already exists."},
        )
        state = client.get(f"/api/chat/{ticket_id}/state").json()

    assert response.status_code == 200
    assert gateway.calls == [
        ("employee-session-1", ticket_id, "clarify-1", "REST, because it already exists.")
    ]
    assert _messages(db_path, ticket_id) == [
        ("assistant", "Which API should we use?"),
        ("human", "REST, because it already exists."),
    ]
    assert state["active_turn"]["pending_clarification"] is None
    assert _turn_row(db_path, turn_id)["pending_clarification_request_id"] is None


def test_clarification_answer_failure_leaves_pending_state_and_history_unchanged(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-1",
                question="Need direction?",
                choices=(),
            ),
            now=2,
        )
    finally:
        conn.close()
    gateway = ClarifyGateway(
        fail=PlannerError(ErrorCode.gateway_offline, "clarify respond failed")
    )
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{ticket_id}/clarification-answer",
            json={"request_id": "clarify-1", "answer": "Use the smaller version."},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    row = _turn_row(db_path, turn_id)
    assert row["pending_clarification_request_id"] == "clarify-1"
    assert row["pending_clarification_question"] == "Need direction?"
    assert _messages(db_path, ticket_id) == []


def test_clarification_answer_rejects_stale_request_without_gateway_call(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-current",
                question="Current question?",
                choices=(),
            ),
            now=2,
        )
    finally:
        conn.close()
    gateway = ClarifyGateway()
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{ticket_id}/clarification-answer",
            json={"request_id": "clarify-old", "answer": "Old answer."},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert gateway.calls == []
    assert _turn_row(db_path, turn_id)["pending_clarification_request_id"] == "clarify-current"
    assert _messages(db_path, ticket_id) == []


def test_clarification_answer_does_not_clear_back_to_back_request(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-a",
                question="First?",
                choices=(),
            ),
            now=2,
        )
    finally:
        conn.close()

    class BackToBackGateway(ClarifyGateway):
        def respond_to_clarification(
            self, session_key: str, entity_id: str, request_id: str, answer: str
        ) -> None:
            super().respond_to_clarification(session_key, entity_id, request_id, answer)
            conn = connect(str(db_path))
            try:
                chat_data.record_pending_clarification(
                    conn,
                    turn_id,
                    entity_id=ticket_id,
                    clarification=ChatPendingClarification(
                        request_id="clarify-b",
                        question="Second?",
                        choices=("B1",),
                    ),
                    now=3,
                )
            finally:
                conn.close()

    gateway = BackToBackGateway()
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{ticket_id}/clarification-answer",
            json={"request_id": "clarify-a", "answer": "Answer A."},
        )
        state = client.get(f"/api/chat/{ticket_id}/state").json()

    assert response.status_code == 200
    assert _messages(db_path, ticket_id) == [
        ("assistant", "First?"),
        ("human", "Answer A."),
    ]
    assert state["active_turn"]["pending_clarification"] == {
        "request_id": "clarify-b",
        "question": "Second?",
        "choices": ["B1"],
    }


def test_clarification_answer_mirrors_after_worker_settles_on_accept(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-settle",
                question="Should the worker finish now?",
                choices=("Yes", "No"),
            ),
            now=2,
        )
    finally:
        conn.close()

    class SettlingGateway(ClarifyGateway):
        def respond_to_clarification(
            self, session_key: str, entity_id: str, request_id: str, answer: str
        ) -> None:
            super().respond_to_clarification(session_key, entity_id, request_id, answer)
            conn = connect(str(db_path))
            try:
                chat_data.finish_turn(
                    conn,
                    turn_id,
                    entity_id=ticket_id,
                    reply_text="",
                    output_role="assistant",
                    now=3,
                )
            finally:
                conn.close()

    gateway = SettlingGateway()
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{ticket_id}/clarification-answer",
            json={"request_id": "clarify-settle", "answer": "Yes"},
        )

    assert response.status_code == 200
    row = _turn_row(db_path, turn_id)
    assert row["status"] == "complete"
    assert row["pending_clarification_request_id"] is None
    assert _messages(db_path, ticket_id) == [
        ("assistant", "Should the worker finish now?"),
        ("human", "Yes"),
    ]


def test_every_worker_turn_settlement_clears_pending_clarification(tmp_path: Path) -> None:
    _app, db_path = _make_app(tmp_path)

    for index, settlement in enumerate(("settle", "finish", "fail"), start=1):
        ticket_id = _ticket(db_path)
        turn_id = _seed_worker_turn(
            db_path,
            ticket_id,
            session_key=f"employee-session-{index}",
            turn_id=f"run_settlement_{index}",
        )
        conn = connect(str(db_path))
        try:
            chat_data.record_pending_clarification(
                conn,
                turn_id,
                entity_id=ticket_id,
                clarification=ChatPendingClarification(
                    request_id=f"clarify-{settlement}",
                    question="This question must not survive settlement.",
                    choices=(),
                ),
                now=2,
            )
            if settlement == "settle":
                chat_data.settle_chat_turn(
                    conn,
                    turn_id,
                    entity_id=ticket_id,
                    status="interrupted",
                    reply_text="",
                    output_role="assistant",
                    error=None,
                    now=3,
                )
            elif settlement == "finish":
                chat_data.finish_turn(
                    conn,
                    turn_id,
                    entity_id=ticket_id,
                    reply_text="",
                    output_role="assistant",
                    now=3,
                )
            else:
                chat_data.fail_turn(
                    conn,
                    turn_id,
                    entity_id=ticket_id,
                    error="worker failed",
                    now=3,
                )
            row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
            assert row is not None
            assert row["pending_clarification_request_id"] is None
            assert row["pending_clarification_question"] is None
            assert row["pending_clarification_choices"] is None
        finally:
            conn.close()


def test_concurrent_duplicate_clarification_answers_reach_gateway_once(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    turn_id = _seed_worker_turn(db_path, ticket_id)
    conn = connect(str(db_path))
    try:
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=ticket_id,
            clarification=ChatPendingClarification(
                request_id="clarify-concurrent",
                question="Only answer once?",
                choices=("Yes",),
            ),
            now=2,
        )
    finally:
        conn.close()

    class ConcurrentGateway(ClarifyGateway):
        def __init__(self) -> None:
            super().__init__()
            self.second_call_entered = threading.Event()

        def respond_to_clarification(
            self, session_key: str, entity_id: str, request_id: str, answer: str
        ) -> None:
            super().respond_to_clarification(session_key, entity_id, request_id, answer)
            if len(self.calls) == 1:
                self.second_call_entered.wait(timeout=0.2)
            else:
                self.second_call_entered.set()

    gateway = ConcurrentGateway()
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]
    lifecycle = app.state.chat_turn_lifecycle

    def answer() -> ChatTurn | ErrorCode:
        try:
            return chat_service.answer_pending_clarification(
                lifecycle,
                ticket_id,
                request_id="clarify-concurrent",
                answer="Yes",
            )
        except PlannerError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(answer), pool.submit(answer))]

    assert len(gateway.calls) == 1
    assert sum(isinstance(result, ChatTurn) for result in results) == 1
    assert ErrorCode.not_found in results
    assert _messages(db_path, ticket_id) == [
        ("assistant", "Only answer once?"),
        ("human", "Yes"),
    ]
