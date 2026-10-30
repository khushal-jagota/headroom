"""Shared Link deadlines and the domain's separate catalog failure authority."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from tests.support.executor_control import MessageReader, deliver, message_link

from planner.core.clock import RealClock
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor import catalog_relay as mac
from planner.executor.catalog_relay import MacCatalogRelay
from planner.executor.mac_agent_gateway import AgentGateway
from planner.executor.mac_service import MacService
from planner.executor.protocol import WireMessage, WireMessageKind
from planner.executor.vps_transport import VpsExecutor

IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
)


async def stalled_send(*_: object) -> None:
    await asyncio.Event().wait()


def peer() -> Any:
    writer = Mock()
    writer.send = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.is_closing.return_value = False
    return writer


def requester(writer: Any) -> MacService:
    executor = MacService.__new__(MacService)
    executor.identity = IDENTITY
    executor.gateway = AgentGateway.__new__(AgentGateway)
    executor.gateway._identity = lambda: executor.identity
    executor.gateway._link = message_link(writer, IDENTITY)
    executor.gateway._handshake_accepted = False
    return executor


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_absolute_request_deadline_covers_send_and_response(blocked: str) -> None:
    async def run() -> None:
        writer = peer()
        link = message_link(writer, IDENTITY)
        if blocked == "drain":
            writer.send.side_effect = stalled_send
        if blocked == "lock":
            await link._lock.acquire()
        try:
            with pytest.raises(TimeoutError):
                await link.request(WireMessageKind.envelope, {}, timeout=0.02)
            assert not link._pending
            writer.close.assert_called_once_with()
            assert writer.send.call_count == (0 if blocked == "lock" else 1)
        finally:
            if blocked == "lock":
                link._lock.release()
            cast_reader = link._reader
            assert isinstance(cast_reader, MessageReader)
            cast_reader.feed_eof()
            await link.wait_closed()

    asyncio.run(run())


def test_timeout_does_not_close_replacement_generation() -> None:
    async def run() -> None:
        original, replacement = peer(), peer()
        first, second = message_link(original, IDENTITY), message_link(replacement, IDENTITY)
        task = asyncio.create_task(first.request(WireMessageKind.envelope, {}, timeout=0.02))
        while not original.send.called:
            await asyncio.sleep(0)
        with pytest.raises(TimeoutError):
            await task
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()
        replacement.send.assert_not_called()
        assert not first._pending and not second._pending

    asyncio.run(run())


def test_generation_change_while_waiting_for_send_never_writes_replacement() -> None:
    async def run() -> None:
        original, replacement = peer(), peer()
        link = message_link(original, IDENTITY)
        await link._lock.acquire()
        task = asyncio.create_task(link.request(WireMessageKind.envelope, {}))
        while not link._pending:
            await asyncio.sleep(0)
        link.close()
        link._lock.release()
        with pytest.raises(ConnectionError):
            await task
        original.send.assert_not_called()
        replacement.send.assert_not_called()
        replacement.close.assert_not_called()
        assert not link._pending

    asyncio.run(run())


def test_external_cancellation_preserves_writer_and_cleans_pending() -> None:
    async def run() -> None:
        writer = peer()
        link = message_link(writer, IDENTITY)
        task = asyncio.create_task(link.request(WireMessageKind.envelope, {}))
        while not writer.send.called:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not link._pending
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["operation", "validation_reply"])
def test_unexpected_request_task_failure_closes_original_generation(failure: str) -> None:
    async def run() -> None:
        endpoint = VpsExecutor(":memory:", RealClock())
        original, replacement = peer(), peer()
        endpoint.runs.identity = IDENTITY
        link = message_link(original, IDENTITY)
        endpoint.runs.link = link

        async def fail(*args: object, **kwargs: object) -> None:
            endpoint.runs.link = message_link(replacement, IDENTITY)
            raise RuntimeError("private operation details")

        handler = AsyncMock(side_effect=fail)
        if failure == "operation":
            endpoint._read = handler  # type: ignore[method-assign]
            payload: Any = {
                "child": {
                    "executor_incarnation": IDENTITY.executor_incarnation,
                    "child_id": "child",
                    "conversation_id": "conversation",
                },
                "path": "/api/tickets",
                "params": {},
            }
        else:
            link.tell = handler  # type: ignore[method-assign]
            payload = {}
        await endpoint._handle(
            WireMessage(WireMessageKind.read_request, "request", payload), IDENTITY, link
        )
        handler.assert_awaited_once()
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()

    asyncio.run(run())


def test_request_task_cancellation_propagates() -> None:
    async def run() -> None:
        endpoint = VpsExecutor(":memory:", RealClock())
        original = peer()

        async def cancel(*args: object) -> None:
            raise asyncio.CancelledError

        endpoint._handle_message = AsyncMock(side_effect=cancel)  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await endpoint._handle(
                WireMessage(WireMessageKind.health, "request", {}),
                IDENTITY,
                message_link(original, IDENTITY),
            )
        original.close.assert_not_called()

    asyncio.run(run())


def test_late_response_after_cancellation_is_ignored() -> None:
    async def run() -> None:
        writer = peer()
        link = message_link(writer, IDENTITY)
        task = asyncio.create_task(link.request(WireMessageKind.envelope, {}))
        while not writer.send.called:
            await asyncio.sleep(0)
        request_id = next(iter(link._pending))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await deliver(link, WireMessage(WireMessageKind.receipt, request_id, {}))
        assert not link._pending and not link.is_closing()

    asyncio.run(run())


def catalog_endpoint(writer: Any) -> VpsExecutor:
    endpoint = VpsExecutor(":memory:", RealClock())
    endpoint.runs.link = message_link(writer, IDENTITY)
    endpoint.runs.identity = ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
    )
    endpoint.runs.ready = True
    endpoint.catalog._tasks = set()
    endpoint.catalog.clock_sample = None
    return endpoint


def catalog_request(identity: ExecutorIdentity) -> Any:
    from pathlib import Path

    from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
    from planner.conversation.model_resolution.contracts import (
        CatalogExpectedContext,
        CatalogRequest,
        CatalogWorkspaceBinding,
    )

    return CatalogRequest(
        ConversationBackendKey.codex,
        CatalogExpectedContext(
            identity,
            CatalogWorkspaceBinding(Path("/workspace"), WorkspacePathPolicy.native_absolute, None),
        ),
    )


async def settle_cancels(endpoint: VpsExecutor) -> None:
    await asyncio.gather(*tuple(endpoint.catalog._tasks))


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_catalog_absolute_deadline_never_closes_shared_writer(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    from planner.conversation.model_resolution.contracts import CatalogUnavailable, ValidationReason
    from planner.executor import catalog_relay as vps

    async def run() -> None:
        monkeypatch.setattr(vps, "CATALOG_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(vps, "CATALOG_CANCEL_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        endpoint = catalog_endpoint(writer)
        assert endpoint.runs.identity is not None and endpoint.runs.link is not None
        if blocked == "drain":
            writer.send.side_effect = stalled_send
        if blocked == "lock":
            await endpoint.runs.link._lock.acquire()
        result = await endpoint.catalog.observe_catalog(
            catalog_request(endpoint.runs.identity), attempt_id="a"
        )
        assert isinstance(result, CatalogUnavailable)
        assert result.reason is ValidationReason.timeout
        assert not endpoint.runs.link._pending
        writer.close.assert_not_called()
        if blocked == "lock":
            endpoint.runs.link._lock.release()
        await settle_cancels(endpoint)
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("revocation", ["cancel", "generation"])
def test_catalog_revocation_cannot_write_replacement_or_accept_late_reply(
    revocation: str,
) -> None:
    from dataclasses import replace

    from planner.executor.protocol import as_object

    async def run() -> None:
        original, replacement = peer(), peer()
        endpoint = catalog_endpoint(original)
        assert endpoint.runs.identity is not None and endpoint.runs.link is not None
        identity = endpoint.runs.identity
        request = catalog_request(identity)
        locked_link = endpoint.runs.link
        await locked_link._lock.acquire()
        task = asyncio.create_task(endpoint.catalog.observe_catalog(request, attempt_id="caller"))
        while not endpoint.runs.link._pending:
            await asyncio.sleep(0)
        request_id = next(iter(endpoint.runs.link._pending))
        if revocation == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            endpoint.runs.link.close()
            endpoint.runs.link = message_link(replacement, IDENTITY)
            endpoint.runs.identity = replace(identity, connection_generation=2)
        locked_link._lock.release()
        if revocation == "generation":
            result = await task
            assert result.kind == "unavailable"
        await settle_cancels(endpoint)
        assert endpoint.runs.link is not None
        await deliver(
            endpoint.runs.link,
            WireMessage(WireMessageKind.catalog_response, request_id, as_object(request)),
        )
        assert not endpoint.runs.link._pending
        replacement.send.assert_not_called()
        assert original.close.call_count == (1 if revocation == "generation" else 0)
        replacement.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "workspace",
        "backend",
        "nested_identity",
        "nested_identity_extra",
        "account_extra",
        "kind",
    ],
)
def test_catalog_reply_binds_context_schema_and_attempt_without_refreshing_time(
    mismatch: str | None,
) -> None:
    from dataclasses import replace

    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.model_resolution.contracts import CatalogObservation, CatalogResponse
    from planner.executor.protocol import ClockSample, as_object

    async def run() -> None:
        writer = peer()
        endpoint = catalog_endpoint(writer)
        assert endpoint.runs.identity is not None and endpoint.runs.link is not None
        identity = endpoint.runs.identity
        request = catalog_request(identity)
        observer = ScriptedCatalogObserver(lambda: identity)
        endpoint.catalog.clock_sample = ClockSample(1000, 1002, 1003, 1005)
        task = asyncio.create_task(endpoint.catalog.observe_catalog(request, attempt_id="caller"))
        while not writer.send.called:
            await asyncio.sleep(0)
        request_id = next(iter(endpoint.runs.link._pending))
        result = await observer.observe(
            request.backend_key,
            workspace=request.expected_context.workspace,
            attempt_id=request_id,
            deadline=0,
        )
        if mismatch == "workspace":
            context = result.evidence.context
            result = replace(
                result,
                evidence=replace(
                    result.evidence,
                    context=replace(
                        context,
                        workspace=replace(context.workspace, workspace_home_relative="different"),
                    ),
                ),
            )
        if mismatch == "backend":
            result = replace(
                result,
                evidence=replace(
                    result.evidence,
                    context=replace(
                        result.evidence.context, backend_key=ConversationBackendKey.claude
                    ),
                ),
            )
        payload = as_object(CatalogResponse(request.backend_key, identity, result))
        if mismatch in ("nested_identity", "nested_identity_extra"):
            # Nested identity must not coerce values or silently discard extras.
            raw: Any = payload["result"]
            nested = raw["evidence"]["context"]["executor_identity"]
            if mismatch == "nested_identity":
                nested["connection_generation"] = "1"
            else:
                nested["unrecognized"] = "must not disappear"
        if mismatch == "account_extra":
            account_payload: Any = payload["result"]
            account_payload["evidence"]["context"]["account_generation"] = {
                "account_id": "account",
                "last_refresh": "generation",
                "credential": "reject",
            }
        kind = WireMessageKind.health if mismatch == "kind" else WireMessageKind.catalog_response
        await deliver(endpoint.runs.link, WireMessage(kind, request_id, payload))
        received = await task
        if mismatch is None:
            assert isinstance(received, CatalogObservation)
            assert received.evidence.attempt_id == "caller"
            assert received.evidence.observed_at == observer.observed_at
            assert received.evidence.clock_uncertainty_seconds == pytest.approx(1.002)
        else:
            assert received.kind == "unavailable"
        await settle_cancels(endpoint)
        writer.close.assert_not_called()

    asyncio.run(run())


def catalog_frame(executor: MacService, frame: WireMessage) -> None:
    link = executor.gateway._link
    assert link is not None
    executor.catalog.handle(frame, link, admit=lambda: executor.gateway.accepting(link))


def catalog_native(writer: Any) -> MacService:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.protocol import ConnectionState

    executor = requester(writer)
    executor.identity = ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
    )
    executor.gateway.state = ConnectionState.ready
    executor.gateway._handshake_accepted = True
    executor.gateway._transport_admission_stopped = False
    executor.catalog = MacCatalogRelay("https://unused.example")
    executor.catalog._catalog_observer = ScriptedCatalogObserver(lambda: executor.identity)
    executor.server_url = "https://unused.example"
    return executor


@pytest.mark.parametrize("invalid", ["non_ready", "stale_identity", "command", "workspace"])
def test_native_catalog_admission_rejects_unready_or_unscoped_authority(invalid: str) -> None:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.protocol import ConnectionState, as_object

    async def run() -> None:
        writer = peer()
        executor = catalog_native(writer)
        observer = executor.catalog._catalog_observer
        assert isinstance(observer, ScriptedCatalogObserver)
        payload: Any = as_object(catalog_request(executor.identity))
        if invalid == "non_ready":
            executor.gateway.state = ConnectionState.reconciling
        elif invalid == "stale_identity":
            payload["expected_context"]["executor_identity"]["connection_generation"] += 1
        elif invalid == "command":
            payload["executable"] = "/arbitrary/provider"
        else:
            payload["expected_context"]["workspace"]["workspace_home_relative"] = "../escape"
        catalog_frame(executor, WireMessage(WireMessageKind.catalog_request, "untrusted", payload))
        await asyncio.sleep(0)
        assert observer.calls == 0
        assert not executor.catalog._catalog_tasks
        writer.send.assert_not_called()
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    "cause",
    [
        "cancel",
        "deadline",
        "deadline_then_cancel",
        "disconnect",
        "quiescence",
        "replacement_writer",
    ],
)
def test_native_catalog_capacity_survives_cancellation_until_physical_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    cause: str,
) -> None:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.protocol import ConnectionState, as_object

    async def run() -> None:
        monkeypatch.setattr(mac, "MAX_CATALOG_OBSERVATIONS", 1)
        monkeypatch.setattr(
            mac, "CATALOG_TIMEOUT_SECONDS", 0.02 if cause.startswith("deadline") else 5
        )
        writer = peer()
        executor = catalog_native(writer)
        observer = executor.catalog._catalog_observer
        assert isinstance(observer, ScriptedCatalogObserver)
        observer.block = True
        observer.cleanup_release.clear()
        request = catalog_request(executor.identity)
        frame = WireMessage(WireMessageKind.catalog_request, "one", as_object(request))
        catalog_frame(executor, frame)
        task = executor.catalog._catalog_tasks["one"][2]
        await observer.entered.wait()
        cleanup: asyncio.Task[None] | None = None
        if cause == "cancel":
            cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            # A mismatched workspace has no authority over the active observation.
            wrong: Any = as_object(request)
            wrong["expected_context"]["workspace"]["workspace_folder"] = "/different"
            catalog_frame(executor, WireMessage(WireMessageKind.catalog_cancel, "one", wrong))
            assert not observer.cancelled.is_set()
            catalog_frame(executor, cancel)
        elif cause == "replacement_writer":
            replacement = peer()
            executor.gateway._link = message_link(replacement, IDENTITY)
            catalog_frame(
                executor, WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            )
            assert not task.cancelling()
            cleanup = asyncio.create_task(executor.catalog.settle())
        elif cause in ("disconnect", "quiescence"):
            if cause == "disconnect":
                executor.gateway.state = ConnectionState.disconnected
            else:
                executor.gateway._transport_admission_stopped = True
            cleanup = asyncio.create_task(executor.catalog.settle())
        await observer.cancelled.wait()
        assert len(executor.catalog._catalog_tasks) == 1
        # Duplicate/late cancellation must not cancel the cleanup await itself.
        if cause in ("cancel", "deadline_then_cancel"):
            cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            catalog_frame(executor, cancel)
            catalog_frame(executor, cancel)
        catalog_frame(executor, WireMessage(WireMessageKind.catalog_request, "two", frame.payload))
        await asyncio.sleep(0)
        assert observer.calls == 1
        assert len(executor.catalog._catalog_tasks) == 1
        observer.cleanup_release.set()
        if cleanup is not None:
            await cleanup
        from planner.core.task_settlement import wait_for_settlement

        await wait_for_settlement(task)
        await asyncio.sleep(0)
        assert writer.send.call_count == (1 if cause == "deadline" else 0)
        assert not executor.catalog._catalog_tasks
        catalog_frame(executor, WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload))
        writer.close.assert_not_called()

    asyncio.run(run())
