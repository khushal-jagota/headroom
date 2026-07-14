"""Contract and behavior locks for the deep canonical human Chat turn."""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import fields
from pathlib import Path
from typing import Literal, get_args, get_type_hints

import pytest
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.chat.contracts import (
    ChatActivityObservation,
    ChattableEntityKind,
    ChatTurn,
    ChatTurnRequest,
    HumanChatCompletion,
    HumanChatObservation,
    HumanChatOutputDelta,
)
from planner.chat.service import ChatTurnLifecycle, _AdmittedHumanChatTurn
from planner.core.adapters.base import GatewayAdapter, HumanSessionKeyBinder
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.days import data as days_data
from planner.runtime.automatic_employee_step_eligibility import (
    is_eligible_for_automatic_employee_step,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, TicketStatus

ROOT = Path(__file__).resolve().parents[2]
PLANNING_DAY_ID = "day_2026-07-14"


def test_closed_human_transport_contract_is_exact() -> None:
    assert [field.name for field in fields(HumanChatOutputDelta)] == ["text"]
    assert [field.name for field in fields(HumanChatCompletion)] == ["text", "role"]
    assert set(get_args(HumanChatObservation)) == {
        ChatActivityObservation,
        HumanChatOutputDelta,
        HumanChatCompletion,
    }
    assert list(inspect.signature(GatewayAdapter.run_human_turn).parameters) == [
        "self",
        "session_key",
        "entity_id",
        "text",
        "mode",
        "bind_session_key",
        "image_paths",
        "require_existing_session",
    ]
    hints = get_type_hints(GatewayAdapter.run_human_turn)
    assert hints["bind_session_key"] == HumanSessionKeyBinder


def test_admitted_turn_and_lifecycle_interface_are_exact() -> None:
    assert _AdmittedHumanChatTurn.__dataclass_params__.frozen
    assert [field.name for field in fields(_AdmittedHumanChatTurn)] == [
        "entity_id",
        "entity_kind",
        "turn_id",
        "expected_session_key",
        "model_text",
        "mode",
        "image_paths",
        "gateway",
        "force_fresh_session",
        "require_existing_session",
    ]
    assert get_type_hints(_AdmittedHumanChatTurn) == {
        "entity_id": str,
        "entity_kind": ChattableEntityKind,
        "turn_id": str,
        "expected_session_key": str | None,
        "model_text": str,
        "mode": Literal["message", "command"],
        "image_paths": tuple[Path, ...],
        "gateway": GatewayAdapter,
        "force_fresh_session": bool,
        "require_existing_session": bool,
    }
    public_methods = {
        name
        for name, value in vars(ChatTurnLifecycle).items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == {
        "start_human_turn",
        "recover_human_turn",
        "pause_active_turn",
    }
    assert list(inspect.signature(ChatTurnLifecycle._execute_turn).parameters) == [
        "self",
        "admitted",
    ]
    assert "force_fresh_session" not in {field.name for field in fields(ChatTurnRequest)}

    service_path = ROOT / "src/planner/chat/service.py"
    service_tree = ast.parse(
        service_path.read_text(encoding="utf-8"), filename=str(service_path)
    )
    admitted_calls = [
        node
        for node in ast.walk(service_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_AdmittedHumanChatTurn"
    ]
    assert len(admitted_calls) == 2
    force_values = {
        ast.unparse(keyword.value)
        for call in admitted_calls
        for keyword in call.keywords
        if keyword.arg == "force_fresh_session"
    }
    strict_values = {
        ast.unparse(keyword.value)
        for call in admitted_calls
        for keyword in call.keywords
        if keyword.arg == "require_existing_session"
    }
    assert force_values == {
        "mode == 'command' and request.text == '/new'",
        "False",
    }
    assert strict_values == {"False", "True"}


def test_api_composition_and_employee_separation_have_one_thin_owner() -> None:
    api_path = ROOT / "src/planner/chat/api.py"
    api_tree = ast.parse(api_path.read_text(encoding="utf-8"), filename=str(api_path))
    api_functions = {
        node.name: node
        for node in api_tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    for name, lifecycle_method in (
        ("start_chat_turn", "start_human_turn"),
        ("start_chief_message", "start_human_turn"),
        ("pause_chat_turn", "pause_active_turn"),
    ):
        function = api_functions[name]
        source = ast.unparse(function)
        assert f"chat_turn_lifecycle.{lifecycle_method}" in source
        for forbidden in ("conn_factory", "adapters", "gateway", "clock", "db_path"):
            assert forbidden not in source

    server_path = ROOT / "src/planner/core/server.py"
    server_tree = ast.parse(
        server_path.read_text(encoding="utf-8"), filename=str(server_path)
    )
    lifecycle_calls = [
        node
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ChatTurnLifecycle"
    ]
    assert len(lifecycle_calls) == 1
    provider = next(
        keyword.value
        for keyword in lifecycle_calls[0].keywords
        if keyword.arg == "gateway_provider"
    )
    assert isinstance(provider, ast.Lambda)
    assert ast.unparse(provider.body) == "app.state.adapters.gateway"

    service_path = ROOT / "src/planner/chat/service.py"
    service_tree = ast.parse(
        service_path.read_text(encoding="utf-8"), filename=str(service_path)
    )
    parents: dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(service_tree) for child in ast.iter_child_nodes(parent)
    }
    thread_calls = [
        node
        for node in ast.walk(service_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "threading"
        and node.func.attr == "Thread"
    ]
    assert len(thread_calls) == 1
    owner: ast.AST = thread_calls[0]
    while owner in parents and not isinstance(owner, ast.FunctionDef):
        owner = parents[owner]
    assert isinstance(owner, ast.FunctionDef)
    assert owner.name == "_launch_execution"

    runner_source = (ROOT / "src/planner/runtime/employee_step_runner.py").read_text(
        encoding="utf-8"
    )
    assert "ChatTurnLifecycle" not in runner_source
    assert "run_human_turn" not in runner_source
    assert "run_ticket_step" in runner_source


def test_http_routes_marshal_exactly_one_request_into_the_lifecycle(tmp_path: Path) -> None:
    db = tmp_path / "thin-routes.db"
    conn = connect(str(db))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        build_adapters(config),
        lambda: connect(str(db)),
    )

    class RecordingLifecycle:
        def __init__(self) -> None:
            self.starts: list[tuple[str, ChatTurnRequest]] = []
            self.pauses: list[str] = []

        def start_human_turn(
            self, entity_id: str, request: ChatTurnRequest
        ) -> ChatTurn:
            self.starts.append((entity_id, request))
            return ChatTurn(
                "run_recorded",
                entity_id,
                "human",
                request.mode,
                "running",
                "thinking",
                "Thinking",
                "assistant",
                "",
                None,
                None,
                1,
                1,
                None,
            )

        def pause_active_turn(self, entity_id: str) -> ChatTurn:
            self.pauses.append(entity_id)
            return ChatTurn(
                "run_paused",
                entity_id,
                "human",
                "message",
                "interrupted",
                "settled",
                None,
                "assistant",
                "partial",
                "session",
                None,
                1,
                2,
                2,
            )

    lifecycle = RecordingLifecycle()
    app.state.chat_turn_lifecycle = lifecycle
    with TestClient(app) as client:
        generic = client.post(
            "/api/chat/day_2026-07-14/turns",
            json={
                "text": "  generic text  ",
                "mode": "message",
                "image_references": ["/files/chats/day_2026-07-14/image.png"],
            },
        )
        chief = client.post(
            "/api/messages/chief",
            json={"text": "  chief text\nwith original spacing  "},
        )
        paused = client.post("/api/chat/t_demo/pause")

    assert generic.status_code == 200
    assert chief.status_code == 200
    assert paused.status_code == 200
    assert lifecycle.starts == [
        (
            "day_2026-07-14",
            ChatTurnRequest(
                text="generic text",
                mode="message",
                image_references=("/files/chats/day_2026-07-14/image.png",),
            ),
        ),
        (
            "agent_panels_chief_of_staff",
            ChatTurnRequest(text="  chief text\nwith original spacing  "),
        ),
    ]
    assert lifecycle.pauses == ["t_demo"]


def test_deleted_human_turn_compatibility_surface_is_absent() -> None:
    contracts = (ROOT / "src/planner/chat/contracts.py").read_text(encoding="utf-8")
    service = (ROOT / "src/planner/chat/service.py").read_text(encoding="utf-8")
    adapter = (ROOT / "src/planner/core/adapters/base.py").read_text(encoding="utf-8")
    assert "ChatStreamChunk" not in contracts
    assert "def stream(" not in adapter
    assert "on_session_key" not in adapter
    for deleted in (
        "def _run_human_turn(",
        "def pause_turn(",
        "def _unix_now(",
        "def _reject_if_ticket_worker_running(",
    ):
        assert deleted not in service


def _running_human_ticket_turn(tmp_path: Path):
    conn = connect(str(tmp_path / "human-binding.db"))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Bind human session",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    turn = chat_data.start_turn(
        conn,
        ticket.id,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="hello",
        output_role="assistant",
        phase="thinking",
        activity_label="Thinking",
        now=2,
    )
    return conn, ticket.id, turn.id


def _session_events(conn, entity_id: str) -> list[tuple[str, dict[str, object]]]:
    return [
        (str(row["kind"]), json.loads(str(row["payload"])))
        for row in conn.execute(
            "SELECT kind, payload FROM events WHERE entity_id = ? "
            "AND kind IN ('employee_session_changed', 'chat_turn_updated') ORDER BY id",
            (entity_id,),
        )
    ]


def test_binding_writer_is_atomic_idempotent_and_rotates_once(tmp_path: Path) -> None:
    conn, ticket_id, turn_id = _running_human_ticket_turn(tmp_path)
    try:
        first = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key=None,
            candidate_session_key="session-one",
            force_fresh_session=False,
            now=3,
        )
        assert first == "session-one"
        assert tuple(
            conn.execute(
                "SELECT tickets.employee_session_id, chat_turns.session_key "
                "FROM tickets JOIN chat_turns ON chat_turns.entity_id = tickets.id "
                "WHERE tickets.id = ?",
                (ticket_id,),
            ).fetchone()
        ) == ("session-one", "session-one")
        first_events = _session_events(conn, ticket_id)
        assert [kind for kind, _ in first_events] == [
            "employee_session_changed",
            "chat_turn_updated",
        ]

        repeated = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key="session-one",
            candidate_session_key="session-one",
            force_fresh_session=False,
            now=4,
        )
        assert repeated == "session-one"
        assert _session_events(conn, ticket_id) == first_events

        rotated = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key="session-one",
            candidate_session_key="session-two",
            force_fresh_session=False,
            now=5,
        )
        assert rotated == "session-two"
        assert [kind for kind, _ in _session_events(conn, ticket_id)] == [
            "employee_session_changed",
            "chat_turn_updated",
            "employee_session_changed",
            "chat_turn_updated",
        ]
    finally:
        conn.close()


