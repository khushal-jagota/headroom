"""Ordinary writes preserve exact tokens. Retired correction routes cannot mutate them."""

from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support.executor_catalog import ScriptedBackendSnapshots

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket


def test_http_scoped_read_exact_write_and_retired_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.runtime import conversation_start

    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda conn: tmp_path)
    db_path = str(tmp_path / "configuration.db")
    with closing(connect(db_path)) as conn:
        create_schema(conn)
        worker = create_ticket(
            conn,
            title="Exact configuration",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type="coding",
            employee_backend="codex",
            employee_launch_model="gpt-6.1-terra",
            employee_launch_reasoning_effort="high",
        )
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path})
    app = create_app(
        cfg,
        build_clock(cfg),
        lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        runtime = app.state.conversation
        source = ScriptedBackendSnapshots()
        source.model = "gpt-6.1-terra"
        object.__setattr__(runtime, "backend_snapshots", source)
        start = client.get(f"/api/tickets/{worker.id}/conversation/start-values")
        assert start.status_code == 200, start.text
        assert start.json()["workspace_folder"] == str(tmp_path)
        assert start.json()["workspace_path_policy"] == "legacy_home_mapping"
        refreshed = client.post(
            "/api/conversation/backends?refresh=true", json={"ticket_id": worker.id}
        )
        assert refreshed.status_code == 200, refreshed.text
        assert source.calls
        assert "backends" in refreshed.json() and "rows" not in refreshed.json()
        assert any(
            model["model_id"] == "gpt-6.1-terra"
            for snapshot in refreshed.json()["backends"]
            for model in snapshot["available_models"]
        )
        invalid = client.post(
            "/api/conversation/backends",
            json={
                "ticket_id": worker.id,
                "executable": "/not-authorized",
            },
        )
        assert invalid.status_code == 400
        forbidden = client.post(
            "/api/conversation/backends",
            json={"ticket_id": worker.id},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "outside"},
        )
        assert forbidden.status_code in (400, 403, 404)
        from typing import Any, cast

        from planner.tickets.contracts import TicketLocale

        mac_source = ScriptedBackendSnapshots()
        mac_source.model = "mac-only-model"
        runtime.system.configure_machine_catalog(cast(Any, mac_source))
        with closing(connect(db_path)) as conn:
            mac_ticket = create_ticket(
                conn, title="Mac Hermes", principal=OWNER_PRINCIPAL, now=1,
                title_max_chars=200, worker_type="coding", locale=TicketLocale.macos,
                employee_backend="hermes", employee_launch_model="mac-only-model",
            )
        mac_read = client.post("/api/conversation/backends", json={"ticket_id": mac_ticket.id})
        assert mac_read.status_code == 200, mac_read.text
        assert {item["backend_key"] for item in mac_read.json()["backends"]} == {
            "codex", "claude", "hermes"}
        assert all(item["available_models"][0]["model_id"] == "mac-only-model"
                   for item in mac_read.json()["backends"])
        source.calls = 0
        for suffix in ("preview", "apply"):
            response = client.post(f"/api/conversation/model-corrections/{suffix}", json={})
            assert response.status_code == 404
        assert client.post("/api/conversation/backends/model-choices", json={}).status_code == 404
        defaults = {
            "employee_backend": "codex",
            "employee_launch_model": "provider-next-token",
            "employee_launch_reasoning_effort": "future-effort",
        }
        response = client.put("/api/workers/chief-of-staff/launch-defaults", json=defaults)
        assert response.status_code == 200, response.text
        with closing(connect(db_path)) as conn:
            saved = conn.execute(
                "SELECT employee_launch_model,employee_launch_reasoning_effort FROM chief_settings"
            ).fetchone()
            assert tuple(saved) == ("provider-next-token", "future-effort")
        disabled = BackendStateStore(db_path)
        disabled.write_model_enablement(Backend.codex, "provider-next-token", False)
        assert (
            client.put("/api/workers/chief-of-staff/launch-defaults", json=defaults).status_code
            == 400
        )
        assert source.calls == 0
