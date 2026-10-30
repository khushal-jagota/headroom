"""Request failures disconnect their generation without claiming operation refusal."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from tests.support.executor_control import MessageReader

from planner.executor import mac
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import ExecutorIdentity, WireMessage, WireMessageKind
from planner.executor.mac import MacExecutor
from planner.executor.vps import ExecutorEndpoint


async def stalled_send(*_: object) -> None:
    await asyncio.Event().wait()


def requester(writer: Any) -> MacExecutor:
    executor = MacExecutor.__new__(MacExecutor)
    executor._writer = writer
    executor._pending = {}
    executor._handshake_accepted = False
    executor._write_lock = asyncio.Lock()
    return executor


def peer() -> Any:
    writer = Mock()
    writer.send = AsyncMock()
    return writer


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_absolute_request_deadline_covers_send_and_response(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        executor = requester(writer)
        if blocked == "drain":
            writer.send.side_effect = stalled_send
        if blocked == "lock":
            await executor._write_lock.acquire()
        with pytest.raises(TimeoutError):
            await executor._request(WireMessageKind.envelope, {})
        assert not executor._pending
        writer.close.assert_called_once_with()
        assert writer.send.call_count == (0 if blocked == "lock" else 1)
        if blocked == "lock":
            executor._write_lock.release()

    asyncio.run(run())


def test_timeout_does_not_close_replacement_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        original, replacement = peer(), peer()
        executor = requester(original)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not original.send.called:
            await asyncio.sleep(0)
        executor._writer = replacement
        with pytest.raises(TimeoutError):
            await task
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()
        replacement.send.assert_not_called()
        assert not executor._pending

    asyncio.run(run())


def test_generation_change_while_waiting_for_send_never_writes_replacement() -> None:
    async def run() -> None:
        original, replacement = peer(), peer()
        executor = requester(original)
        await executor._write_lock.acquire()
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not executor._pending:
            await asyncio.sleep(0)
        executor._writer = replacement
        executor._write_lock.release()
        with pytest.raises(ConnectionError):
            await task
        original.send.assert_not_called()
        replacement.send.assert_not_called()
        replacement.close.assert_not_called()
        assert not executor._pending

    asyncio.run(run())


def test_external_cancellation_preserves_writer_and_cleans_pending() -> None:
    async def run() -> None:
        writer = peer()
        executor = requester(writer)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not writer.send.called:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not executor._pending
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["operation", "validation_reply"])
def test_unexpected_request_task_failure_closes_original_generation(failure: str) -> None:
    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        original, replacement = peer(), peer()
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1
        )
        endpoint.identity = identity
        endpoint.writer = original
        endpoint._responses = {}
        endpoint._catalog_responses = {}
        if failure == "operation":

            async def fail(*args: object) -> None:
                endpoint.writer = replacement
                raise RuntimeError("private operation details")

            handler = AsyncMock(side_effect=fail)
            endpoint._read = handler  # type: ignore[method-assign]
            payload: Any = {
                "child": {
                    "executor_incarnation": "incarnation",
                    "child_id": "child",
                    "conversation_id": "conversation",
                },
                "path": "/api/tickets",
                "params": {},
            }
        else:

            async def fail_send(*args: object) -> None:
                endpoint.writer = replacement
                raise RuntimeError("private send details")

            handler = AsyncMock(side_effect=fail_send)
            endpoint._send = handler  # type: ignore[method-assign]
            payload = {}
        await asyncio.create_task(
            endpoint._handle(
                WireMessage(WireMessageKind.read_request, "request", payload), identity, original
            )
        )
        handler.assert_awaited_once()
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()

    asyncio.run(run())


def test_request_task_cancellation_propagates() -> None:
    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        original = peer()

        async def cancel(*args: object) -> None:
            raise asyncio.CancelledError

        endpoint._handle_message = AsyncMock(side_effect=cancel)  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await endpoint._handle(
                WireMessage(WireMessageKind.health, "request", {}),
                ExecutorIdentity(
                    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1
                ),
                original,
            )
        original.close.assert_not_called()

    asyncio.run(run())


def test_late_response_after_timeout_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.executor.wire import encode_message

    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        executor = requester(writer)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not executor._pending:
            await asyncio.sleep(0)
        request_id = next(iter(executor._pending))
        with pytest.raises(TimeoutError):
            await task
        reader = MessageReader()
        reader.feed_data(encode_message(WireMessage(WireMessageKind.receipt, request_id, {})))
        reader.feed_eof()
        with pytest.raises(ConnectionError):
            await executor._read_loop(reader)
        assert not executor._pending

    asyncio.run(run())


# Catalog reads have different failure authority than durable operations. These
# cases reuse the request fixtures above to prove the separate resource boundary.
def catalog_endpoint(writer: Any) -> ExecutorEndpoint:
    endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
    endpoint.writer = writer
    endpoint.identity = ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
    )
    endpoint.ready = True
    endpoint._catalog_responses = {}
    endpoint._responses = {}
    endpoint._send_lock = asyncio.Lock()
    endpoint._tasks = set()
    endpoint.clock_sample = None
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


async def settle_cancels(endpoint: ExecutorEndpoint) -> None:
    await asyncio.gather(*tuple(endpoint._tasks))


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_catalog_absolute_deadline_never_closes_shared_writer(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    from planner.conversation.model_resolution.contracts import CatalogUnavailable, ValidationReason
    from planner.executor import vps

    async def run() -> None:
        monkeypatch.setattr(vps, "CATALOG_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(vps, "CATALOG_CANCEL_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        endpoint = catalog_endpoint(writer)
        assert endpoint.identity is not None
        if blocked == "drain":
            writer.send.side_effect = stalled_send
        if blocked == "lock":
            await endpoint._send_lock.acquire()
        result = await endpoint.observe_catalog(catalog_request(endpoint.identity), attempt_id="a")
        assert isinstance(result, CatalogUnavailable)
        assert result.reason is ValidationReason.timeout
        assert not endpoint._catalog_responses
        writer.close.assert_not_called()
        if blocked == "lock":
            endpoint._send_lock.release()
        await settle_cancels(endpoint)
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("revocation", ["cancel", "generation"])
def test_catalog_revocation_cannot_write_replacement_or_accept_late_reply(
    revocation: str,
) -> None:
    from dataclasses import replace

    from planner.executor.mac import as_object

    async def run() -> None:
        original, replacement = peer(), peer()
        endpoint = catalog_endpoint(original)
        assert endpoint.identity is not None
        identity = endpoint.identity
        request = catalog_request(identity)
        await endpoint._send_lock.acquire()
        task = asyncio.create_task(endpoint.observe_catalog(request, attempt_id="caller"))
        while not endpoint._catalog_responses:
            await asyncio.sleep(0)
        request_id = next(iter(endpoint._catalog_responses))
        if revocation == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            endpoint.writer = replacement
            endpoint.identity = replace(identity, connection_generation=2)
        endpoint._send_lock.release()
        if revocation == "generation":
            result = await task
            assert result.kind == "unavailable"
        await settle_cancels(endpoint)
        assert endpoint.writer is not None
        await endpoint._handle_message(
            WireMessage(WireMessageKind.catalog_response, request_id, as_object(request)),
            endpoint.identity,
            endpoint.writer,
        )
        assert not endpoint._catalog_responses
        replacement.send.assert_not_called()
        original.close.assert_not_called()
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
    from planner.executor.contracts import ClockSample
    from planner.executor.mac import as_object

    async def run() -> None:
        writer = peer()
        endpoint = catalog_endpoint(writer)
        assert endpoint.identity is not None
        identity = endpoint.identity
        request = catalog_request(identity)
        observer = ScriptedCatalogObserver(lambda: identity)
        endpoint.clock_sample = ClockSample(1000, 1002, 1003, 1005)
        task = asyncio.create_task(endpoint.observe_catalog(request, attempt_id="caller"))
        while not writer.send.called:
            await asyncio.sleep(0)
        request_id = next(iter(endpoint._catalog_responses))
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
        await endpoint._handle_message(WireMessage(kind, request_id, payload), identity, writer)
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


def catalog_native(writer: Any) -> MacExecutor:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.contracts import ConnectionState

    executor = requester(writer)
    executor.identity = ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
    )
    executor.state = ConnectionState.ready
    executor._handshake_accepted = True
    executor._transport_admission_stopped = False
    executor._catalog_tasks = {}
    executor._catalog_observer = ScriptedCatalogObserver(lambda: executor.identity)
    executor.server_url = "https://unused.example"
    return executor


@pytest.mark.parametrize("invalid", ["non_ready", "stale_identity", "command", "workspace"])
def test_native_catalog_admission_rejects_unready_or_unscoped_authority(invalid: str) -> None:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.executor.contracts import ConnectionState
    from planner.executor.mac import as_object

    async def run() -> None:
        writer = peer()
        executor = catalog_native(writer)
        observer = executor._catalog_observer
        assert isinstance(observer, ScriptedCatalogObserver)
        payload: Any = as_object(catalog_request(executor.identity))
        if invalid == "non_ready":
            executor.state = ConnectionState.reconciling
        elif invalid == "stale_identity":
            payload["expected_context"]["executor_identity"]["connection_generation"] += 1
        elif invalid == "command":
            payload["executable"] = "/arbitrary/provider"
        else:
            payload["expected_context"]["workspace"]["workspace_home_relative"] = "../escape"
        executor._catalog_frame(WireMessage(WireMessageKind.catalog_request, "untrusted", payload))
        await asyncio.sleep(0)
        assert observer.calls == 0
        assert not executor._catalog_tasks
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

    from planner.executor.contracts import ConnectionState
    from planner.executor.mac import as_object

    async def run() -> None:
        monkeypatch.setattr(mac, "MAX_CATALOG_OBSERVATIONS", 1)
        monkeypatch.setattr(
            mac, "CATALOG_TIMEOUT_SECONDS", 0.02 if cause.startswith("deadline") else 5
        )
        writer = peer()
        executor = catalog_native(writer)
        observer = executor._catalog_observer
        assert isinstance(observer, ScriptedCatalogObserver)
        observer.block = True
        observer.cleanup_release.clear()
        request = catalog_request(executor.identity)
        frame = WireMessage(WireMessageKind.catalog_request, "one", as_object(request))
        executor._catalog_frame(frame)
        task = executor._catalog_tasks["one"][2]
        await observer.entered.wait()
        cleanup: asyncio.Task[None] | None = None
        if cause == "cancel":
            cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            # A mismatched workspace has no authority over the active observation.
            wrong: Any = as_object(request)
            wrong["expected_context"]["workspace"]["workspace_folder"] = "/different"
            executor._catalog_frame(WireMessage(WireMessageKind.catalog_cancel, "one", wrong))
            assert not observer.cancelled.is_set()
            executor._catalog_frame(cancel)
        elif cause == "replacement_writer":
            replacement = peer()
            executor._writer = replacement
            executor._catalog_frame(
                WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            )
            assert not task.cancelling()
            cleanup = asyncio.create_task(executor._settle_catalogs())
        elif cause in ("disconnect", "quiescence"):
            if cause == "disconnect":
                executor.state = ConnectionState.disconnected
            else:
                executor._transport_admission_stopped = True
            cleanup = asyncio.create_task(executor._settle_catalogs())
        await observer.cancelled.wait()
        assert len(executor._catalog_tasks) == 1
        # Duplicate/late cancellation must not cancel the cleanup await itself.
        if cause in ("cancel", "deadline_then_cancel"):
            cancel = WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload)
            executor._catalog_frame(cancel)
            executor._catalog_frame(cancel)
        executor._catalog_frame(WireMessage(WireMessageKind.catalog_request, "two", frame.payload))
        await asyncio.sleep(0)
        assert observer.calls == 1
        assert len(executor._catalog_tasks) == 1
        observer.cleanup_release.set()
        if cleanup is not None:
            await cleanup
        from planner.executor.transfers import wait_for_settlement

        await wait_for_settlement(task)
        await asyncio.sleep(0)
        assert writer.send.call_count == (1 if cause == "deadline" else 0)
        assert not executor._catalog_tasks
        executor._catalog_frame(WireMessage(WireMessageKind.catalog_cancel, "one", frame.payload))
        writer.close.assert_not_called()

    asyncio.run(run())
