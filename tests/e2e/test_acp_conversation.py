"""One real route -> official ACP SDK -> child-process conversation proof."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pytest
from acp.schema import AgentMessageChunk, SessionNotification, TextContentBlock
from acp.transports import default_environment
from fastapi.testclient import TestClient
from playwright.sync_api import Browser
from starlette.testclient import WebSocketTestSession
from websockets.sync.client import connect as connect_websocket

from planner.conversation.backend_catalog import (
    EmployeeBackendCatalog,
    static_employee_backend_registration,
)
from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.composition import ConversationTestOptions
from planner.conversation.employee_registry import ConversationIngressSource
from planner.conversation.hermes_turn_strategy import (
    HERMES_SUMMARY_PREFIX,
    HermesAcpTurnStrategy,
)
from planner.conversation.hub import (
    REPLAY_UNAVAILABLE_CLOSE_REASON,
    SLOW_CONSUMER_CLOSE_REASON,
)
from planner.conversation.sdk_child import SdkAcpEmployeeChildFactory
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.days.logic.dates import resolve_day_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap
from planner.worker_context import data as worker_context_data
from planner.worker_types.configuration import (
    PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS,
    build_employee_runtime_definitions,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT = REPOSITORY_ROOT / "tests/support/acp_scripted_agent.py"
E2E_SERVER = REPOSITORY_ROOT / "tests/support/acp_e2e_server.py"


def test_production_employee_backend_catalog_is_hermes_codex_claude() -> None:
    assert (
        PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog.registered_backend_keys()
        == ("hermes", "codex", "claude")
    )


class _Strategy:
    def classify_replay(
        self, _binding: Any, replay: tuple[Any, ...], _boundaries: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        return replay

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class _GenerationScopedSdkChildFactory:
    def __init__(
        self,
        source_definition: AgentBackendDefinition,
        replacement_definition: AgentBackendDefinition,
    ) -> None:
        self._source = SdkAcpEmployeeChildFactory(source_definition)
        self._replacement = SdkAcpEmployeeChildFactory(replacement_definition)

    async def create(
        self,
        employee: Any,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> Any:
        factory = self._source if generation == 1 else self._replacement
        return await factory.create(
            employee,
            generation,
            update_ingress,
            permission_callback,
            death_callback,
        )


def _definition(
    *,
    backend_key: str = "hermes",
    observes_compaction: bool = False,
) -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key=backend_key,
        argv=(sys.executable, str(SCRIPTED_AGENT)),
        inherited_environment_names=(
            *tuple(default_environment()),
            "ACP_TEST_PROMPT_AUDIT_PATH",
            "ACP_TEST_LATE_SEND_AUDIT_PATH",
            "ACP_TEST_PROMPT_AUDIT_UDP",
            "ACP_TEST_PROMPT_RELEASE_UDP",
            "ACP_TEST_MALFORMED_DURING_PROMPT",
            "ACP_TEST_DURABLE_STORE_PATH",
            "ACP_TEST_FORK_SESSION",
        ),
        environment_overrides=(),
        expected_agent_name="panels-scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(False, observes_compaction),
        reverse_service_capabilities=ReverseServiceCapabilities(False, False, True),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_Strategy(),
    )


def _compaction_definition() -> AgentBackendDefinition:
    definition = _definition(observes_compaction=True)
    return AgentBackendDefinition(
        backend_key=definition.backend_key,
        argv=definition.argv,
        inherited_environment_names=definition.inherited_environment_names,
        environment_overrides=definition.environment_overrides,
        expected_agent_name=definition.expected_agent_name,
        expected_agent_version=definition.expected_agent_version,
        turn_capabilities=definition.turn_capabilities,
        reverse_service_capabilities=definition.reverse_service_capabilities,
        working_directory_resolver=definition.working_directory_resolver,
        turn_strategy=HermesAcpTurnStrategy(
            concurrent_prompt=None,
            capture_updates=None,
        ),
    )


def _application(
    config: Any,
    clock: Any,
    definition: AgentBackendDefinition,
    *,
    browser_capacity: int = 128,
    reset_buffer_byte_limit: int = 1_048_576,
    child_factory: Any | None = None,
) -> Any:
    factory = child_factory if child_factory is not None else SdkAcpEmployeeChildFactory(definition)
    return _application_with_backends(
        config, clock, ((definition, factory),), browser_capacity, reset_buffer_byte_limit
    )


def _application_with_backends(
    config: Any,
    clock: Any,
    backends: tuple[tuple[AgentBackendDefinition, Any], ...],
    browser_capacity: int = 128,
    reset_buffer_byte_limit: int = 1_048_576,
) -> Any:
    catalog = EmployeeBackendCatalog(
        tuple(
            static_employee_backend_registration(definition, factory)
            for definition, factory in backends
        )
    )
    return create_app(
        config,
        clock,
        lambda: connect(config.db_path),
        conversation_test_options=ConversationTestOptions(
            employee_runtime_definitions=build_employee_runtime_definitions(catalog),
            browser_capacity=browser_capacity,
            reset_buffer_byte_limit=reset_buffer_byte_limit,
        ),
    )


def _seed_eligible_ticket(
    conn: Any,
    clock: Any,
    boundary_hour: int,
    *,
    title: str = "[ACP_TEST_WAIT_FOR_CANCEL] automatic vertical",
    employee_backend: str | None = None,
    employee_runtime_definitions: Any | None = None,
) -> Any:
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title=title,
        actor="test",
        now=clock.now_unix(),
        title_max_chars=200,
        employee_backend=employee_backend,
        employee_runtime_definitions=employee_runtime_definitions,
    )
    tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    days_data.add_day_ticket(
        conn,
        resolve_day_id("today", clock.now(), boundary_hour),
        ticket.id,
        clock.now_unix(),
    )
    return ticket


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as available:
        available.bind(("127.0.0.1", 0))
        return int(available.getsockname()[1])


def _wait_for_http(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited during boot with {process.returncode}")
        try:
            if httpx.get(url, timeout=0.5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"server did not become ready: {url}")


def _start_acp_backend(
    *, db_path: str, logs_dir: Path, port: int, audit_path: Path | None = None
) -> subprocess.Popen[bytes]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("PLAN_")
    }
    environment.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(logs_dir),
            "PLAN_PORT": str(port),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    if audit_path is not None:
        environment["ACP_TEST_PROMPT_AUDIT_PATH"] = str(audit_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.support.acp_e2e_server"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_http(f"http://127.0.0.1:{port}/api/meta", process)
    return process


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@contextmanager
def _vite_vertical_server(tmp_path: Path) -> Iterator[tuple[str, str, Path, Path]]:
    database_path = tmp_path / "browser.db"
    db_path = str(database_path)
    audit_path = tmp_path / "browser-prompt-audit.jsonl"
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP browser vertical",
            actor="test",
            now=1,
            title_max_chars=200,
        )
    backend_port = _free_port()
    vite_port = _free_port()
    base_environment = {
        name: value for name, value in os.environ.items() if not name.startswith("PLAN_")
    }
    backend = _start_acp_backend(
        db_path=db_path,
        logs_dir=tmp_path / "logs",
        port=backend_port,
        audit_path=audit_path,
    )
    vite = subprocess.Popen(
        [
            str(REPOSITORY_ROOT / "web/node_modules/.bin/vite"),
            "--host",
            "127.0.0.1",
            "--port",
            str(vite_port),
        ],
        cwd=REPOSITORY_ROOT / "web",
        env={
            **base_environment,
            "PLAN_WEB_BACKEND": f"http://127.0.0.1:{backend_port}",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http(f"http://127.0.0.1:{vite_port}/", vite)
        yield f"http://127.0.0.1:{vite_port}", ticket.id, audit_path, database_path
    finally:
        for process in (vite, backend):
            _stop_process(process)


def _receive_until(
    websocket: WebSocketTestSession,
    predicate: Any,
    *,
    limit: int = 64,
) -> list[dict[str, Any]]:
    envelopes: list[dict[str, Any]] = []
    for _ in range(limit):
        envelope = websocket.receive_json()
        envelopes.append(envelope)
        if predicate(envelope):
            return envelopes
    raise AssertionError(f"conversation predicate not reached: {envelopes!r}")


def _is_ready(envelope: dict[str, Any]) -> bool:
    return envelope["type"] == "connection" and envelope["payload"]["state"] == "ready"


def _is_idle(envelope: dict[str, Any]) -> bool:
    return envelope["type"] == "activity" and envelope["payload"]["state"] == "idle"


def _assert_compacted_replay(
    replay: list[dict[str, Any]],
    *,
    session_id: str,
    binding_generation: int,
    boundary_id: str,
    trigger: str,
    marker_free: bool = False,
) -> dict[str, Any]:
    assert not any(
        item["type"] == "acp_session_update"
        and item["acpSessionId"] == session_id
        and HERMES_SUMMARY_PREFIX
        in str(item["payload"]["update"].get("content", {}).get("text", ""))
        for item in replay
    )
    before = next(
        item
        for item in replay
        if item["type"] == "acp_session_update"
        and item["acpSessionId"] == session_id
        and item["payload"]["update"].get("messageId") == "durable-before-compaction-summary"
    )
    compacted = [
        item
        for item in replay
        if item["type"] == "context_compaction"
        and item["acpSessionId"] == session_id
        and item["bindingGeneration"] == binding_generation
        and item["payload"]["state"] == "compacted"
        and item["payload"]["boundaryId"] == boundary_id
    ]
    assert len(compacted) == 1
    boundary = compacted[0]
    assert boundary["payload"]["trigger"] == trigger
    assert "summary" not in boundary["payload"]
    after = next(
        item
        for item in replay
        if item["type"] == "acp_session_update"
        and item["acpSessionId"] == session_id
        and item["payload"]["update"].get("messageId") == "durable-after-compaction-summary"
    )
    if marker_free:
        assert replay.index(before) < replay.index(after) < replay.index(boundary)
    else:
        assert replay.index(before) < replay.index(boundary) < replay.index(after)
    return boundary


def _assert_only_completed_compaction(replay: list[dict[str, Any]], boundary_id: str) -> None:
    assert [
        item["payload"]["boundaryId"]
        for item in replay
        if item["type"] == "context_compaction" and item["payload"]["state"] == "compacted"
    ] == [boundary_id]


def _prompt_action(
    ticket_id: str,
    session_id: str,
    message_id: str,
    text: str,
    *,
    script: str = "default",
    delivery_choice: str = "normal",
) -> dict[str, Any]:
    return {
        "type": "prompt",
        "employeeId": ticket_id,
        "clientMessageId": message_id,
        "deliveryChoice": delivery_choice,
        "prompt": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
            "_meta": {"script": script},
        },
    }


def test_ticket_route_worker_selector_is_preselected_catalog_only_and_first_prompt_attaches(
    tmp_path: Path,
    browser: Browser,
) -> None:
    with _vite_vertical_server(tmp_path) as (base_url, ticket_id, audit_path, database_path):
        page = browser.new_page()
        backend_mutation_requests: list[str] = []
        page.on(
            "request",
            lambda request: backend_mutation_requests.append(request.url)
            if request.method == "PUT"
            and request.url.endswith(f"/api/tickets/{ticket_id}/employee-backend")
            else None,
        )
        try:
            page.goto(f"{base_url}/#/ticket/{ticket_id}")
            pane = page.locator("[data-acp-conversation-pane]")
            pane.wait_for(timeout=10_000)
            selector = page.locator('[data-ticket-employee-backend-editable="true"] select')
            selector.wait_for(timeout=10_000)
            assert selector.input_value() == "hermes"
            assert selector.locator("option").evaluate_all(
                "options => options.map(option => option.value)"
            ) == ["hermes", "probe-backend"]
            with connect(str(database_path)) as conn:
                assert (
                    conn.execute(
                        "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket_id,)
                    ).fetchone()[0]
                    is None
                )
                assert (
                    conn.execute(
                        "SELECT 1 FROM conversation_session_bindings WHERE employee_id = ?",
                        (ticket_id,),
                    ).fetchone()
                    is None
                )

            selector.select_option("probe-backend")
            page.locator('[data-ticket-employee-backend="probe-backend"]').wait_for(timeout=10_000)
            assert len(backend_mutation_requests) == 1
            with connect(str(database_path)) as conn:
                assert tuple(
                    conn.execute(
                        "SELECT employee_backend, employee_session_id FROM tickets WHERE id = ?",
                        (ticket_id,),
                    ).fetchone()
                ) == ("probe-backend", None)
                assert (
                    conn.execute(
                        "SELECT 1 FROM conversation_session_bindings WHERE employee_id = ?",
                        (ticket_id,),
                    ).fetchone()
                    is None
                )

            pane.locator("[data-chat-input]").fill("browser through Vite")
            pane.locator("[data-chat-send]").click()
            pane.locator("[data-acp-thought]").wait_for(timeout=10_000)
            pane.locator('[data-acp-tool="tool-1"]').wait_for(timeout=10_000)
            pane.locator("[data-acp-message]").filter(has_text="typed answer").wait_for(
                timeout=10_000
            )
            page.locator('[data-ticket-employee-backend-editable="false"]').wait_for(timeout=10_000)
            assert (
                page.locator('[data-ticket-employee-backend-editable="true"] select').count() == 0
            )
        finally:
            page.close()

        audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert audit[-1]["prompt"][0]["text"] == "browser through Vite"
        with connect(str(database_path)) as conn:
            binding = conn.execute(
                "SELECT backend_key, acp_session_id FROM conversation_session_bindings "
                "WHERE employee_id = ?",
                (ticket_id,),
            ).fetchone()
            mirror = conn.execute(
                "SELECT employee_backend, employee_session_id FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
        assert binding is not None and mirror is not None
        assert binding["backend_key"] == mirror["employee_backend"] == "probe-backend"
        assert binding["acp_session_id"] == mirror["employee_session_id"]


def test_ticket_route_kickoff_advance_enables_exactly_one_eager_attach(
    tmp_path: Path,
    browser: Browser,
) -> None:
    with _vite_vertical_server(tmp_path) as (base_url, ticket_id, _audit_path, database_path):
        page = browser.new_page()
        try:
            page.goto(f"{base_url}/#/ticket/{ticket_id}")
            page.locator('[data-ticket-employee-backend-editable="true"] select').wait_for(
                timeout=10_000
            )
            with connect(str(database_path)) as conn:
                assert (
                    conn.execute(
                        "SELECT 1 FROM conversation_session_bindings WHERE employee_id = ?",
                        (ticket_id,),
                    ).fetchone()
                    is None
                )

            accepted = httpx.post(
                f"{base_url}/api/tickets/{ticket_id}/accept/kickoff",
                json={"next_ceiling": "needs_success", "at_cap": "propose"},
                timeout=10,
            )
            assert accepted.status_code == 200, accepted.text
            page.locator('[data-ticket-employee-backend-editable="false"]').wait_for(timeout=10_000)
            page.locator("[data-acp-status]").filter(has_text="idle").wait_for(timeout=10_000)
        finally:
            page.close()

        with connect(str(database_path)) as conn:
            binding = conn.execute(
                "SELECT backend_key, binding_generation, acp_session_id "
                "FROM conversation_session_bindings WHERE employee_id = ?",
                (ticket_id,),
            ).fetchone()
            mirror = conn.execute(
                "SELECT employee_backend, employee_session_id FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
        assert binding is not None and mirror is not None
        assert tuple(binding)[:2] == ("hermes", 1)
        assert binding["backend_key"] == mirror["employee_backend"]
        assert binding["acp_session_id"] == mirror["employee_session_id"]


def test_fake_non_hermes_human_and_automatic_step_share_backend_and_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "fake-non-hermes.db")
    audit_path = tmp_path / "fake-non-hermes-audit.jsonl"
    monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_PATH", str(audit_path))
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    hermes = _definition()
    probe = _definition(backend_key="probe-backend")
    backends = (
        (hermes, SdkAcpEmployeeChildFactory(hermes)),
        (probe, SdkAcpEmployeeChildFactory(probe)),
    )
    catalog = EmployeeBackendCatalog(
        tuple(
            static_employee_backend_registration(definition, factory)
            for definition, factory in backends
        )
    )
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = _seed_eligible_ticket(
            conn,
            clock,
            config.boundary_hour,
            title="Fake non-Hermes shared session",
            employee_backend="probe-backend",
            employee_runtime_definitions=build_employee_runtime_definitions(catalog),
        )

    app = _application_with_backends(config, clock, backends)
    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            session_id = str(initial[0]["acpSessionId"])
            websocket.send_json(
                _prompt_action(ticket.id, session_id, "probe-human", "probe human prompt")
            )
            _receive_until(websocket, _is_idle)

        response = client.post(f"/api/test/run-step/{ticket.id}")
        assert response.status_code == 200, response.text
        assert app.state.employee_step_runner.wait_idle(timeout=5)

    audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [item["sessionId"] for item in audit] == [session_id, session_id]
    assert audit[0]["prompt"][0]["text"] == "probe human prompt"
    assert "Work ticket" in audit[1]["prompt"][0]["text"]
    with connect(db_path) as conn:
        binding = conn.execute(
            "SELECT backend_key, acp_session_id FROM conversation_session_bindings "
            "WHERE employee_id = ?",
            (ticket.id,),
        ).fetchone()
        mirror = conn.execute(
            "SELECT employee_backend, employee_session_id FROM tickets WHERE id = ?",
            (ticket.id,),
        ).fetchone()
    assert binding is not None and mirror is not None
    assert tuple(binding) == ("probe-backend", session_id)
    assert tuple(mirror) == ("probe-backend", session_id)


def test_fresh_uvicorn_process_resumes_durable_binding(tmp_path: Path) -> None:
    db_path = str(tmp_path / "restart.db")
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP restart",
            actor="test",
            now=1,
            title_max_chars=200,
        )
    port = _free_port()
    first = _start_acp_backend(db_path=db_path, logs_dir=tmp_path / "first-logs", port=port)
    try:
        with connect_websocket(
            f"ws://127.0.0.1:{port}/api/conversation",
            origin=f"http://127.0.0.1:{port}",
        ) as websocket:
            websocket.send(json.dumps({"type": "attach", "employeeId": ticket.id}))
            reset = json.loads(websocket.recv())
            ready = json.loads(websocket.recv())
            assert (reset["sequence"], ready["sequence"]) == (1, 2)
            session_id = str(reset["acpSessionId"])
            websocket.send(
                json.dumps(_prompt_action(ticket.id, session_id, "restart-1", "before restart"))
            )
            while True:
                envelope = json.loads(websocket.recv())
                if _is_idle(envelope):
                    last_sequence = int(envelope["sequence"])
                    break
    finally:
        _stop_process(first)

    second = _start_acp_backend(db_path=db_path, logs_dir=tmp_path / "second-logs", port=port)
    try:
        with connect_websocket(
            f"ws://127.0.0.1:{port}/api/conversation",
            origin=f"http://127.0.0.1:{port}",
        ) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "type": "attach",
                        "employeeId": ticket.id,
                        "lastSeenBindingGeneration": 1,
                        "lastSeenSequence": last_sequence,
                    }
                )
            )
            resumed_reset = json.loads(websocket.recv())
            resumed_ready = json.loads(websocket.recv())
            assert resumed_reset["payload"]["state"] == "reset"
            assert resumed_ready["payload"]["state"] == "ready"
            assert resumed_reset["acpSessionId"] == session_id
            assert resumed_reset["bindingGeneration"] == 1
            assert resumed_reset["sequence"] > last_sequence
    finally:
        _stop_process(second)


def test_official_fork_compaction_survives_refresh_child_death_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "durable-compaction.db")
    durable_store = tmp_path / "scripted-durable-sessions.json"
    monkeypatch.setenv("ACP_TEST_DURABLE_STORE_PATH", str(durable_store))
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP durable fork compaction",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )

    first_replacement_session_id = ""
    second_replacement_session_id = ""
    app = _application(config, clock, _compaction_definition())
    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            original_session_id = str(initial[0]["acpSessionId"])
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    original_session_id,
                    "compact-durable",
                    "/compact",
                    script="explicit_compaction_marker_free",
                )
            )
            transition = _receive_until(websocket, _is_idle, limit=128)
            replacement_reset = next(
                item
                for item in transition
                if item["type"] == "connection"
                and item["payload"]["state"] == "reset"
                and item["bindingGeneration"] == 2
            )
            first_replacement_session_id = str(replacement_reset["acpSessionId"])
            assert first_replacement_session_id != original_session_id
            compacted = [
                item
                for item in transition
                if item["type"] == "context_compaction" and item["payload"]["state"] == "compacted"
            ]
            assert len(compacted) == 1
            compacting = next(
                item
                for item in transition
                if item["type"] == "context_compaction" and item["payload"]["state"] == "compacting"
            )
            assert "summary" not in compacting["payload"]
            assert "summary" not in compacted[0]["payload"]
            assert transition.index(compacting) < transition.index(compacted[0])
            first_boundary_id = str(compacted[0]["payload"]["boundaryId"])
            _assert_compacted_replay(
                transition,
                session_id=first_replacement_session_id,
                binding_generation=2,
                boundary_id=first_boundary_id,
                trigger="explicit",
                marker_free=True,
            )
            assert transition.index(replacement_reset) < transition.index(compacted[0])

            with connect(db_path) as conn:
                binding = conn.execute(
                    "SELECT acp_session_id, binding_generation "
                    "FROM conversation_session_bindings WHERE employee_id = ?",
                    (ticket.id,),
                ).fetchone()
                mirror = conn.execute(
                    "SELECT employee_session_id FROM tickets WHERE id = ?",
                    (ticket.id,),
                ).fetchone()
            assert binding is not None and mirror is not None
            assert tuple(binding) == (first_replacement_session_id, 2)
            assert mirror["employee_session_id"] == first_replacement_session_id

        with client.websocket_connect("/api/conversation") as refreshed:
            refreshed.send_json({"type": "attach", "employeeId": ticket.id})
            replay = _receive_until(refreshed, _is_ready, limit=64)
            _assert_compacted_replay(
                replay,
                session_id=first_replacement_session_id,
                binding_generation=2,
                boundary_id=first_boundary_id,
                trigger="explicit",
                marker_free=True,
            )
            refreshed.send_json(
                _prompt_action(
                    ticket.id,
                    first_replacement_session_id,
                    "kill-compacted-child",
                    "die after compaction",
                    script="die",
                )
            )
            _receive_until(
                refreshed,
                lambda item: item["type"] == "connection" and item["payload"]["state"] == "error",
                limit=64,
            )

        with client.websocket_connect("/api/conversation") as respawned:
            respawned.send_json({"type": "attach", "employeeId": ticket.id})
            replay = _receive_until(respawned, _is_ready, limit=64)
            _assert_compacted_replay(
                replay,
                session_id=first_replacement_session_id,
                binding_generation=2,
                boundary_id=first_boundary_id,
                trigger="explicit",
                marker_free=True,
            )
            respawned.send_json(
                _prompt_action(
                    ticket.id,
                    first_replacement_session_id,
                    "compact-durable-again",
                    "/compact",
                    script="explicit_compaction",
                )
            )
            second_transition = _receive_until(
                respawned,
                lambda item: (
                    item["type"] == "connection"
                    and item["payload"]["state"] == "reset"
                    and item["bindingGeneration"] == 3
                ),
                limit=128,
            )
            second_transition.extend(_receive_until(respawned, _is_idle, limit=128))
            second_reset = next(
                item
                for item in second_transition
                if item["type"] == "connection"
                and item["payload"]["state"] == "reset"
                and item["bindingGeneration"] == 3
            )
            second_replacement_session_id = str(second_reset["acpSessionId"])
            assert second_replacement_session_id not in {
                original_session_id,
                first_replacement_session_id,
            }
            second_compacted = [
                item
                for item in second_transition
                if item["type"] == "context_compaction" and item["payload"]["state"] == "compacted"
            ]
            assert len(second_compacted) == 1
            second_boundary_id = str(second_compacted[0]["payload"]["boundaryId"])
            assert second_boundary_id != first_boundary_id
            _assert_compacted_replay(
                second_transition,
                session_id=second_replacement_session_id,
                binding_generation=3,
                boundary_id=second_boundary_id,
                trigger="explicit",
            )
            _assert_only_completed_compaction(second_transition, second_boundary_id)

            with connect(db_path) as conn:
                durable = conn.execute(
                    "SELECT acp_session_id, binding_generation, "
                    "compaction_boundaries_json "
                    "FROM conversation_session_bindings WHERE employee_id = ?",
                    (ticket.id,),
                ).fetchone()
            assert durable is not None
            assert tuple(durable)[:2] == (second_replacement_session_id, 3)
            assert json.loads(str(durable["compaction_boundaries_json"])) == [
                {"boundary_id": second_boundary_id, "trigger": "explicit"}
            ]

        with client.websocket_connect("/api/conversation") as latest:
            latest.send_json({"type": "attach", "employeeId": ticket.id})
            replay = _receive_until(latest, _is_ready, limit=64)
            _assert_compacted_replay(
                replay,
                session_id=second_replacement_session_id,
                binding_generation=3,
                boundary_id=second_boundary_id,
                trigger="explicit",
            )
            _assert_only_completed_compaction(replay, second_boundary_id)

    restarted = _application(config, clock, _compaction_definition())
    with TestClient(restarted) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            replay = _receive_until(websocket, _is_ready, limit=64)
            _assert_compacted_replay(
                replay,
                session_id=second_replacement_session_id,
                binding_generation=3,
                boundary_id=second_boundary_id,
                trigger="explicit",
            )
            _assert_only_completed_compaction(replay, second_boundary_id)
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    second_replacement_session_id,
                    "after-restart",
                    "continue after restart",
                )
            )
            _receive_until(websocket, _is_idle, limit=64)


def test_automatic_official_fork_retargets_and_runs_queued_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "automatic-compaction.db")
    audit_path = tmp_path / "automatic-audit.jsonl"
    monkeypatch.setenv(
        "ACP_TEST_DURABLE_STORE_PATH",
        str(tmp_path / "automatic-durable-sessions.json"),
    )
    monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_PATH", str(audit_path))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as release:
        release.bind(("127.0.0.1", 0))
        release_host, release_port = release.getsockname()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as replacement_release:
        replacement_release.bind(("127.0.0.1", 0))
        replacement_release_host, replacement_release_port = replacement_release.getsockname()
    monkeypatch.setenv("ACP_TEST_PROMPT_RELEASE_UDP", f"{release_host}:{release_port}")
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP automatic durable fork",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )
    definition = _compaction_definition()
    replacement_definition = AgentBackendDefinition(
        backend_key=definition.backend_key,
        argv=definition.argv,
        inherited_environment_names=definition.inherited_environment_names,
        environment_overrides=(
            *definition.environment_overrides,
            (
                "ACP_TEST_PROMPT_RELEASE_UDP",
                f"{replacement_release_host}:{replacement_release_port}",
            ),
        ),
        expected_agent_name=definition.expected_agent_name,
        expected_agent_version=definition.expected_agent_version,
        turn_capabilities=definition.turn_capabilities,
        reverse_service_capabilities=definition.reverse_service_capabilities,
        working_directory_resolver=definition.working_directory_resolver,
        turn_strategy=definition.turn_strategy,
    )
    app = _application(
        config,
        clock,
        definition,
        child_factory=_GenerationScopedSdkChildFactory(definition, replacement_definition),
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            original_session_id = str(initial[0]["acpSessionId"])
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    original_session_id,
                    "automatic-source",
                    "trigger automatic compaction",
                    script="automatic_compaction",
                )
            )
            _receive_until(
                websocket,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    original_session_id,
                    "automatic-successor",
                    "queued after automatic compaction",
                    delivery_choice="queue",
                )
            )
            _receive_until(
                websocket,
                lambda item: (
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "automatic-successor"
                    and item["payload"]["state"] == "queued"
                ),
            )
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"1", (release_host, release_port))
            transition = _receive_until(
                websocket,
                lambda item: (
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "automatic-successor"
                    and item["payload"]["state"] == "started"
                ),
                limit=128,
            )
            replacement_reset = next(
                item
                for item in transition
                if item["type"] == "connection"
                and item["payload"]["state"] == "reset"
                and item["bindingGeneration"] == 2
            )
            replacement_session_id = str(replacement_reset["acpSessionId"])
            assert any(
                item["type"] == "context_compaction"
                and item["payload"]["state"] == "compacted"
                and item["payload"]["trigger"] == "automatic"
                for item in transition
            )
            assert any(
                item["type"] == "human_echo"
                and item["payload"]["clientMessageId"] == "automatic-successor"
                and item["payload"]["prompt"]["sessionId"] == replacement_session_id
                for item in transition
            )
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"2", (replacement_release_host, replacement_release_port))
            _receive_until(websocket, _is_idle, limit=64)
            audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
            assert [item["sessionId"] for item in audit] == [
                original_session_id,
                replacement_session_id,
            ]
            assert audit[1]["prompt"][0]["text"] == ("queued after automatic compaction")


def test_missing_fork_capability_retires_source_and_fresh_attach_restores_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "missing-fork.db")
    monkeypatch.setenv("ACP_TEST_FORK_SESSION", "0")
    monkeypatch.setenv("ACP_TEST_DURABLE_STORE_PATH", str(tmp_path / "missing-fork-store.json"))
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP missing fork",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )
    app = _application(config, clock, _compaction_definition())
    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            session_id = str(initial[0]["acpSessionId"])
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    "missing-fork-compact",
                    "/compact",
                    script="explicit_compaction",
                )
            )
            failed = _receive_until(
                websocket,
                lambda item: item["type"] == "connection" and item["payload"]["state"] == "error",
                limit=128,
            )
            assert any(
                item["type"] == "context_compaction" and item["payload"]["state"] == "failed"
                for item in failed
            )
            assert not any(
                item["type"] == "connection" and item["payload"]["state"] == "reset"
                for item in failed
            )
            with connect(db_path) as conn:
                binding = conn.execute(
                    "SELECT acp_session_id, binding_generation "
                    "FROM conversation_session_bindings WHERE employee_id = ?",
                    (ticket.id,),
                ).fetchone()
            assert binding is not None
            assert tuple(binding) == (session_id, 1)

        with client.websocket_connect("/api/conversation") as restored:
            restored.send_json({"type": "attach", "employeeId": ticket.id})
            replay = _receive_until(restored, _is_ready, limit=64)
            reset = next(
                item
                for item in replay
                if item["type"] == "connection" and item["payload"]["state"] == "reset"
            )
            assert reset["acpSessionId"] == session_id
            assert reset["bindingGeneration"] == 1
            restored.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    "after-missing-fork",
                    "usable after fresh attach",
                )
            )
            usable = _receive_until(
                restored,
                lambda item: (
                    item["type"] == "acp_session_update"
                    and item["payload"]["update"]["sessionUpdate"] == "agent_message_chunk"
                ),
                limit=64,
            )
            usable.extend(_receive_until(restored, _is_idle, limit=64))
            assert any(_is_idle(item) for item in usable)


def test_browser_and_worker_share_one_real_sdk_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "planning.db")
    audit_path = tmp_path / "prompt-audit.jsonl"
    monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_PATH", str(audit_path))
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="ACP vertical",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) "
            "VALUES (?, 'test_db_only', '{}', ?)",
            (ticket.id, clock.now_unix()),
        )

    definition = _definition()
    app = _application(config, clock, definition)

    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            reset = next(
                item
                for item in initial
                if item["type"] == "connection" and item["payload"]["state"] == "reset"
            )
            session_id = str(reset["acpSessionId"])
            assert [(item["type"], item["sequence"]) for item in initial] == [
                ("connection", 1),
                ("connection", 2),
            ]
            assert reset["bindingGeneration"] == 1
            assert not audit_path.exists()
            with connect(db_path) as conn:
                first_binding = conn.execute(
                    "SELECT acp_session_id, binding_generation "
                    "FROM conversation_session_bindings WHERE employee_id = ?",
                    (ticket.id,),
                ).fetchone()
                first_mirror = conn.execute(
                    "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket.id,)
                ).fetchone()
            assert first_binding is not None and first_mirror is not None
            assert tuple(first_binding) == (session_id, 1)
            assert first_mirror["employee_session_id"] == session_id

            websocket.send_json(_prompt_action(ticket.id, session_id, "human-1", "human prompt"))
            human_turn = _receive_until(websocket, _is_idle)
            update_kinds = {
                item["payload"]["update"]["sessionUpdate"]
                for item in human_turn
                if item["type"] == "acp_session_update"
            }
            assert {
                "agent_thought_chunk",
                "tool_call",
                "tool_call_update",
                "agent_message_chunk",
            }.issubset(update_kinds)

            worker_result: list[Any] = []

            def record_worker_callback(callback_session_id: str) -> None:
                with audit_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"callbackSessionId": callback_session_id}) + "\n")

            worker = threading.Thread(
                target=lambda: worker_result.append(
                    app.state.conversation.step_gateway.run_ticket_step(
                        session_id,
                        ticket.id,
                        "worker prompt",
                        on_employee_session_id=record_worker_callback,
                    )
                )
            )
            worker.start()
            worker_turn = _receive_until(websocket, _is_idle)
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert worker_result[0].status == "complete"
            assert worker_result[0].employee_session_id == session_id
            assert worker_result[0].error is None
            assert any(item["type"] == "acp_session_update" for item in worker_turn)

            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    "human-permission",
                    "permission prompt",
                    script="permission",
                )
            )
            permission_prefix = _receive_until(
                websocket, lambda item: item["type"] == "permission_request"
            )
            request = permission_prefix[-1]
            unattached = client.portal.call(
                app.state.conversation.permission_broker.respond_to_permission,
                "not-attached",
                request["payload"]["requestId"],
                "allow-once",
            )
            assert unattached.disposition == "rejected"
            websocket.send_json(
                {
                    "type": "permission_response",
                    "employeeId": ticket.id,
                    "requestId": request["payload"]["requestId"],
                    "optionId": "allow-once",
                }
            )
            permission_suffix = _receive_until(websocket, _is_idle)
            assert any(item["type"] == "permission_outcome" for item in permission_suffix)

            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    "hold-active",
                    "hold prompt",
                    script="wait_for_cancel",
                )
            )
            _receive_until(
                websocket,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    "queued-choice",
                    "queued prompt",
                    delivery_choice="queue",
                )
            )
            _receive_until(
                websocket,
                lambda item: (
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "queued-choice"
                    and item["payload"]["state"] == "queued"
                ),
            )
            websocket.send_json({"type": "cancel", "employeeId": ticket.id})
            after_cancel = _receive_until(websocket, _is_idle)
            assert any(
                item["type"] == "delivery_receipt"
                and item["payload"]["clientMessageId"] == "hold-active"
                and item["payload"]["state"] == "interrupted"
                for item in after_cancel
            )

            last_sequence = int(after_cancel[-1]["sequence"])

        with client.websocket_connect("/api/conversation") as refreshed:
            refreshed.send_json(
                {
                    "type": "attach",
                    "employeeId": ticket.id,
                    "lastSeenBindingGeneration": 1,
                    "lastSeenSequence": last_sequence,
                }
            )
            replay = _receive_until(refreshed, _is_ready)
            assert any(
                item["type"] == "acp_session_update"
                and item["payload"]["update"]["sessionUpdate"] == "agent_thought_chunk"
                for item in replay
            )

            with client.websocket_connect("/api/conversation") as second_browser:
                second_browser.send_json({"type": "attach", "employeeId": ticket.id})
                _receive_until(second_browser, _is_ready)
                _receive_until(refreshed, _is_ready)
                old_handle = client.portal.call(
                    app.state.conversation.registry.resolve_runtime_handle,
                    ticket.id,
                    1,
                )
                old_source = ConversationIngressSource(
                    old_handle.employee,
                    old_handle.child_generation,
                    old_handle.record_identity,
                )
                refreshed.send_json({"type": "new_conversation", "employeeId": ticket.id})
                replacement = _receive_until(refreshed, _is_ready)
                second_replacement = _receive_until(second_browser, _is_ready)
                replacement_reset = next(
                    item
                    for item in replacement
                    if item["type"] == "connection" and item["payload"]["state"] == "reset"
                )
                second_reset = next(
                    item
                    for item in second_replacement
                    if item["type"] == "connection" and item["payload"]["state"] == "reset"
                )
                assert replacement_reset == second_reset
                assert replacement_reset["bindingGeneration"] == 2
                assert replacement_reset["acpSessionId"] != session_id
                replacement_session_id = str(replacement_reset["acpSessionId"])
                replacement_last_sequence = int(replacement[-1]["sequence"])

                with connect(db_path) as conn:
                    replacement_binding = conn.execute(
                        "SELECT acp_session_id, binding_generation "
                        "FROM conversation_session_bindings WHERE employee_id = ?",
                        (ticket.id,),
                    ).fetchone()
                    replacement_mirror = conn.execute(
                        "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket.id,)
                    ).fetchone()
                assert replacement_binding is not None and replacement_mirror is not None
                assert tuple(replacement_binding) == (replacement_session_id, 2)
                assert replacement_mirror["employee_session_id"] == replacement_session_id

                client.portal.call(
                    app.state.conversation.hub.registry_conversation_ingress,
                    old_source,
                    SessionNotification(
                        session_id=session_id,
                        update=AgentMessageChunk(
                            session_update="agent_message_chunk",
                            message_id="stale-old-session",
                            content=TextContentBlock(type="text", text="must be ignored"),
                        ),
                    ),
                )
                replacement_handle = client.portal.call(
                    app.state.conversation.registry.resolve_runtime_handle,
                    ticket.id,
                    2,
                )
                client.portal.call(
                    app.state.conversation.hub.publish_activity,
                    replacement_handle.employee,
                    replacement_handle.binding,
                    "thinking",
                    "replacement remains live",
                )
                stale_barrier = _receive_until(refreshed, lambda item: item["type"] == "activity")[
                    -1
                ]
                _receive_until(second_browser, lambda item: item["type"] == "activity")
                assert stale_barrier["acpSessionId"] == replacement_session_id
                assert old_handle.child.alive is True

        complete_audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert complete_audit[1] == {"callbackSessionId": session_id}
        audit = [item for item in complete_audit if "sessionId" in item]
        assert [item["sessionId"] for item in audit] == [session_id] * 5
        assert [item["prompt"][0]["text"] for item in audit] == [
            "human prompt",
            "worker prompt",
            "permission prompt",
            "hold prompt",
            "queued prompt",
        ]
        assert all(
            "DB rows are not ACP delivery" not in json.dumps(item, separators=(",", ":"))
            for item in audit
        )

    restarted_app = _application(config, clock, _definition())
    with TestClient(restarted_app) as restarted_client:
        with restarted_client.websocket_connect("/api/conversation") as restarted:
            restarted.send_json(
                {
                    "type": "attach",
                    "employeeId": ticket.id,
                    "lastSeenBindingGeneration": 2,
                    "lastSeenSequence": replacement_last_sequence,
                }
            )
            resumed = _receive_until(restarted, _is_ready)
            resumed_reset = resumed[0]
            assert resumed_reset["payload"]["state"] == "reset"
            assert resumed_reset["bindingGeneration"] == 2
            assert resumed_reset["acpSessionId"] == replacement_session_id
            assert resumed_reset["sequence"] > replacement_last_sequence

    with connect(db_path) as conn:
        binding = conn.execute(
            "SELECT acp_session_id, binding_generation "
            "FROM conversation_session_bindings WHERE employee_id = ?",
            (ticket.id,),
        ).fetchone()
    assert binding is not None
    assert int(binding["binding_generation"]) == 2
    assert str(binding["acp_session_id"]) == replacement_session_id


def test_official_requested_cancel_exception_recovers_same_session_for_stop_and_send_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "requested-cancel.db")
    prompt_audit = tmp_path / "requested-cancel-prompts.jsonl"
    late_send_audit = tmp_path / "requested-cancel-late-send.jsonl"
    durable_store = tmp_path / "requested-cancel-durable.json"
    monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_PATH", str(prompt_audit))
    monkeypatch.setenv("ACP_TEST_LATE_SEND_AUDIT_PATH", str(late_send_audit))
    monkeypatch.setenv("ACP_TEST_DURABLE_STORE_PATH", str(durable_store))
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        stop_ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Requested cancel Stop recovery",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )
        send_now_ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Requested cancel Send Now recovery",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )

    app = _application(config, clock, _definition())
    with TestClient(app) as client:
        with (
            client.websocket_connect("/api/conversation") as stop_first,
            client.websocket_connect("/api/conversation") as stop_second,
        ):
            stop_first.send_json({"type": "attach", "employeeId": stop_ticket.id})
            stop_initial = _receive_until(stop_first, _is_ready)
            stop_session = str(stop_initial[0]["acpSessionId"])
            stop_second.send_json({"type": "attach", "employeeId": stop_ticket.id})
            _receive_until(stop_second, _is_ready)
            _receive_until(stop_first, _is_ready)
            stop_old = client.portal.call(
                app.state.conversation.registry.resolve_runtime_handle,
                stop_ticket.id,
                1,
            )
            stop_first.send_json(
                _prompt_action(
                    stop_ticket.id,
                    stop_session,
                    "stop-predecessor",
                    "stop predecessor",
                    script="requested_cancel_unwind",
                )
            )
            _receive_until(
                stop_first,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            _receive_until(
                stop_second,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            stop_first.send_json({"type": "cancel", "employeeId": stop_ticket.id})
            stop_recovery = _receive_until(stop_first, _is_idle, limit=96)
            stop_recovery_second = _receive_until(stop_second, _is_idle, limit=96)

            for recovery in (stop_recovery, stop_recovery_second):
                types = [item["type"] for item in recovery]
                reset_index = next(
                    index
                    for index, item in enumerate(recovery)
                    if item["type"] == "connection" and item["payload"]["state"] == "reset"
                )
                ready_index = next(
                    index
                    for index, item in enumerate(recovery)
                    if item["type"] == "connection" and item["payload"]["state"] == "ready"
                )
                queue_index = types.index("queue_snapshot")
                assert reset_index < ready_index < queue_index
                assert any(
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "stop-predecessor"
                    and item["payload"]["state"] == "interrupted"
                    for item in recovery
                )
                assert "REQUESTED CANCEL LATE OLD OUTPUT" not in json.dumps(recovery)

            stop_replacement = client.portal.call(
                app.state.conversation.registry.resolve_runtime_handle,
                stop_ticket.id,
                1,
            )
            assert stop_replacement.binding == stop_old.binding
            assert stop_replacement.child_generation == stop_old.child_generation + 1
            assert stop_replacement.child is not stop_old.child
            assert stop_replacement.record_identity is not stop_old.record_identity
            stop_first.send_json(
                _prompt_action(
                    stop_ticket.id,
                    stop_session,
                    "stop-follow-up",
                    "stop follow up",
                )
            )
            stop_follow_up = _receive_until(stop_first, _is_idle)
            assert "typed answer" in json.dumps(stop_follow_up)
            assert "REQUESTED CANCEL LATE OLD OUTPUT" not in json.dumps(stop_follow_up)

        with client.websocket_connect("/api/conversation") as send_now:
            send_now.send_json({"type": "attach", "employeeId": send_now_ticket.id})
            send_now_initial = _receive_until(send_now, _is_ready)
            send_now_session = str(send_now_initial[0]["acpSessionId"])
            send_now_old = client.portal.call(
                app.state.conversation.registry.resolve_runtime_handle,
                send_now_ticket.id,
                1,
            )
            send_now.send_json(
                _prompt_action(
                    send_now_ticket.id,
                    send_now_session,
                    "send-now-predecessor",
                    "send now predecessor",
                    script="requested_cancel_unwind",
                )
            )
            _receive_until(
                send_now,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            send_now.send_json(
                _prompt_action(
                    send_now_ticket.id,
                    send_now_session,
                    "send-now-fifo",
                    "send now fifo",
                    delivery_choice="queue",
                )
            )
            _receive_until(
                send_now,
                lambda item: (
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "send-now-fifo"
                    and item["payload"]["state"] == "queued"
                ),
            )
            send_now.send_json(
                _prompt_action(
                    send_now_ticket.id,
                    send_now_session,
                    "send-now-successor",
                    "send now successor",
                    script="send_now_successor",
                    delivery_choice="send_now",
                )
            )
            through_fifo_start = _receive_until(
                send_now,
                lambda item: (
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "send-now-fifo"
                    and item["payload"]["state"] == "started"
                ),
                limit=160,
            )
            after_fifo = _receive_until(send_now, _is_idle, limit=96)
            send_now_recovery = through_fifo_start + after_fifo
            assert "REQUESTED CANCEL LATE OLD OUTPUT" not in json.dumps(send_now_recovery)
            reset_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if item["type"] == "connection" and item["payload"]["state"] == "reset"
            )
            ready_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if item["type"] == "connection" and item["payload"]["state"] == "ready"
            )
            successor_started_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if item["type"] == "delivery_receipt"
                and item["payload"]["clientMessageId"] == "send-now-successor"
                and item["payload"]["state"] == "started"
            )
            fifo_human_echo_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if index > ready_index
                and item["type"] == "human_echo"
                and item["payload"]["clientMessageId"] == "send-now-fifo"
            )
            successor_human_echo_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if index > ready_index
                and item["type"] == "human_echo"
                and item["payload"]["clientMessageId"] == "send-now-successor"
            )
            queue_snapshot_index = next(
                index
                for index, item in enumerate(send_now_recovery)
                if index > ready_index and item["type"] == "queue_snapshot"
            )
            assert (
                reset_index
                < ready_index
                < fifo_human_echo_index
                < successor_human_echo_index
                < queue_snapshot_index
                < successor_started_index
            )
            post_reset = send_now_recovery[reset_index:]
            assert (
                sum(
                    item["type"] == "human_echo"
                    and item["payload"]["clientMessageId"] == "send-now-fifo"
                    for item in post_reset
                )
                == 1
            )
            assert (
                sum(
                    item["type"] == "human_echo"
                    and item["payload"]["clientMessageId"] == "send-now-successor"
                    for item in post_reset
                )
                == 1
            )
            assert send_now_recovery[successor_human_echo_index]["payload"]["prompt"] == {
                "sessionId": send_now_session,
                "prompt": [{"type": "text", "text": "send now successor"}],
                "_meta": {"script": "send_now_successor"},
            }
            assert [
                item["clientMessageId"]
                for item in send_now_recovery[queue_snapshot_index]["payload"]["items"]
            ] == ["send-now-fifo"]
            assert json.dumps(send_now_recovery).count("SEND NOW SUCCESSOR ANSWER") == 1
            assert (
                sum(
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "send-now-successor"
                    and item["payload"]["state"] == "started"
                    for item in send_now_recovery
                )
                == 1
            )
            assert (
                sum(
                    item["type"] == "delivery_receipt"
                    and item["payload"]["clientMessageId"] == "send-now-fifo"
                    and item["payload"]["state"] == "started"
                    for item in send_now_recovery
                )
                == 1
            )
            send_now_replacement = client.portal.call(
                app.state.conversation.registry.resolve_runtime_handle,
                send_now_ticket.id,
                1,
            )
            assert send_now_replacement.binding == send_now_old.binding
            assert send_now_replacement.child_generation == (send_now_old.child_generation + 1)
            assert send_now_replacement.child is not send_now_old.child
            assert send_now_replacement.record_identity is not (send_now_old.record_identity)

    late_records = [json.loads(line) for line in late_send_audit.read_text().splitlines()]
    assert [item["event"] for item in late_records] == [
        "requested_cancel_late_send_completed",
        "requested_cancel_late_send_completed",
    ]
    prompt_records = [json.loads(line) for line in prompt_audit.read_text().splitlines()]
    prompts_by_text = [item["prompt"][0]["text"] for item in prompt_records]
    assert prompts_by_text.count("stop predecessor") == 1
    assert prompts_by_text.count("stop follow up") == 1
    assert prompts_by_text.count("send now predecessor") == 1
    assert prompts_by_text.count("send now successor") == 1
    assert prompts_by_text.count("send now fifo") == 1
    assert all(
        item["sessionId"] == stop_session
        for item in prompt_records
        if item["prompt"][0]["text"].startswith("stop ")
    )
    assert all(
        item["sessionId"] == send_now_session
        for item in prompt_records
        if item["prompt"][0]["text"].startswith("send now ")
    )


def test_automatic_worker_delivers_pending_context_through_official_sdk_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "worker-context.db")
    context_text = "Reread the changed Ticket before continuing."
    title = "[ACP_TEST_WAIT_FOR_CANCEL] automatic worker context"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as audit_receiver:
        audit_receiver.bind(("127.0.0.1", 0))
        audit_receiver.settimeout(3)
        host, port = audit_receiver.getsockname()
        monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_UDP", f"{host}:{port}")
        config = load_config(
            env={
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": db_path,
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
                "PLAN_DISPATCH_ENABLED": "0",
            }
        )
        clock = build_clock(config)
        with connect(db_path) as conn:
            create_schema(conn)
            ticket = _seed_eligible_ticket(
                conn,
                clock,
                config.boundary_hour,
                title=title,
            )
            pending = worker_context_data.set_context(
                conn,
                ticket.id,
                "ticket_changed",
                context_text,
            )
        app = _application(config, clock, _definition())

        with TestClient(app) as client:
            response = client.post(f"/api/test/run-step/{ticket.id}")
            assert response.status_code == 200
            audit = json.loads(audit_receiver.recv(65_536))
            assert len(audit["prompt"]) == 1
            assert audit["prompt"][0]["type"] == "text"
            model_text = str(audit["prompt"][0]["text"])
            assert model_text.startswith(f"Work ticket {ticket.id} — {title}.")
            assert model_text.count("[Pending worker context]") == 1
            assert model_text.count(context_text) == 1
            assert model_text.endswith(
                f"[Pending worker context]\n- {context_text}\n[/Pending worker context]"
            )

            deadline = time.monotonic() + 3
            pending_row: Any = None
            while time.monotonic() < deadline:
                with connect(db_path) as conn:
                    pending_row = conn.execute(
                        "SELECT revision FROM pending_worker_context "
                        "WHERE worker_entity_id = ? AND context_key = ?",
                        (ticket.id, pending.context_key),
                    ).fetchone()
                if pending_row is None:
                    break
                time.sleep(0.01)
            assert pending_row is None

            session_id = str(audit["sessionId"])
            with connect(db_path) as conn:
                step = conn.execute(
                    "SELECT status, employee_session_id, error "
                    "FROM employee_step_runs WHERE ticket_id = ?",
                    (ticket.id,),
                ).fetchone()
            assert step is not None
            assert tuple(step) == ("running", session_id, None)

            app.state.conversation.step_gateway.interrupt(
                session_id,
                ticket.id,
                deadline=monotonic() + 1,
            )
            assert app.state.employee_step_runner.wait_idle(timeout=3)


def test_automatic_worker_starts_stream_before_midturn_browser_attach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "worker-first.db")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as audit_receiver:
        audit_receiver.bind(("127.0.0.1", 0))
        audit_receiver.settimeout(3)
        host, port = audit_receiver.getsockname()
        monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_UDP", f"{host}:{port}")
        config = load_config(
            env={
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": db_path,
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
                "PLAN_DISPATCH_ENABLED": "0",
            }
        )
        clock = build_clock(config)
        with connect(db_path) as conn:
            create_schema(conn)
            ticket = _seed_eligible_ticket(conn, clock, config.boundary_hour)
        app = _application(config, clock, _definition())

        with TestClient(app) as client:
            response = client.post(f"/api/test/run-step/{ticket.id}")
            assert response.status_code == 200
            audit = json.loads(audit_receiver.recv(65_536))
            session_id = str(audit["sessionId"])
            assert "[ACP_TEST_WAIT_FOR_CANCEL]" in audit["prompt"][0]["text"]
            with connect(db_path) as conn:
                running = conn.execute(
                    "SELECT ticket_status, employee_session_id FROM tickets WHERE id = ?",
                    (ticket.id,),
                ).fetchone()
                step = conn.execute(
                    "SELECT status, employee_session_id FROM employee_step_runs "
                    "WHERE ticket_id = ?",
                    (ticket.id,),
                ).fetchone()
            assert running is not None and step is not None
            assert tuple(running) == ("agent_running_step", session_id)
            assert tuple(step) == ("running", session_id)

            with client.websocket_connect("/api/conversation") as websocket:
                websocket.send_json({"type": "attach", "employeeId": ticket.id})
                ready_count = 0

                def complete_active_replay(envelope: dict[str, Any]) -> bool:
                    nonlocal ready_count
                    if _is_ready(envelope):
                        ready_count += 1
                    return ready_count == 2

                replay = _receive_until(websocket, complete_active_replay)
                assert (
                    sum(
                        item["type"] == "connection" and item["payload"]["state"] == "reset"
                        for item in replay
                    )
                    == 1
                )
                worker_user_updates = [
                    item
                    for item in replay
                    if item["type"] == "acp_session_update"
                    and item["payload"]["update"]["sessionUpdate"] == "user_message_chunk"
                ]
                assert len(worker_user_updates) == 1
                assert worker_user_updates[0]["acpSessionId"] == session_id

                app.state.conversation.step_gateway.interrupt(
                    session_id,
                    ticket.id,
                    deadline=monotonic() + 1,
                )
                _receive_until(
                    websocket,
                    lambda item: (
                        item["type"] == "activity" and item["payload"]["state"] == "interrupted"
                    ),
                )

            assert app.state.employee_step_runner.wait_idle(timeout=3)
            with connect(db_path) as conn:
                settled_ticket = conn.execute(
                    "SELECT ticket_status, employee_session_id FROM tickets WHERE id = ?",
                    (ticket.id,),
                ).fetchone()
                settled_step = conn.execute(
                    "SELECT status FROM employee_step_runs WHERE ticket_id = ?",
                    (ticket.id,),
                ).fetchone()
            assert settled_ticket is not None and settled_step is not None
            assert tuple(settled_ticket) == ("errored", session_id)
            assert settled_step["status"] == "interrupted"


def test_stale_worker_permission_cannot_settle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "worker-permission.db")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as audit_receiver:
        audit_receiver.bind(("127.0.0.1", 0))
        audit_receiver.settimeout(3)
        host, port = audit_receiver.getsockname()
        monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_UDP", f"{host}:{port}")
        config = load_config(
            env={
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": db_path,
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
                "PLAN_DISPATCH_ENABLED": "0",
            }
        )
        clock = build_clock(config)
        with connect(db_path) as conn:
            create_schema(conn)
            ticket = _seed_eligible_ticket(
                conn,
                clock,
                config.boundary_hour,
                title="[ACP_TEST_PERMISSION] automatic vertical",
            )
        app = _application(config, clock, _definition())

        with TestClient(app) as client:
            with client.websocket_connect("/api/conversation") as websocket:
                websocket.send_json({"type": "attach", "employeeId": ticket.id})
                initial = _receive_until(websocket, _is_ready)
                session_id = str(initial[0]["acpSessionId"])
                response = client.post(f"/api/test/run-step/{ticket.id}")
                assert response.status_code == 200
                audit = json.loads(audit_receiver.recv(65_536))
                assert audit["sessionId"] == session_id
                permission = _receive_until(
                    websocket, lambda item: item["type"] == "permission_request"
                )[-1]
                request_id = str(permission["payload"]["requestId"])
                connection_id = client.portal.call(
                    lambda: next(
                        iter(
                            app.state.conversation.hub._streams[  # noqa: SLF001
                                ticket.id
                            ].browsers
                        )
                    )
                )

                with connect(db_path) as conn:
                    conn.execute(
                        "UPDATE tickets SET employee_session_id = 'stale-session' WHERE id = ?",
                        (ticket.id,),
                    )
                rejected = client.portal.call(
                    app.state.conversation.permission_broker.respond_to_permission,
                    connection_id,
                    request_id,
                    "allow-once",
                )
                assert rejected.disposition == "rejected"
                pending = client.portal.call(
                    app.state.conversation.permission_broker.pending_snapshot, ticket.id
                )
                assert len(pending) == 1 and pending[0].settling is False
                with connect(db_path) as conn:
                    conn.execute(
                        "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
                        (session_id, ticket.id),
                    )
                client.portal.call(
                    app.state.conversation.permission_broker.detach_browser,
                    connection_id,
                )

            assert app.state.employee_step_runner.wait_idle(timeout=3)


@pytest.mark.parametrize("failure_mode", ["rejection", "capture"])
def test_worker_failure_settles_before_queued_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    db_path = str(tmp_path / f"worker-{failure_mode}.db")
    audit_path = tmp_path / f"worker-{failure_mode}-audit.jsonl"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as audit_receiver:
        audit_receiver.bind(("127.0.0.1", 0))
        audit_receiver.settimeout(3)
        audit_host, audit_port = audit_receiver.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as release_reservation:
            release_reservation.bind(("127.0.0.1", 0))
            release_host, release_port = release_reservation.getsockname()
        monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_UDP", f"{audit_host}:{audit_port}")
        monkeypatch.setenv("ACP_TEST_PROMPT_AUDIT_PATH", str(audit_path))
        monkeypatch.setenv("ACP_TEST_PROMPT_RELEASE_UDP", f"{release_host}:{release_port}")
        if failure_mode == "rejection":
            monkeypatch.setenv("ACP_TEST_MALFORMED_DURING_PROMPT", "1")
        config = load_config(
            env={
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": db_path,
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
                "PLAN_DISPATCH_ENABLED": "0",
            }
        )
        clock = build_clock(config)
        with connect(db_path) as conn:
            create_schema(conn)
            ticket = tickets_data.create_ticket(
                conn,
                worker_type="coding",
                title=f"ACP worker {failure_mode}",
                actor="test",
                now=clock.now_unix(),
                title_max_chars=200,
            )
        app = _application(
            config,
            clock,
            _definition(observes_compaction=failure_mode == "capture"),
        )

        with TestClient(app) as client:
            with client.websocket_connect("/api/conversation") as websocket:
                websocket.send_json({"type": "attach", "employeeId": ticket.id})
                initial = _receive_until(websocket, _is_ready)
                session_id = str(initial[0]["acpSessionId"])
                worker_result: list[Any] = []
                worker = threading.Thread(
                    target=lambda: worker_result.append(
                        app.state.conversation.step_gateway.run_ticket_step(
                            session_id,
                            ticket.id,
                            "/compact" if failure_mode == "capture" else "reject worker",
                        )
                    )
                )
                worker.start()
                first_audit = json.loads(audit_receiver.recv(65_536))
                assert first_audit["sessionId"] == session_id
                websocket.send_json(
                    _prompt_action(
                        ticket.id,
                        session_id,
                        "queued-successor",
                        "queued successor",
                        delivery_choice="queue",
                    )
                )
                _receive_until(
                    websocket,
                    lambda item: (
                        item["type"] == "delivery_receipt"
                        and item["payload"]["clientMessageId"] == "queued-successor"
                        and item["payload"]["state"] == "queued"
                    ),
                )
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as release_sender:
                    release_sender.sendto(b"1", (release_host, release_port))

                if failure_mode == "capture":
                    successor_audit = json.loads(audit_receiver.recv(65_536))
                    assert successor_audit["prompt"][0]["text"] == "queued successor"
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as release_sender:
                        release_sender.sendto(b"2", (release_host, release_port))
                    _receive_until(websocket, _is_idle)
                else:
                    rejected = _receive_until(
                        websocket,
                        lambda item: (
                            item["type"] == "delivery_receipt"
                            and item["payload"]["clientMessageId"] == "queued-successor"
                            and item["payload"]["state"] == "rejected"
                        ),
                    )
                    assert rejected[-1]["payload"]["reason"]
                    _receive_until(
                        websocket,
                        lambda item: (
                            item["type"] == "activity" and item["payload"]["state"] == "failed"
                        ),
                    )

                worker.join(timeout=3)
                assert not worker.is_alive()
                assert worker_result[0].status == "errored"
                audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
                assert len(audit) == (2 if failure_mode == "capture" else 1)


@pytest.mark.parametrize("replay_mode", ["slow_consumer", "reset_overflow"])
def test_active_vertical_replay_fails_closed_without_harming_live_browser(
    tmp_path: Path,
    replay_mode: str,
) -> None:
    db_path = str(tmp_path / f"active-replay-{replay_mode}.db")
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-20T12:00:00+00:00",
            "PLAN_DISPATCH_ENABLED": "0",
        }
    )
    clock = build_clock(config)
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title=f"ACP active replay {replay_mode}",
            actor="test",
            now=clock.now_unix(),
            title_max_chars=200,
        )
    app = _application(
        config,
        clock,
        _definition(),
        browser_capacity=16,
        reset_buffer_byte_limit=1 if replay_mode == "reset_overflow" else 1_048_576,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/api/conversation") as websocket:
            websocket.send_json({"type": "attach", "employeeId": ticket.id})
            initial = _receive_until(websocket, _is_ready)
            session_id = str(initial[0]["acpSessionId"])
            websocket.send_json(
                _prompt_action(
                    ticket.id,
                    session_id,
                    f"held-{replay_mode}",
                    "held active prompt",
                    script="wait_for_cancel",
                )
            )
            _receive_until(
                websocket,
                lambda item: item["type"] == "activity" and item["payload"]["state"] == "thinking",
            )
            replay = client.portal.call(
                lambda: app.state.conversation.hub.attach_browser(
                    ticket.id,
                    connection_id=f"direct-{replay_mode}",
                )
            )

            if replay_mode == "reset_overflow":
                assert replay.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
                assert replay.queue.empty()
            else:
                assert replay.close_reason is None
                handle = client.portal.call(
                    app.state.conversation.registry.resolve_runtime_handle,
                    ticket.id,
                    1,
                )
                for index in range(32):
                    detail = f"fanout-{index}"
                    client.portal.call(
                        app.state.conversation.hub.publish_activity,
                        handle.employee,
                        handle.binding,
                        "thinking",
                        detail,
                    )
                    _receive_until(
                        websocket,
                        lambda item, expected=detail: (
                            item["type"] == "activity" and item["payload"]["detail"] == expected
                        ),
                    )
                    if replay.closed.is_set():
                        break
                assert replay.closed.is_set()
                assert replay.close_reason == SLOW_CONSUMER_CLOSE_REASON

            websocket.send_json({"type": "cancel", "employeeId": ticket.id})
            _receive_until(websocket, _is_idle)
