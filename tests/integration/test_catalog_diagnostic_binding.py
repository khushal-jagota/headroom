"""One synthetic proof across authenticated preview, owner IPC and native catalog.

The existing TLS/WebSocket host is reused. Only provider probes, destination
filesystem localization and private input vectors are synthetic. This protects
binding and failure isolation that disconnected classifier/API tests cannot prove.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from tests.integration.test_executor_end_to_end import boot, eventually
from tests.unit.test_claude_settings_snapshot import bench
from tests.unit.test_destination_catalog_observer import (
    ScriptedLaunchEnvironment,
    launch_fixture,
)

from planner.conversation import api
from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsScopeRegistry,
    read_claude_catalog_settings_snapshot,
)
from planner.conversation.contracts import WorkspacePathPolicy
from planner.conversation.model_resolution import diagnostics, observer
from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
    ClaudeJsonValue,
)
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding
from planner.conversation.runtime_composition import ConversationRuntime
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.core.server import handle_planner_error
from planner.executor.contracts import IpcOperation, IpcRequest, IpcResponse, JsonObject
from planner.executor.wire import encode_frame, read_typed_frame
from planner.projects.data import create_project
from planner.tickets import data as tickets
from planner.tickets.contracts import TicketLocale

pytestmark = pytest.mark.usefixtures("executor_transport_home")
TARGET = "t_mw0fvv1n"
WORKSPACE = Path("/Users/khushaljagota/panels-dogfood-t_0471dgaa")
PREVIEW = "/api/conversation/backends/model-choices"
RESERVE = PREVIEW + "/diagnostic-reservation"
HEADER = "X-Panels-Catalog-Diagnostic-Reservation"


async def _owner_ipc(
    socket: Path,
    operation: str,
    payload: JsonObject,
    *,
    child: bool = False,
) -> IpcResponse:
    reader, writer = await asyncio.open_unix_connection(str(socket))
    try:
        writer.write(
            encode_frame(
                IpcRequest(
                    str(uuid4()),
                    IpcOperation(operation),
                    "forbidden-child" if child else None,
                    "forbidden-capability" if child else None,
                    payload,
                )
            )
        )
        await writer.drain()
        return await asyncio.wait_for(read_typed_frame(reader, IpcResponse), 2)
    finally:
        writer.close()
        await writer.wait_closed()


def test_authenticated_preview_binds_one_native_diagnostic_and_preserves_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = time.monotonic()

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            # Create the exact reviewed identity in this fixture's isolated database.
            with closing(connect(harness.endpoint.db_path)) as conn:
                project = create_project(
                    conn, name="Synthetic original target", folder_path=WORKSPACE, now=1
                )
                with monkeypatch.context() as ids:
                    ids.setattr(tickets, "new_id", lambda prefix: TARGET)
                    target = tickets.create_ticket(
                        conn,
                        title="Synthetic diagnostic target",
                        worker_type="coding",
                        principal=OWNER_PRINCIPAL,
                        now=1,
                        title_max_chars=200,
                        locale=TicketLocale.macos,
                        project_id=project.id,
                        employee_backend="claude",
                        employee_launch_model="opus",
                        selection_kind="legacy_unclassified",
                        employee_launch_reasoning_effort=None,
                    )
                before = list(conn.iterdump())
            assert target.id == TARGET
            app = harness.transport_app
            app.include_router(api.router, prefix="/api/conversation")
            app.add_exception_handler(PlannerError, handle_planner_error)
            app.middleware_stack = None
            runtime = cast(
                ConversationRuntime,
                SimpleNamespace(
                    database_path=harness.endpoint.db_path,
                    system=harness.system,
                    executor_endpoint=harness.endpoint,
                ),
            )
            app.dependency_overrides[api._runtime] = lambda: runtime
            calls: list[str] = []
            attempts: list[str] = []
            current = [False]
            launches = launch_fixture(tmp_path, monkeypatch)
            environment = ScriptedLaunchEnvironment()
            location = ClaudeInputLocation(tmp_path / "private.json", tmp_path / "private.json")
            credential = ClaudeCredentialMarker(
                ClaudeInputLocation(tmp_path / "credential", tmp_path / "credential"),
                tmp_path,
                None,
            )

            def vector(*args: object) -> ClaudeInputVector:
                calls.append("capture-input")
                value = "private-after" if current[0] else "private-before"
                return ClaudeInputVector(
                    "preferred",
                    (
                        ClaudeInputDocument(
                            "global_state",
                            location,
                            ClaudeJsonValue(
                                "object", (("private-sentinel", ClaudeJsonValue("string", value)),)
                            ),
                        ),
                    ),
                    (),
                    credential,
                )

            async def probe(
                executable: str,
                *,
                environment: Any = None,
                working_directory: Path | None = None,
                claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
            ) -> ClaudeModelCatalog:
                del executable, environment, working_directory
                calls.append("catalog")
                current[0] = True
                client, transport, inner, _ = bench()
                await client.connect()
                try:
                    sample = await read_claude_catalog_settings_snapshot(transport)
                finally:
                    await client.disconnect()
                calls.extend(
                    frame["request"]["subtype"]
                    for frame in inner.frames
                    if frame.get("type") == "control_request"
                )
                assert claude_scope_registry is not None
                return ClaudeModelCatalog(
                    (ClaudeModel("opus", "Opus", "claude-opus-5", ()),),
                    (),
                    provider_scope=claude_scope_registry.retain(sample.settings),
                )

            class SyntheticDestination(observer.ProductionCatalogObserver):
                def _workspace(self, workspace: CatalogWorkspaceBinding) -> Path:
                    assert workspace == CatalogWorkspaceBinding(
                        WORKSPACE,
                        WorkspacePathPolicy.native_absolute,
                        None,
                    )
                    # No directory is created or read under the real Mac path.
                    return tmp_path

                async def observe(self, *args: Any, **kwargs: Any) -> Any:
                    attempts.append(kwargs["attempt_id"])
                    current[0] = False
                    return await super().observe(*args, **kwargs)

            monkeypatch.setattr(observer, "_configuration_revisions", vector)
            monkeypatch.setattr(observer, "_account_generation", lambda *args: None)
            monkeypatch.setattr(observer, "probe_claude_model_catalog", probe)
            harness.mac._catalog_observer = SyntheticDestination(
                destination=TicketLocale.macos,
                panels_server_url=harness.proxy.origin,
                executor_identity=harness.mac.identity,
                launches=lambda: launches,
                environment=environment,
            )
            async with harness.proxy.session_factory() as owner:
                owner.headers["Accept-Encoding"] = "identity"
                # The proxy supplies a configured trusted owner login over real TLS.
                async with owner.post(
                    harness.proxy.origin + PREVIEW, json={"ticket_id": TARGET}
                ) as response:
                    assert response.status == 200
                    baseline = await response.json()
                assert baseline["validation"]["reason"] == "context_changed"
                baseline_calls = list(calls)
                baseline_probes = list(environment.calls)
                calls.clear()
                environment.calls.clear()
                async with owner.post(
                    harness.proxy.origin + RESERVE, json={"ticket_id": TARGET}
                ) as response:
                    assert response.status == 200
                    reservation = await response.json()
                assert calls == [] and environment.calls == []
                arm = reservation["arm"]
                expected = arm["expected_context"]
                assert arm["backend_key"] == "claude"
                assert expected["workspace"]["workspace_folder"] == str(WORKSPACE)
                assert expected["executor_identity"]["connection_generation"] == (
                    harness.mac.identity.connection_generation
                )
                headers = {HEADER: reservation["reservation"]}
                # Employee requests take ordinary local ingress. Trusted owner ingress
                # deliberately strips employee headers, so these cannot use that proxy.
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://fixture",
                ) as local:
                    for actor_headers in (
                        {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": TARGET},
                        {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": "fixture-manager"},
                    ):
                        for route in (RESERVE, PREVIEW):
                            denied = await local.post(
                                route,
                                json={"ticket_id": TARGET},
                                headers={**headers, **actor_headers},
                            )
                            assert denied.status_code == 400
                            assert denied.json()["error"]["code"] == "agent_forbidden"
                    denied = await local.post(
                        RESERVE,
                        json={"ticket_id": TARGET},
                        headers={
                            "Tailscale-User-Login": "foreign@example.com",
                        },
                    )
                    assert denied.status_code == 403
                assert calls == [] and environment.calls == []
                denied_arm = await _owner_ipc(
                    harness.mac.paths.ipc_socket, "catalog_diagnostic_arm", arm, child=True
                )
                assert denied_arm.error is not None
                armed = await _owner_ipc(
                    harness.mac.paths.ipc_socket, "catalog_diagnostic_arm", arm
                )
                assert armed.error is None and armed.result == {"armed": True}
                completed: list[JsonObject] = []
                finish = diagnostics.DiagnosticSink.finish

                def diagnostic_output_failure(sink: diagnostics.DiagnosticSink) -> JsonObject:
                    # Real hooks and comparator run first. A diagnostic output failure
                    # then forces the finite fallback without altering domain refusal.
                    completed.append(finish(sink))
                    raise RuntimeError("private diagnostic failure sentinel")

                monkeypatch.setattr(diagnostics.DiagnosticSink, "finish", diagnostic_output_failure)
                async with owner.post(
                    harness.proxy.origin + PREVIEW, json={"ticket_id": TARGET}, headers=headers
                ) as response:
                    assert response.status == 200
                    observed = await response.json()
                assert observed["validation"]["reason"] == "context_changed"
                assert observed["validation"]["state"] == baseline["validation"]["state"]
                assert (
                    calls
                    == baseline_calls
                    == [
                        "capture-input",
                        "catalog",
                        "initialize",
                        "get_settings",
                        "list_models",
                        "get_settings",
                        "capture-input",
                    ]
                )
                assert environment.calls == baseline_probes
                assert attempts[-1] == arm["request_id"] and attempts[0] != arm["request_id"]
                await eventually(
                    lambda: harness.mac._catalog_diagnostics.state == "settled",
                    "diagnostic receipt did not settle",
                )
                receipt = await _owner_ipc(
                    harness.mac.paths.ipc_socket,
                    "catalog_diagnostic_result",
                    {"request_id": arm["request_id"]},
                )
                assert receipt.error is None
                assert receipt.result == diagnostics.unavailable_record()
                assert len(completed) == 1
                assert diagnostics.validate_record(completed[0]) == completed[0]
                roles = completed[0]["roles"]
                assert isinstance(roles, dict)
                assert roles["selected_global_state"] == {
                    "source_validity": "equal",
                    "semantic_content": "changed",
                }
                for key in (
                    "executable_identity",
                    "executable_version",
                    "launch_environment",
                    "workspace",
                    "executor_identity",
                    "provider_source_snapshot",
                ):
                    assert completed[0][key] == "equal"
                encoded = json.dumps(receipt.result)
                assert len(encoded.encode()) <= 4096
                for private in (
                    "private-before",
                    "private-after",
                    "private-sentinel",
                    "private diagnostic failure sentinel",
                    str(WORKSPACE),
                    str(tmp_path),
                ):
                    assert private not in encoded
                repeated = await _owner_ipc(
                    harness.mac.paths.ipc_socket,
                    "catalog_diagnostic_result",
                    {"request_id": arm["request_id"]},
                )
                assert repeated.error is not None
                async with owner.post(
                    harness.proxy.origin + PREVIEW, json={"ticket_id": TARGET}, headers=headers
                ) as response:
                    assert response.status != 200
                assert len(attempts) == 2
            with closing(connect(harness.endpoint.db_path)) as conn:
                assert list(conn.iterdump()) == before
        finally:
            await harness.close()

    asyncio.run(run())
    print(f"connected diagnostic binding case: {time.monotonic() - started:.3f}s")
