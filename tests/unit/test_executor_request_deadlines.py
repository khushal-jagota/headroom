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


@pytest.mark.parametrize("cause", ["cancel", "deadline"])
def test_native_catalog_capacity_survives_cancellation_until_physical_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    cause: str,
) -> None:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.protocol import as_object

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
        if cause == "cancel":
            cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            # A mismatched workspace has no authority over the active observation.
            wrong: Any = as_object(request)
            wrong["expected_context"]["workspace"]["workspace_folder"] = "/different"
            catalog_frame(executor, WireMessage(WireMessageKind.catalog_cancel, "one", wrong))
            assert not observer.cancelled.is_set()
            catalog_frame(executor, cancel)
        await observer.cancelled.wait()
        # Duplicate/late cancellation must not cancel the cleanup await itself.
        cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
        catalog_frame(executor, cancel)
        catalog_frame(executor, cancel)
        catalog_frame(executor, WireMessage(WireMessageKind.catalog_request, "two", frame.payload))
        await asyncio.sleep(0)
        assert observer.calls == 1
        observer.cleanup_release.set()
        from planner.core.task_settlement import wait_for_settlement

        await wait_for_settlement(task)
        await asyncio.sleep(0)
        assert observer.calls == 1
        observer.block = False
        observer.entered.clear()
        catalog_frame(
            executor, WireMessage(WireMessageKind.catalog_request, "three", frame.payload)
        )
        await asyncio.wait_for(observer.entered.wait(), 1)
        assert observer.calls == 2
        await executor.catalog.settle()
        catalog_frame(executor, WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload))
        writer.close.assert_not_called()

    asyncio.run(run())
