"""One-shot authority gaps: pending cancellation, receipt fate and captured transport.

Use existing in-process transport fixtures. These cases add no server or provider.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from click.testing import CliRunner
from tests.unit.test_executor_request_deadlines import (
    catalog_endpoint,
    catalog_native,
    peer,
)

from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution.contracts import (
    CatalogExpectedContext,
    CatalogRequest,
    CatalogWorkspaceBinding,
    ModelSelection,
    SelectionKind,
)
from planner.conversation.model_resolution.diagnostics import unavailable_record
from planner.executor import catalog_diagnostics as diagnostic
from planner.executor import ipc_client
from planner.executor.cli import catalog_diagnostic_arm, catalog_diagnostic_result
from planner.executor.contracts import (
    IpcOperation,
    IpcRequest,
    IpcResponse,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import as_object
from planner.tickets.contracts import TicketLocale


def binding(identity: Any) -> tuple[str, CatalogRequest, dict[str, Any]]:
    request_id = str(uuid4())
    request = CatalogRequest(
        ConversationBackendKey.claude,
        CatalogExpectedContext(
            identity,
            CatalogWorkspaceBinding(
                diagnostic.DIAGNOSTIC_WORKSPACE, WorkspacePathPolicy.native_absolute, None
            ),
        ),
    )
    return request_id, request, {"request_id": request_id, **as_object(request)}


def test_pending_cancel_is_terminal_before_catalog_task_arrives() -> None:
    async def run() -> None:
        executor = catalog_native(peer())
        assert executor._writer is not None
        request_id, request, payload = binding(executor.identity)
        executor._catalog_diagnostics.arm(payload, executor.identity, executor._writer)
        executor._catalog_frame(
            WireMessage(WireMessageKind.catalog_cancel, request_id, as_object(request))
        )
        assert not executor._catalog_tasks
        assert executor._catalog_diagnostics.begin(request_id, request, executor._writer) is None
        assert (
            executor._catalog_diagnostics.result({"request_id": request_id}) == unavailable_record()
        )
        with pytest.raises(ValueError):
            executor._catalog_diagnostics.arm(payload, executor.identity, executor._writer)
        with pytest.raises(ValueError):
            executor._catalog_diagnostics.result({"request_id": request_id})

    asyncio.run(run())


def test_arm_expiry_and_receipt_expiry_never_restore_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        clock = [1.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        controller = diagnostic.NativeCatalogDiagnostics()
        executor = catalog_native(peer())
        assert executor._writer is not None
        request_id, request, payload = binding(executor.identity)
        controller.arm(payload, executor.identity, executor._writer)
        clock[0] += 61
        assert controller.begin(request_id, request, executor._writer) is None
        assert controller.state == "settled"
        clock[0] += 61
        with pytest.raises(ValueError):
            controller.result({"request_id": request_id})
        assert controller.request is None and controller.receipt is None
        assert controller.request_id is None
        with pytest.raises(ValueError):
            controller.arm(payload, executor.identity, executor._writer)

    asyncio.run(run())


def test_native_rejects_child_claims_and_closed_oversized_arm_before_admission() -> None:
    async def run() -> None:
        executor = catalog_native(peer())
        assert executor._writer is not None
        executor._ipc_stopping = False
        _, _, payload = binding(executor.identity)
        for child, capability in (("child", None), (None, "capability")):
            response = await executor.handle_ipc(
                IpcRequest(
                    str(uuid4()),
                    IpcOperation.catalog_diagnostic_arm,
                    child,
                    capability,
                    payload,
                )
            )
            assert response.error is not None
            assert executor._catalog_diagnostics.state == "unused"
        for location in ("top", "context", "workspace", "identity", "oversize"):
            value = json.loads(json.dumps(payload))
            if location == "top":
                value["unknown"] = True
            elif location == "oversize":
                value["request_id"] = "x" * 4097
            else:
                target = value["expected_context"]
                if location == "workspace":
                    target = target["workspace"]
                elif location == "identity":
                    target = target["executor_identity"]
                target["unknown"] = True
            with pytest.raises(ValueError):
                executor._catalog_diagnostics.arm(value, executor.identity, executor._writer)
            assert executor._catalog_diagnostics.state == "unused"
        assert executor._catalog_diagnostics.arm(payload, executor.identity, executor._writer) == {
            "armed": True
        }

    asyncio.run(run())


def test_active_disconnect_receipt_is_unavailable_after_physical_cleanup() -> None:
    async def run() -> None:
        executor = catalog_native(peer())
        assert executor._writer is not None
        request_id, request, payload = binding(executor.identity)
        controller = executor._catalog_diagnostics
        controller.arm(payload, executor.identity, executor._writer)
        assert controller.begin(request_id, request, peer()) is None
        assert controller.state == "armed"
        sink = controller.begin(request_id, request, executor._writer)
        assert sink is not None
        controller.invalidate()
        assert controller.state == "active" and controller.sink is sink
        with pytest.raises(ValueError):
            controller.result({"request_id": request_id})
        controller.finish(request_id)
        assert controller.sink is None and controller.request is None
        assert controller.result({"request_id": request_id}) == unavailable_record()

    asyncio.run(run())


def test_reservation_exact_selection_writer_revision_and_scope_reset() -> None:
    writer = peer()
    writer.is_closing.return_value = False
    endpoint = catalog_endpoint(writer)
    assert endpoint.identity is not None
    _, request, _ = binding(endpoint.identity)
    selection = ModelSelection(
        TicketLocale.macos,
        ConversationBackendKey.claude,
        "opus",
        SelectionKind.legacy_unclassified,
        None,
        "revision-1",
    )
    workspace = request.expected_context.workspace
    reservation = diagnostic.DiagnosticReservation.create(
        diagnostic.DIAGNOSTIC_TICKET_ID,
        selection,
        workspace,
        endpoint,
    )
    for changed in (
        replace(selection, reasoning_effort="high"),
        replace(selection, selection_revision="revision-2"),
    ):
        with pytest.raises(ValueError):
            reservation.check(changed, workspace, endpoint)
    endpoint.writer = peer()
    with pytest.raises(ValueError):
        reservation.check(selection, workspace, endpoint)
    with pytest.raises(RuntimeError), diagnostic.reservation_scope(reservation):
        assert diagnostic.current_reservation() is reservation
        raise RuntimeError("synthetic")
    assert diagnostic.current_reservation() is None


def test_cancellation_keeps_ordinary_guard_and_uses_original_diagnostic_writer() -> None:
    async def run() -> None:
        original, replacement = peer(), peer()
        original.is_closing.return_value = False
        endpoint = catalog_endpoint(original)
        identity = endpoint.identity
        assert identity is not None
        request_id, request, _ = binding(identity)
        endpoint.writer = replacement
        await endpoint._cancel_catalog(request_id, request, identity, original)
        original.write.assert_not_called()
        await endpoint._cancel_catalog(request_id, request, identity, original, diagnostic=True)
        original.write.assert_called_once()
        replacement.write.assert_not_called()

    asyncio.run(run())


def test_cli_arm_caps_raw_input_and_result_uses_real_strict_json_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import planner.executor.cli as cli

    runner = CliRunner()
    calls: list[tuple[IpcOperation, dict[str, Any]]] = []

    def request(operation: IpcOperation, payload: dict[str, Any]) -> IpcResponse:
        calls.append((operation, payload))
        return IpcResponse("response", unavailable_record(), None)

    monkeypatch.setattr(cli, "request", request)
    rejected = runner.invoke(catalog_diagnostic_arm, input=" " * 4097)
    assert rejected.exit_code != 0 and not calls
    request_id = str(uuid4())
    answer = runner.invoke(catalog_diagnostic_result, ["--request-id", request_id])
    assert answer.exit_code == 0 and len(calls) == 1
    assert calls[0] == (IpcOperation.catalog_diagnostic_result, {"request_id": request_id})
    # Reach connection setup through the real synchronous decoder. No socket retry occurs.
    monkeypatch.delenv("PLAN_EXECUTOR_CHILD_ID", raising=False)
    monkeypatch.delenv("PLAN_EXECUTOR_CAPABILITY", raising=False)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args: (_ for _ in ()).throw(
            OSError("synthetic socket"),
        ),
    )
    with pytest.raises(OSError, match="synthetic socket"):
        ipc_client.request(IpcOperation.catalog_diagnostic_result, {"request_id": request_id})
