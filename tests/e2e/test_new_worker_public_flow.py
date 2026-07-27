"""Public New Worker flow through the production ACP composition."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from acp.transports import default_environment
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

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
from planner.conversation.sdk_child import SdkAcpEmployeeChildFactory
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.days.logic.dates import resolve_day_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import build_employee_runtime_definitions
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.initiative_planning import INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT = REPOSITORY_ROOT / "tests/support/acp_scripted_agent.py"
_AGENT = {"X-Plan-Actor": "agent"}

_SCRIPTED_WORKER_TYPE_DEFINITIONS = tuple(
    replace(
        definition,
        worker_profile=replace(
            definition.worker_profile,
            default_employee_backend="hermes",
            default_employee_model=None,
            default_employee_reasoning_effort=None,
        ),
    )
    for definition in (
        CODING_WORKER_TYPE_DEFINITION,
        NEW_WORKER_TYPE_DEFINITION,
        EXPLORATION_WORKER_TYPE_DEFINITION,
        INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION,
    )
)


class _Strategy:
    def classify_replay(
        self,
        _binding: object,
        replay: tuple[object, ...],
        _boundaries: tuple[object, ...],
    ) -> tuple[object, ...]:
        return replay

    async def steer(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def observe_compaction(self, *args: object, **kwargs: object) -> None:
        return None

    async def capture_compaction(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


def _definition() -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key="hermes",
        argv=(sys.executable, str(SCRIPTED_AGENT)),
        inherited_environment_names=tuple(default_environment()),
        environment_overrides=(),
        expected_agent_name="panels-scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(False, False),
        reverse_service_capabilities=ReverseServiceCapabilities(False, False, True),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_Strategy(),
    )


def _application(tmp_path: Path) -> tuple[Any, Path, str]:
    db_path = tmp_path / "new-worker.db"
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
    )
    clock = build_clock(config)
    definition = _definition()
    catalog = EmployeeBackendCatalog(
        (
            static_employee_backend_registration(
                definition,
                SdkAcpEmployeeChildFactory(
                    definition, panels_server_url="http://127.0.0.1:8767"
                ),
            ),
        )
    )
    runtime_definitions = build_employee_runtime_definitions(
        catalog,
        worker_type_definitions=_SCRIPTED_WORKER_TYPE_DEFINITIONS,
    )
    with connect(str(db_path)) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="new_worker",
            title="Design a public API worker",
            actor="human",
            now=clock.now_unix(),
            title_max_chars=200,
            employee_runtime_definitions=runtime_definitions,
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
            resolve_day_id("today", clock.now(), config.boundary_hour),
            ticket.id,
            clock.now_unix(),
        )
    app = create_app(
        config,
        clock,
        lambda: connect(str(db_path)),
        conversation_test_options=ConversationTestOptions(
            employee_runtime_definitions=runtime_definitions,
        ),
    )
    return app, db_path, ticket.id


def _wait_for_ticket(client: TestClient, ticket_id: str, predicate) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/tickets/{ticket_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.02)
    raise AssertionError(f"ticket condition not met: {last!r}")


def _receive_until(
    websocket: WebSocketTestSession,
    predicate,
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


def _run_opening_and_human_turn(
    client: TestClient,
    db_path: Path,
    ticket_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dispatched = client.post(f"/api/test/run-step/{ticket_id}")
    assert dispatched.status_code == 200, dispatched.text
    opened = _wait_for_ticket(
        client,
        ticket_id,
        lambda ticket: ticket["ticket_status"] == "paired_work",
    )
    assert opened["employee_session_id"] is not None

    with client.websocket_connect("/api/conversation") as websocket:
        websocket.send_json({"type": "attach", "employeeId": ticket_id})
        replay = _receive_until(websocket, _is_ready)
        reset = next(item for item in replay if item["payload"].get("state") == "reset")
        assert reset["acpSessionId"] == opened["employee_session_id"]
        websocket.send_json(
            {
                "type": "prompt",
                "employeeId": ticket_id,
                "clientMessageId": "new-worker-human-1",
                "deliveryChoice": "normal",
                "prompt": [
                    {
                        "type": "text",
                        "text": "Continue the Understanding conversation.",
                    }
                ],
            }
        )
        turn = _receive_until(websocket, _is_idle)

    with connect(str(db_path)) as conn:
        binding = conn.execute(
            "SELECT acp_session_id, binding_generation "
            "FROM conversation_session_bindings WHERE employee_id = ?",
            (ticket_id,),
        ).fetchone()
        run = conn.execute(
            "SELECT status, employee_session_id FROM employee_step_runs WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
    assert binding is not None and tuple(binding) == (opened["employee_session_id"], 1)
    assert run is not None and tuple(run) == ("complete", opened["employee_session_id"])
    return opened, turn


def test_new_worker_understanding_uses_one_acp_session_for_automatic_and_human_demand(
    tmp_path: Path,
) -> None:
    app, db_path, ticket_id = _application(tmp_path)
    with TestClient(app) as client:
        opened, turn = _run_opening_and_human_turn(client, db_path, ticket_id)
        after = client.get(f"/api/tickets/{ticket_id}").json()

    assert opened["stage"] == "needs_understanding"
    assert opened["fields"]["understanding"]["proposal"] is None
    assert after["employee_session_id"] == opened["employee_session_id"]
    assert after["ticket_status"] == "paired_work"
    assert "typed answer" in json.dumps(turn)


def test_new_worker_understanding_proposal_parks_and_public_accept_advances(
    tmp_path: Path,
) -> None:
    app, db_path, ticket_id = _application(tmp_path)
    with TestClient(app) as client:
        _run_opening_and_human_turn(client, db_path, ticket_id)
        proposed = client.post(
            f"/api/tickets/{ticket_id}/propose",
            headers=_AGENT,
            json={
                "body": "Purpose, judgment risks, and boundaries captured.",
                "recap": "Understanding ready.",
            },
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["ticket_status"] == "awaiting_approval"
        accepted = client.post(
            f"/api/tickets/{ticket_id}/accept/understanding",
            json={"next_ceiling": "needs_stages", "at_cap": "propose"},
        )

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["stage"] == "needs_stages"
    assert body["fields"]["understanding"]["proposal"] is None
    assert body["fields"]["understanding"]["value"].startswith("Purpose")
