"""Ordinary writes preserve exact tokens. Retired correction routes cannot mutate them."""

from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from tests.unit.test_current_model_resolution import ScopedCatalogue, context, observation, row

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey as Backend, WorkspacePathPolicy
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket


def test_http_scoped_read_exact_write_and_retired_corrections(tmp_path: Path, monkeypatch) -> None:
    from planner.runtime import conversation_start

    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    db_path = str(tmp_path / "configuration.db")
    with closing(connect(db_path)) as conn:
        create_schema(conn)
        worker = create_ticket(
            conn, title="Exact configuration", principal=OWNER_PRINCIPAL, now=1,
            title_max_chars=200, worker_type="coding", employee_backend="codex",
            employee_launch_model="gpt-6.1-terra", employee_launch_reasoning_effort="high",
        )
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path})
    app = create_app(
        cfg, build_clock(cfg), lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    source = ScopedCatalogue(observation((row("gpt-6.1-terra"),), launch=context(
        workspace=binding, localized_workspace_folder=tmp_path,
    )))
    with TestClient(app) as client:
        runtime = app.state.conversation
        runtime.system.configure_destination_validation(source)
        # Read every provider through the same existing observer fixture.
        original = source.capture_context
        async def capture(backend, *, workspace):
            from dataclasses import replace
            captured = await original(backend, workspace=workspace)
            source.current_context = replace(captured, backend_key=backend, workspace=workspace,
                                             localized_workspace_folder=workspace.workspace_folder)
            source.value = replace(source.value, evidence=replace(
                source.value.evidence, context=source.current_context,
            ))
            return source.current_context
        source.capture_context = capture
        start = client.get(f"/api/tickets/{worker.id}/conversation/start-values")
        assert start.status_code == 200, start.text
        assert start.json()["workspace_folder"] == str(tmp_path)
        assert start.json()["workspace_path_policy"] == "legacy_home_mapping"
        refreshed = client.post("/api/conversation/backends?refresh=true", json={"ticket_id": worker.id})
        assert refreshed.status_code == 200, refreshed.text
        assert source.calls
        assert "backends" in refreshed.json() and "rows" not in refreshed.json()
        assert any(model["model_id"] == "gpt-6.1-terra"
                   for snapshot in refreshed.json()["backends"]
                   for model in snapshot["available_models"])
        invalid = client.post("/api/conversation/backends", json={
            "ticket_id": worker.id, "executable": "/not-authorized",
        })
        assert invalid.status_code == 400
        forbidden = client.post("/api/conversation/backends", json={"ticket_id": worker.id},
                                headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "outside"})
        assert forbidden.status_code in (400, 403, 404)
        source.calls.clear()
        source.fail = True
        for suffix in ("preview", "apply"):
            response = client.post(f"/api/conversation/model-corrections/{suffix}", json={})
            assert response.status_code == 404
        assert client.post("/api/conversation/backends/model-choices", json={}).status_code == 404
        defaults = {"employee_backend": "codex", "employee_launch_model": "provider-next-token",
                    "employee_launch_reasoning_effort": "future-effort"}
        response = client.put("/api/workers/chief-of-staff/launch-defaults", json=defaults)
        assert response.status_code == 200, response.text
        with closing(connect(db_path)) as conn:
            saved = conn.execute("SELECT employee_launch_model,employee_launch_reasoning_effort "
                                 "FROM chief_settings").fetchone()
            assert tuple(saved) == ("provider-next-token", "future-effort")
        disabled = BackendStateStore(db_path)
        disabled.write_model_enablement(Backend.codex, "provider-next-token", False)
        assert client.put("/api/workers/chief-of-staff/launch-defaults", json=defaults).status_code == 400
        assert source.calls == []