def test_binding_adopts_ordinary_winner_but_force_fresh_replaces_it(tmp_path: Path) -> None:
    conn, ticket_id, turn_id = _running_human_ticket_turn(tmp_path)
    try:
        conn.execute(
            "UPDATE tickets SET employee_session_id = 'concurrent-winner' WHERE id = ?",
            (ticket_id,),
        )
        adopted = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key=None,
            candidate_session_key="losing-candidate",
            force_fresh_session=False,
            now=3,
        )
        assert adopted == "concurrent-winner"
        assert _session_events(conn, ticket_id)[-1] == (
            "chat_turn_updated",
            {"turn_id": turn_id, "can_pause": True},
        )
        assert not any(
            payload.get("employee_session_id") == "losing-candidate"
            for _, payload in _session_events(conn, ticket_id)
        )

        forced = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key="concurrent-winner",
            candidate_session_key="fresh-new-session",
            force_fresh_session=True,
            now=4,
        )
        assert forced == "fresh-new-session"
        assert tuple(
            conn.execute(
                "SELECT tickets.employee_session_id, chat_turns.session_key "
                "FROM tickets JOIN chat_turns ON chat_turns.entity_id = tickets.id "
                "WHERE tickets.id = ?",
                (ticket_id,),
            ).fetchone()
        ) == ("fresh-new-session", "fresh-new-session")

        conn.execute(
            "UPDATE tickets SET employee_session_id = 'winner-after-forced-bind' WHERE id = ?",
            (ticket_id,),
        )
        ordinary_next_report = chat_data.bind_human_turn_session(
            conn,
            turn_id,
            entity_kind="ticket",
            entity_id=ticket_id,
            expected_session_key="fresh-new-session",
            candidate_session_key="later-candidate",
            force_fresh_session=False,
            now=5,
        )
        assert ordinary_next_report == "winner-after-forced-bind"
        assert tuple(
            conn.execute(
                "SELECT tickets.employee_session_id, chat_turns.session_key "
                "FROM tickets JOIN chat_turns ON chat_turns.entity_id = tickets.id "
                "WHERE tickets.id = ?",
                (ticket_id,),
            ).fetchone()
        ) == ("winner-after-forced-bind", "winner-after-forced-bind")
        assert not any(
            payload.get("employee_session_id") == "later-candidate"
            for _, payload in _session_events(conn, ticket_id)
        )
    finally:
        conn.close()


