"""Synthetic proofs across authenticated preview, owner IPC and native catalog.

The existing TLS/WebSocket host is reused. Only provider probes, destination
filesystem localization and private input vectors are synthetic. This protects
binding and failure isolation that disconnected classifier/API tests cannot prove.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pydantic import TypeAdapter
from tests.integration.test_executor_end_to_end import boot, eventually
from tests.unit.test_claude_settings_snapshot import SECRET, bench
from tests.unit.test_destination_catalog_observer import (
    ScriptedLaunchEnvironment,
    launch_fixture,
)

from planner.conversation import api
from planner.conversation.backends import claude_settings
from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsScopeRegistry,
    read_claude_catalog_settings_snapshot,
)
from planner.conversation.contracts import WorkspacePathPolicy
from planner.conversation.model_resolution import diagnostics, observer
from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDirectory,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
    ClaudeJsonValue,
)
from planner.conversation.model_resolution.contracts import (
    CatalogRequest,
    CatalogWorkspaceBinding,
)
from planner.conversation.model_resolution.diagnostic_contracts import validate_record
from planner.conversation.model_resolution.logic.claude_configuration import ClaudeConfigurationRole
from planner.conversation.runtime_composition import ConversationRuntime
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.core.server import handle_planner_error
from planner.executor import catalog_diagnostics as native_diagnostics
from planner.executor.catalog_diagnostic_operator import retrieve_once
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



def _private_vector(
    path: Path, after: bool, scenario: str,
) -> ClaudeInputVector | None:
    """Bounded synthetic source records enter through the production capture seam."""
    if scenario == "capture_missing" and after:
        return None
    location = ClaudeInputLocation(path / "private.json", path / "private.json")
    credential = ClaudeCredentialMarker(
        ClaudeInputLocation(path / "credential", path / "credential"), path,
        (0,) * 32768 if scenario == "limit_work" else (1, 2, 3),
    )
    value = "private-after" if after else "private-before"
    content = ClaudeJsonValue("object", (
        ("private-sentinel", ClaudeJsonValue("string", value)),
        ("nested", ClaudeJsonValue("array", (
            ClaudeJsonValue("boolean", True), ClaudeJsonValue("integer", 7),
            ClaudeJsonValue("object", (("model", ClaudeJsonValue("string", "opus")),)),
        ))),
    ))
    documents = [ClaudeInputDocument(ClaudeConfigurationRole.global_state, location, content)]
    payload = content
    null = ClaudeJsonValue("null", None)
    if scenario == "limit_nodes":
        payload = ClaudeJsonValue("array", (null,) * 32768)
    elif scenario == "limit_sequence":
        payload = ClaudeJsonValue("array", (null,) * 32769)
    elif scenario == "limit_depth":
        for _ in range(66):
            payload = ClaudeJsonValue("array", (payload,))
    elif scenario == "limit_string":
        payload = ClaudeJsonValue("string", "s" * 4097)
    elif scenario == "limit_integer":
        payload = ClaudeJsonValue("integer", 1 << 4096)
    elif scenario == "limit_scalars":
        payload = ClaudeJsonValue("array", (ClaudeJsonValue("string", "s" * 1000),) * 300)
    elif scenario == "internal_shape":
        payload = ClaudeJsonValue("boolean", cast(Any, "private-invalid-boolean"))
    for role in (ClaudeConfigurationRole.settings, ClaudeConfigurationRole.managed_settings,
                 ClaudeConfigurationRole.remote_policy_cache):
        documents.append(ClaudeInputDocument(role, location, payload))
    if scenario == "limit_documents":
        documents.extend(ClaudeInputDocument(ClaudeConfigurationRole.settings, location, content)
                         for _ in range(61))
    candidates = tuple(path / f"candidate-{index}" for index in range(
        257 if scenario == "limit_candidates" else 2
    ))
    return ClaudeInputVector("preferred", tuple(documents),
                             (ClaudeInputDirectory(location, candidates),), credential)


def _ordinary_facts(value: object) -> object:
    """Compare complete domain output except per-capture reference/time identities."""
    if isinstance(value, dict):
        return {key: (
            "per-capture-reference" if key == "context_ref" and isinstance(item, str)
            else 0 if key == "observed_at" and type(item) is int
            else _ordinary_facts(item)
        ) for key, item in value.items()}
    if isinstance(value, list):
        return [_ordinary_facts(item) for item in value]
    return value


@pytest.mark.parametrize("scenario", [
    "compared", "collector_compared", "capture_missing", "provider_missing", "provider_changed",
    "duplicate_hook", "internal_shape", "finalization_error", "cancelled",
    "limit_documents", "limit_candidates", "limit_nodes", "limit_depth", "limit_string",
    "limit_integer", "limit_scalars", "limit_sequence", "limit_work", "limit_time",
])
def test_authenticated_preview_binds_one_native_diagnostic_and_preserves_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
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
            source_file = tmp_path / ".claude" / "settings.json"
            source_file.parent.mkdir()
            source_before = json.dumps({
                "env": {"SYNTHETIC_PRIVATE": "private-before"},
                "permissions": {"allow": ["Read(private-sentinel)"], "deny": []},
                "nested": {"models": ["opus", {"enabled": True, "budget": 7}]},
            })
            source_after = source_before.replace("private-before", "private-after")
            source_file.write_text(source_before)
            original_configuration = observer._configuration_revisions
            opened_sources: list[str] = []
            if scenario == "collector_compared":
                original_open = os.open

                def open_source(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
                    if flags & os.O_NONBLOCK and str(path).endswith(".json"):
                        source = Path(path)
                        # Prove all document bytes come from synthetic files.
                        assert source.is_relative_to(tmp_path)
                        opened_sources.append(str(source.relative_to(tmp_path)))
                    return original_open(path, flags, *args, **kwargs)

                monkeypatch.setattr(os, "open", open_source)

            def vector(*args: Any) -> Any:
                calls.append("capture-input")
                if scenario == "collector_compared":
                    result = original_configuration(*args)
                    assert isinstance(result, ClaudeInputVector)
                    assert all(type(document.role) is ClaudeConfigurationRole
                               for document in result.documents)
                    return result
                return _private_vector(tmp_path, current[0], scenario)

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
                if scenario == "provider_changed":
                    original_send = inner.send

                    async def change_sources(data: str) -> None:
                        frame = json.loads(data)
                        if frame.get("request", {}).get("subtype") == "list_models":
                            inner.settings["sources"][0]["settings"]["model"] = "sonnet"
                        await original_send(data)

                    monkeypatch.setattr(inner, "send", change_sources)
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
                if scenario == "collector_compared":
                    source_file.write_text(source_after)
                assert claude_scope_registry is not None
                return ClaudeModelCatalog(
                    (ClaudeModel("opus", "Opus", "claude-opus-5", ()),),
                    (),
                    provider_scope=claude_scope_registry.retain(sample.settings),
                )

            class SyntheticDestination(observer.ProductionCatalogObserver):
                def _launch(self, *args: Any, **kwargs: Any) -> Any:
                    executable, launch_environment = super()._launch(*args, **kwargs)
                    if scenario == "limit_work":
                        launch_environment.update((f"K{index:04x}", "") for index in range(24000))
                    return executable, launch_environment

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
                    if scenario == "collector_compared":
                        source_file.write_text(source_before)
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
                expected_reason = (
                    "catalog_unavailable" if scenario == "provider_changed" else "context_changed"
                )
                assert baseline["validation"]["reason"] == expected_reason
                baseline_calls = list(calls)
                baseline_probes = list(environment.calls)
                baseline_opened_sources = list(opened_sources)
                opened_sources.clear()
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

                def diagnostic_finish(sink: diagnostics.DiagnosticSink) -> JsonObject:
                    record = finish(sink)
                    completed.append(record)
                    if scenario == "finalization_error":
                        raise RuntimeError("private diagnostic failure sentinel")
                    return record

                monkeypatch.setattr(diagnostics.DiagnosticSink, "finish", diagnostic_finish)
                # A diagnostic-only clock makes resource limits deterministic and
                # never changes transport, provider or observer deadline clocks.
                clock_ticks = [0]

                def diagnostic_clock() -> float:
                    clock_ticks[0] += 1
                    return clock_ticks[0] * 0.01 if scenario == "limit_time" else 0.0

                sink_type = diagnostics.DiagnosticSink
                if scenario.startswith("limit_"):
                    monkeypatch.setattr(native_diagnostics, "DiagnosticSink",
                                        lambda: sink_type(clock=diagnostic_clock))
                if scenario == "provider_missing":
                    monkeypatch.setattr(claude_settings, "offer_provider_comparison",
                                        lambda _: None)
                if scenario == "duplicate_hook":
                    offer = diagnostics.offer_capture

                    def duplicate(value: diagnostics.DiagnosticCapture) -> None:
                        offer(value)
                        offer(value)

                    monkeypatch.setattr(observer, "offer_capture", duplicate)
                if scenario == "cancelled":
                    request = TypeAdapter(CatalogRequest).validate_json(json.dumps({
                        "backend_key": arm["backend_key"], "expected_context": expected,
                    }))
                    assert harness.endpoint.identity is not None
                    assert harness.endpoint.writer is not None
                    await harness.endpoint._cancel_catalog(
                        arm["request_id"], request, harness.endpoint.identity,
                        harness.endpoint.writer, diagnostic=True,
                    )
                    await eventually(lambda: harness.mac._catalog_diagnostics.state == "settled",
                                     "pending explicit cancellation did not arrive")
                async with owner.post(
                    harness.proxy.origin + PREVIEW, json={"ticket_id": TARGET}, headers=headers
                ) as response:
                    assert response.status == 200
                    observed = await response.json()
                assert observed["validation"]["reason"] == expected_reason
                assert _ordinary_facts(observed) == _ordinary_facts(baseline)
                assert calls == baseline_calls
                assert calls == ([
                        "capture-input",
                        "catalog",
                        "initialize",
                        "get_settings",
                        "list_models",
                        "get_settings",
                        "capture-input",
                    ] if scenario != "provider_changed" else [
                        "capture-input", "catalog", "initialize", "get_settings", "list_models",
                        "get_settings",
                    ])
                assert environment.calls == baseline_probes
                assert opened_sources == baseline_opened_sources
                if scenario == "collector_compared":
                    assert opened_sources == [".claude/settings.json"] * 4
                assert attempts[-1] == arm["request_id"] and attempts[0] != arm["request_id"]
                await eventually(
                    lambda: harness.mac._catalog_diagnostics.state == "settled",
                    "diagnostic receipt did not settle",
                )
                retrievals: list[bool] = []
                output_path = tmp_path / "validated-receipt.json"
                loop = asyncio.get_running_loop()

                def retrieve() -> object:
                    retrievals.append(True)
                    response = asyncio.run_coroutine_threadsafe(_owner_ipc(
                        harness.mac.paths.ipc_socket, "catalog_diagnostic_result",
                        {"request_id": arm["request_id"]},
                    ), loop).result(timeout=5)
                    assert response.error is None
                    return response.result

                def preserve(value: JsonObject) -> None:
                    output_path.write_text(json.dumps(value))

                receipt = await asyncio.to_thread(retrieve_once, retrieve, preserve)
                assert retrievals == [True]
                assert json.loads(output_path.read_text()) == receipt
                assert validate_record(receipt) == receipt
                with pytest.raises(ValueError):
                    validate_record({**receipt, "unexpected": "private-sentinel"})
                expected_status = {
                    "collector_compared": "compared",
                    "provider_missing": "compared", "provider_changed": "capture_missing",
                    "duplicate_hook": "internal_error", "internal_shape": "internal_error",
                    "finalization_error": "internal_error",
                }.get(scenario, scenario)
                assert receipt["diagnostic_status"] == expected_status
                provider = "equal"
                if scenario in {"provider_missing", "duplicate_hook", "internal_shape",
                                "finalization_error", "cancelled"}:
                    provider = "unavailable"
                elif scenario == "provider_changed":
                    provider = "changed"
                assert receipt["provider_source_snapshot"] == provider
                if expected_status != "compared":
                    unavailable = diagnostics.unavailable_record()
                    for key in unavailable:
                        if key not in {"diagnostic_status", "provider_source_snapshot"}:
                            assert receipt[key] == unavailable[key]
                assert len(completed) == (0 if scenario == "cancelled" else 1)
                if scenario in {"compared", "collector_compared", "provider_missing",
                                "finalization_error"}:
                    assert completed[0]["diagnostic_status"] == "compared"
                    roles = completed[0]["roles"]
                    assert isinstance(roles, dict)
                    changed_role = "settings" if scenario == "collector_compared" else (
                        "selected_global_state"
                    )
                    assert roles[changed_role] == {
                        "source_validity": "equal", "semantic_content": "changed",
                    }
                    for key in ("executable_identity", "executable_version", "launch_environment",
                                "workspace", "executor_identity"):
                        assert completed[0][key] == "equal"
                encoded = json.dumps(receipt)
                assert len(encoded.encode()) <= 4096
                for private in (
                    SECRET,
                    "private-invalid-boolean",
                    "private-before",
                    "private-after",
                    "private-sentinel",
                    "private diagnostic failure sentinel",
                    str(WORKSPACE),
                    str(tmp_path),
                ):
                    assert private not in encoded
                print("finite diagnostic evidence: " + json.dumps({
                    "scenario": scenario, "receipt": receipt,
                }))
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
    print(f"connected diagnostic {scenario}: {time.monotonic() - started:.3f}s")