def test_binding_rolls_back_entity_turn_and_events_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, ticket_id, turn_id = _running_human_ticket_turn(tmp_path)
    real_append_event = chat_data.append_event

    def fail_after_entity_update(*args: object, **kwargs: object) -> object:
        if (
            len(args) >= 3
            and getattr(args[2], "value", args[2]) == "chat_turn_updated"
        ):
            raise RuntimeError("event write failed")
        return real_append_event(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(chat_data, "append_event", fail_after_entity_update)
    try:
        with pytest.raises(RuntimeError, match="event write failed"):
            chat_data.bind_human_turn_session(
                conn,
                turn_id,
                entity_kind="ticket",
                entity_id=ticket_id,
                expected_session_key=None,
                candidate_session_key="rolled-back",
                force_fresh_session=False,
                now=3,
            )
        assert tuple(
            conn.execute(
                "SELECT tickets.employee_session_id, chat_turns.session_key "
                "FROM tickets JOIN chat_turns ON chat_turns.entity_id = tickets.id "
                "WHERE tickets.id = ?",
                (ticket_id,),
            ).fetchone()
        ) == (None, None)
        assert _session_events(conn, ticket_id) == []
    finally:
        conn.close()


def test_late_binding_cannot_mutate_a_settled_turn(tmp_path: Path) -> None:
    conn, ticket_id, turn_id = _running_human_ticket_turn(tmp_path)
    try:
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
        before = _session_events(conn, ticket_id)
        with pytest.raises(PlannerError, match="no longer running"):
            chat_data.bind_human_turn_session(
                conn,
                turn_id,
                entity_kind="ticket",
                entity_id=ticket_id,
                expected_session_key=None,
                candidate_session_key="too-late",
                force_fresh_session=False,
                now=4,
            )
        assert _session_events(conn, ticket_id) == before
        assert conn.execute(
            "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()[0] is None
    finally:
        conn.close()


def _eligible_ticket_database(tmp_path: Path, name: str) -> tuple[str, str]:
    db = str(tmp_path / name)
    conn = connect(db)
    create_schema(conn)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Admission race",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=2,
            next_ceiling="needs_success",
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(conn, PLANNING_DAY_ID, ticket.id, 3)
        return db, ticket.id
    finally:
        conn.close()


def _claim(db: str, ticket_id: str):
    conn = connect(db)
    try:
        return tickets_data.claim_automatic_employee_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: PLANNING_DAY_ID,
            eligibility_check=is_eligible_for_automatic_employee_step,
            now=10,
        )
    finally:
        conn.close()


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _BlockingHumanGateway:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.run_calls: list[tuple[str | None, str, str, str]] = []
        self.interrupt_calls: list[tuple[str, str]] = []

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
        del image_paths, require_existing_session
        self.run_calls.append((session_key, entity_id, text, mode))
        bind_session_key("human-session")
        self.entered.set()
        assert self.release.wait(5.0)
        yield HumanChatCompletion("human reply", "assistant")

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self.interrupt_calls.append((session_key, entity_id))


class _InterruptRecordingGateway:
    def __init__(self) -> None:
        self.interrupt_calls: list[tuple[str, str]] = []

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
        del (
            session_key,
            entity_id,
            text,
            mode,
            bind_session_key,
            image_paths,
            require_existing_session,
        )
        raise AssertionError("human transport must not run")
        yield

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self.interrupt_calls.append((session_key, entity_id))


def test_human_admission_wins_atomic_race_and_claim_has_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, ticket_id = _eligible_ticket_database(tmp_path, "human-wins.db")
    gateway = _BlockingHumanGateway()
    lifecycle = ChatTurnLifecycle(
        lambda: connect(db),
        lambda: gateway,  # type: ignore[arg-type]
        lambda: 10,
        db,
    )
    admission_holds_write_lock = threading.Event()
    release_admission = threading.Event()
    real_start = chat_data.start_turn_in_transaction

    def start_while_holding_lock(
        conn: sqlite3.Connection,
        entity_id: str,
        **kwargs: object,
    ):
        admission_holds_write_lock.set()
        assert release_admission.wait(5.0)
        return real_start(conn, entity_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(chat_data, "start_turn_in_transaction", start_while_holding_lock)
    human_result: list[object] = []
    claim_result: list[object] = []

    def admit_human() -> None:
        try:
            human_result.append(
                lifecycle.start_human_turn(ticket_id, ChatTurnRequest(text="hello"))
            )
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            human_result.append(exc)

    human_thread = threading.Thread(target=admit_human)
    claim_thread = threading.Thread(target=lambda: claim_result.append(_claim(db, ticket_id)))
    human_thread.start()
    assert admission_holds_write_lock.wait(5.0)
    claim_thread.start()
    release_admission.set()
    human_thread.join(5.0)
    claim_thread.join(5.0)
    assert not human_thread.is_alive()
    assert not claim_thread.is_alive()
    assert not isinstance(human_result[0], BaseException)
    assert claim_result == [None]
    assert gateway.entered.wait(5.0)

    conn = connect(db)
    try:
        assert tickets_data.read_ticket(conn, ticket_id).ticket_status is TicketStatus.empty
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turns WHERE entity_id = ? AND status = 'running'",
            (ticket_id,),
        ).fetchone()[0] == 1
        assert tuple(
            conn.execute(
                "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
                (ticket_id,),
            ).fetchall()[-1]
        ) == ("human", "hello")
        assert not conn.execute(
            "SELECT 1 FROM events WHERE entity_id = ? AND kind = 'ticket_status_changed' "
            "AND json_extract(payload, '$.ticket_status') = 'agent_running_step'",
            (ticket_id,),
        ).fetchone()
    finally:
        conn.close()
        gateway.release.set()
    assert _wait_until(lambda: len(gateway.run_calls) == 1)


def test_employee_claim_wins_atomic_race_and_human_admission_has_no_side_effects(
    tmp_path: Path,
) -> None:
    db, ticket_id = _eligible_ticket_database(tmp_path, "employee-wins.db")
    gateway = _InterruptRecordingGateway()
    lifecycle = ChatTurnLifecycle(
        lambda: connect(db),
        lambda: gateway,  # type: ignore[arg-type]
        lambda: 10,
        db,
    )
    claim_holds_write_lock = threading.Event()
    release_claim = threading.Event()
    claim_result: list[object] = []
    human_result: list[object] = []

    def eligibility_while_holding_lock(
        conn: sqlite3.Connection,
        ticket: object,
        *,
        planning_day_id: str,
        worker_type_definition: object,
    ) -> bool:
        claim_holds_write_lock.set()
        assert release_claim.wait(5.0)
        return is_eligible_for_automatic_employee_step(
            conn,
            ticket,  # type: ignore[arg-type]
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,  # type: ignore[arg-type]
        )

    def claim_employee() -> None:
        conn = connect(db)
        try:
            claim_result.append(
                tickets_data.claim_automatic_employee_step(
                    conn,
                    ticket_id,
                    planning_day_id_resolver=lambda: PLANNING_DAY_ID,
                    eligibility_check=eligibility_while_holding_lock,  # type: ignore[arg-type]
                    now=10,
                )
            )
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            claim_result.append(exc)
        finally:
            conn.close()

    def admit_human() -> None:
        try:
            human_result.append(
                lifecycle.start_human_turn(ticket_id, ChatTurnRequest(text="too late"))
            )
        except BaseException as exc:
            human_result.append(exc)

    claim_thread = threading.Thread(target=claim_employee)
    human_thread = threading.Thread(target=admit_human)
    claim_thread.start()
    assert claim_holds_write_lock.wait(5.0)
    human_thread.start()
    release_claim.set()
    claim_thread.join(5.0)
    human_thread.join(5.0)
    assert not claim_thread.is_alive()
    assert not human_thread.is_alive()
    assert not isinstance(claim_result[0], BaseException)
    assert isinstance(human_result[0], PlannerError)
    assert "worker is already running" in str(human_result[0])

    conn = connect(db)
    try:
        assert (
            tickets_data.read_ticket(conn, ticket_id).ticket_status
            is TicketStatus.agent_running_step
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turns WHERE entity_id = ?", (ticket_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE entity_id = ?", (ticket_id,)
        ).fetchone()[0] == 0
    finally:
        conn.close()
    assert gateway.interrupt_calls == []


@pytest.mark.parametrize("first_status", ["complete", "interrupted"])
def test_first_settlement_wins_between_completion_and_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_status: str,
) -> None:
    conn, ticket_id, turn_id = _running_human_ticket_turn(tmp_path)
    try:
        chat_data.record_turn_activity(
            conn,
            turn_id,
            entity_id=ticket_id,
            observation=ChatActivityObservation("thinking", "Reasoning", "running"),
            now=3,
        )
        chat_data.append_turn_output(
            conn,
            turn_id,
            entity_id=ticket_id,
            delta="partial",
            now=4,
        )
    finally:
        conn.close()

    first_holds_write_lock = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    real_append_event = chat_data.append_event

    def append_event_while_first_holds_lock(*args: object, **kwargs: object) -> object:
        result = real_append_event(*args, **kwargs)  # type: ignore[arg-type]
        if (
            threading.current_thread().name == "first-settlement"
            and len(args) >= 3
            and getattr(args[2], "value", args[2]) == "chat_turn_finished"
        ):
            first_holds_write_lock.set()
            assert release_first.wait(5.0)
        return result

    monkeypatch.setattr(chat_data, "append_event", append_event_while_first_holds_lock)
    results: dict[str, object] = {}
    second_status = "interrupted" if first_status == "complete" else "complete"

    def settle(name: str, status: str, now: int) -> None:
        thread_conn = connect(str(tmp_path / "human-binding.db"))
        try:
            if name == "second":
                second_started.set()
            results[name] = chat_data.settle_chat_turn(
                thread_conn,
                turn_id,
                entity_id=ticket_id,
                status=status,  # type: ignore[arg-type]
                reply_text="answer" if status == "complete" else "",
                output_role="assistant",
                error=None,
                now=now,
            )
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            results[name] = exc
        finally:
            thread_conn.close()

    first_thread = threading.Thread(
        target=settle,
        args=("first", first_status, 5),
        name="first-settlement",
    )
    second_thread = threading.Thread(
        target=settle,
        args=("second", second_status, 6),
        name="second-settlement",
    )
    first_thread.start()
    assert first_holds_write_lock.wait(5.0)
    second_thread.start()
    assert second_started.wait(5.0)
    release_first.set()
    first_thread.join(5.0)
    second_thread.join(5.0)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not isinstance(results["first"], BaseException)
    assert results["second"] == results["first"]
    settled = results["first"]
    assert isinstance(settled, ChatTurn)
    assert settled.status == first_status
    assert settled.output_text == (
        "answer" if first_status == "complete" else "partial"
    )

    conn = connect(str(tmp_path / "human-binding.db"))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turn_activity_entries WHERE turn_id = ?", (turn_id,)
        ).fetchone()[0] == 0
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT role, text FROM chat_messages WHERE turn_id = ? AND role = 'assistant'",
                (turn_id,),
            ).fetchall()
        ] == [("assistant", settled.output_text)]
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id = ? AND kind = 'chat_turn_finished'",
            (ticket_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_worker_origin_pause_settles_visible_turn_without_changing_worker_state_or_wake(
    tmp_path: Path,
) -> None:
    db, ticket_id = _eligible_ticket_database(tmp_path, "worker-pause.db")
    conn = connect(db)
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent_running_step' WHERE id = ?",
            (ticket_id,),
        )
        turn = chat_data.start_turn(
            conn,
            ticket_id,
            origin="worker",
            mode="worker_step",
            visible_role="worker",
            visible_text="",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=4,
        )
        chat_data.attach_session_key(
            conn,
            turn.id,
            entity_id=ticket_id,
            session_key="worker-session",
            now=5,
        )
        chat_data.append_turn_output(
            conn,
            turn.id,
            entity_id=ticket_id,
            delta="worker partial",
            now=6,
        )
        status_events_before = conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id = ? AND kind = 'ticket_status_changed'",
            (ticket_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    gateway = _InterruptRecordingGateway()
    lifecycle = ChatTurnLifecycle(
        lambda: connect(db),
        lambda: gateway,  # type: ignore[arg-type]
        lambda: 7,
        db,
    )
    settled = lifecycle.pause_active_turn(ticket_id)

    assert settled.status == "interrupted"
    assert settled.output_text == "worker partial"
    assert gateway.interrupt_calls == [("worker-session", ticket_id)]
    conn = connect(db)
    try:
        assert (
            tickets_data.read_ticket(conn, ticket_id).ticket_status
            is TicketStatus.agent_running_step
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id = ? AND kind = 'ticket_status_changed'",
            (ticket_id,),
        ).fetchone()[0] == status_events_before
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT role, text FROM chat_messages WHERE turn_id = ?", (turn.id,)
            ).fetchall()
        ] == [("assistant", "worker partial")]
    finally:
        conn.close()

    assert list(inspect.signature(ChatTurnLifecycle.__init__).parameters) == [
        "self",
        "conn_factory",
        "gateway_provider",
        "now",
        "db_path",
    ]
