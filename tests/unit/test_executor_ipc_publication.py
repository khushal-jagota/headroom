"""Real Unix IPC proves response uncertainty separately from journal acceptance."""

from __future__ import annotations

import asyncio
import errno
import json
import socket
import struct
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from click.testing import CliRunner

from planner.cli.main import main
from planner.conversation.backends.contracts import BackendChild
from planner.executor import ipc_client
from planner.executor.cli import executor_group
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    FileSnapshot,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    JsonObject,
    ReceiptDisposition,
    StopRun,
)
from planner.executor.mac import MacExecutor
from planner.executor.mac_outbox import MacOutbox
from planner.executor.runtime import _Child
from planner.executor.wire import encode_frame, read_typed_frame


@asynccontextmanager
async def ipc_service(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[MacExecutor, _Child]]:
    executor = MacExecutor(
        paths=ExecutorPaths(root),
        revision="a" * 40,
        executor_id="synthetic",
        panels_server_url="https://unused",
    )
    child = _Child(
        ChildReference(executor.identity.executor_incarnation, "child", "c_one"),
        "capability",
        cast(BackendChild, object()),
        ticket_id="t_example",
    )
    executor.registry.children["child"] = child
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(root))
    monkeypatch.setenv("PLAN_TICKET_ID", "t_example")
    monkeypatch.setenv("PLAN_EXECUTOR_CHILD_ID", "child")
    monkeypatch.setenv("PLAN_EXECUTOR_CAPABILITY", "capability")
    executor.paths.run.mkdir(parents=True)
    executor.paths.artifacts.joinpath("t_example").mkdir(parents=True)
    (executor.paths.artifacts / "t_example" / "file").write_text("original bytes")
    handlers: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            await executor._ipc_client(reader, writer)
        finally:
            handlers.discard(task)

    server = await asyncio.start_unix_server(handle, path=executor.paths.ipc_socket)
    try:
        async with server:
            yield executor, child
    finally:
        if handlers:
            await asyncio.gather(*handlers)
        if executor._publications:
            await asyncio.gather(*(row.task for row in executor._publications.values()))
        if executor._ipc_lookups:
            await asyncio.gather(*executor._ipc_lookups)
        executor.spool.close()


def write_request(child: _Child, identity: str, *, budget: float = 0.04) -> IpcRequest:
    return IpcRequest(
        identity,
        IpcOperation.write,
        child.reference.child_id,
        child.capability,
        {
            "operation": "ticket.edit",
            "arguments": {"ticket_id": "t_example", "body": {"recap": "original"}},
        },
        time.monotonic() + budget,
    )


def retry_request(child: _Child, identity: str) -> IpcRequest:
    return IpcRequest(
        str(uuid4()),
        IpcOperation.retry,
        child.reference.child_id,
        child.capability,
        {"submission_id": identity},
        time.monotonic() + 0.03,
    )


def short_client_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ipc_client, "CLI_RESULT_TIMEOUT_SECONDS", 0.3)

    class ShortSocket(socket.socket):
        def settimeout(self, value: float | None) -> None:
            super().settimeout(min(value, 0.05) if value is not None else value)

    monkeypatch.setattr(
        ipc_client,
        "socket",
        SimpleNamespace(
            socket=ShortSocket,
            AF_UNIX=socket.AF_UNIX,
            SOCK_STREAM=socket.SOCK_STREAM,
        ),
    )


def test_actual_caller_timeout_keeps_owner_and_original_snapshot_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered, release = threading.Event(), threading.Event()
        original = MacOutbox._capture
        captures = 0

        def held_snapshot(
            *args: object, **kwargs: object
        ) -> tuple[tuple[FileSnapshot, ...], list[tuple[object, ...]]]:
            nonlocal captures
            captures += 1
            snapshots = original(*args, **kwargs)  # type: ignore[arg-type]
            entered.set()
            assert release.wait(5)
            return snapshots

        monkeypatch.setattr(MacOutbox, "_capture", held_snapshot)
        short_client_socket(monkeypatch)
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            try:
                result = await asyncio.to_thread(
                    CliRunner().invoke, main, ["worker", "recap", "--json"], input="original"
                )
                monkeypatch.setattr(ipc_client, "socket", socket)
                assert entered.is_set()
                assert result.exit_code == 3, result.output
                outcome = json.loads(result.stdout)
                identity = outcome["submission_id"]
                assert outcome["request_id"] == identity
                assert outcome["publication"] == "unknown"
                tracker = executor._publications[identity]
                assert not tracker.task.done()
                assert executor.registry._source_work[child.reference] == 1
                assert executor.spool.pending_count() == 0
                (executor.paths.artifacts / "t_example" / "file").write_text("changed bytes")
                concurrent = await executor.handle_ipc(retry_request(child, identity))
                assert concurrent.unconfirmed is not None
                assert concurrent.unconfirmed.publication == "preparing"
                assert executor._publications[identity] is tracker
                release.set()
                publication = await tracker.task
                assert executor.registry._source_work == {}
                assert captures == 1
                snapshots = executor.spool.snapshots(identity)
                assert snapshots[0].local_path.read_text() == "original bytes"
                retried = await asyncio.to_thread(
                    CliRunner().invoke, executor_group, ["retry", identity, "--json"]
                )
                assert retried.exit_code == 3, retried.output
                assert json.loads(retried.stdout)["publication"] == "queued"
                assert executor.spool.pending() == (publication.envelope,)
                assert captures == 1
            finally:
                release.set()

    asyncio.run(run())


def test_shared_deadline_returns_preparing_but_tracker_survives_physical_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered, release = threading.Event(), threading.Event()
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            mint = executor.spool.add

            def held_mint(*args: object, **kwargs: object) -> object:
                envelope = mint(*args, **kwargs)  # type: ignore[arg-type]
                entered.set()
                assert release.wait(5)
                return envelope

            monkeypatch.setattr(executor.spool, "add", held_mint)
            request = write_request(child, "original", budget=0.1)
            try:
                started = time.monotonic()
                response = await executor.handle_ipc(request)
                assert time.monotonic() - started < 0.6
                assert entered.is_set()
                assert response.unconfirmed is not None
                assert response.unconfirmed.publication == "preparing"
                tracker = executor._publications["original"]
                assert executor.spool.pending_count() == 1
                assert executor.registry._source_work[child.reference] == 1
                conflict = replace(request, payload={"operation": "ticket.edit", "arguments": {}})
                refusal = await executor.handle_ipc(conflict)
                assert refusal.error is not None and refusal.error["code"] == "validation"
                duplicate = await executor.handle_ipc(
                    replace(request, response_deadline=time.monotonic() + 0.01)
                )
                assert duplicate.unconfirmed is not None
                assert executor._publications["original"] is tracker
                envelope = executor.spool.pending()[0]
                executor.spool.acknowledge(
                    AcceptanceReceipt(
                        envelope.sender_id,
                        envelope.submission_id,
                        envelope.payload_sha256,
                        ReceiptDisposition.accepted,
                        {"saved": True},
                        1,
                    )
                )
                observed = await executor.handle_ipc(retry_request(child, "original"))
                assert observed.result == {"saved": True}
                assert executor._publications["original"] is tracker
                assert executor.registry._source_work[child.reference] == 1
                release.set()
                await tracker.task
                assert executor.registry._source_work == {}
                assert "original" not in executor._publications
            finally:
                release.set()

    asyncio.run(run())


def test_retry_preserves_acknowledged_and_unknown_uncertainty_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            response = await executor.handle_ipc(write_request(child, "original"))
            assert response.queued is not None
            envelope = executor.spool.pending()[0]
            executor.spool.acknowledge(
                AcceptanceReceipt(
                    envelope.sender_id,
                    "original",
                    envelope.payload_sha256,
                    ReceiptDisposition.refused,
                    {"error": {"code": "validation"}},
                    1,
                )
            )
            assert not (executor.spool.root / "entries" / "original").exists()
            acknowledged = await executor.handle_ipc(retry_request(child, "original"))
            assert acknowledged.error == {"code": "validation"}
            changed = replace(
                write_request(child, "original"),
                payload={"operation": "ticket.edit", "arguments": {}},
            )
            conflict = await executor.handle_ipc(changed)
            assert conflict.error is not None and conflict.error["code"] == "validation"
            assert not executor.spool.pending()
            sender = executor.spool.sender_id
        # A fresh registry has no old native child authority. Journal identity still survives.
        restarted = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="synthetic",
            panels_server_url="https://unused",
        )
        try:
            assert restarted.spool.sender_id == sender
            row = restarted.spool.lookup("original")
            assert row is None  # Old run capabilities are gone, so confirmed lookup rows are swept.
            stale = await restarted.handle_ipc(retry_request(child, "original"))
            assert stale.error is not None and stale.error["code"] == "agent_forbidden"
            restarted.registry.children["child"] = child
            missing = await restarted.handle_ipc(retry_request(child, "absent"))
            assert missing.unconfirmed is not None and missing.unconfirmed.publication == "unknown"
            assert not restarted.spool.pending()
        finally:
            restarted.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("refusal", ["capability", "admission"])
def test_actual_local_refusal_is_canonical_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool, refusal: str
) -> None:
    async def run() -> None:
        async with ipc_service(tmp_path, monkeypatch) as (executor, _):
            if refusal == "capability":
                monkeypatch.setenv("PLAN_EXECUTOR_CAPABILITY", "wrong")
            else:
                executor.registry.admission_stopped = True
            result = await asyncio.to_thread(
                CliRunner().invoke,
                main,
                ["worker", "recap"] + (["--json"] if as_json else []),
                input="refused",
            )
            assert result.exit_code == 1
            assert isinstance(result.exception, SystemExit)
            assert "Traceback" not in result.output
            if as_json:
                error = json.loads(result.stderr)["error"]
                assert set(error) == {"code", "message", "detail"}
                assert error["code"] == "agent_forbidden"
            else:
                assert "cannot perform" in result.stderr
            assert not executor.spool.pending()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["eof", "malformed", "oversized", "identity"])
def test_response_failure_after_send_retains_original_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        path = tmp_path / "run"
        path.mkdir()
        monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))
        observed: list[IpcRequest] = []
        completed = asyncio.Event()

        async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            incoming = await read_typed_frame(reader, IpcRequest)
            observed.append(incoming)
            if failure == "malformed":
                writer.write(struct.pack("!I", 1) + b"{")
            elif failure == "oversized":
                writer.write(struct.pack("!I", 2**22))
            elif failure == "identity":
                writer.write(encode_frame(IpcResponse("wrong", {}, None)))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            completed.set()

        async with await asyncio.start_unix_server(peer, path=path / "executor.sock"):
            response = await asyncio.to_thread(
                ipc_client.request,
                IpcOperation.write,
                {"operation": "ticket.edit", "arguments": {}},
            )
            await completed.wait()
        assert response.unconfirmed is not None
        assert response.unconfirmed.request_id == observed[0].request_id
        assert response.unconfirmed.submission_id == observed[0].request_id
        assert response.unconfirmed.publication == "unknown"

    asyncio.run(run())


def test_partial_send_failure_is_uncertain_but_pre_send_failure_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = bytearray()

    class PartialSocket:
        def __enter__(self) -> PartialSocket:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def settimeout(self, value: float) -> None:
            pass

        def connect(self, path: str) -> None:
            pass

        def sendall(self, content: bytes) -> None:
            observed.extend(content[:10])
            raise BrokenPipeError("partial frame")

    monkeypatch.setattr(
        ipc_client,
        "socket",
        SimpleNamespace(
            socket=lambda *args: PartialSocket(),
            AF_UNIX=socket.AF_UNIX,
            SOCK_STREAM=socket.SOCK_STREAM,
        ),
    )
    response = ipc_client.request(IpcOperation.write, {"operation": "ticket.edit", "arguments": {}})
    assert observed and response.unconfirmed is not None
    assert response.unconfirmed.submission_id == response.request_id
    with pytest.raises(ValueError):
        ipc_client.request(IpcOperation.write, {"operation": "ticket.edit"})


@pytest.mark.parametrize("payload", [{}, {"operation": "ticket.edit", "arguments": []}])
def test_invalid_payload_is_a_canonical_refusal_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: JsonObject
) -> None:
    async def run() -> None:
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            response = await executor.handle_ipc(
                replace(write_request(child, "bad"), payload=payload)
            )
            assert response.error is not None and response.error["code"] == "validation"
            assert not executor._publications and not executor.spool.pending()

    asyncio.run(run())


def test_production_six_second_preparation_consumes_shared_thirty_second_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        captures = 0

        original = MacOutbox._capture

        def slow_snapshot(
            *args: object, **kwargs: object
        ) -> tuple[tuple[FileSnapshot, ...], list[tuple[object, ...]]]:
            nonlocal captures
            captures += 1
            time.sleep(6)
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(MacOutbox, "_capture", slow_snapshot)
        async with ipc_service(tmp_path, monkeypatch) as (executor, _):
            started = time.monotonic()
            response = await asyncio.to_thread(
                ipc_client.request,
                IpcOperation.write,
                {"operation": "ticket.edit", "arguments": {}},
            )
            elapsed = time.monotonic() - started
            assert 29 <= elapsed < 34, elapsed
            assert captures == 1
            assert response.queued is not None and response.error is None
            assert executor.spool.pending()[0].submission_id == response.request_id

    asyncio.run(run())


def test_post_commit_cleanup_error_keeps_identity_and_retry_observes_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            mint = executor.spool.add

            def committed_then_failed(*args: object, **kwargs: object) -> object:
                mint(*args, **kwargs)  # type: ignore[arg-type]
                raise OSError("snapshot cleanup failed after commit")

            monkeypatch.setattr(executor.spool, "add", committed_then_failed)
            response = await executor.handle_ipc(write_request(child, "original"))
            assert response.error is None and response.unconfirmed is not None
            assert response.unconfirmed.submission_id == "original"
            original = executor.spool.pending()[0]
            retried = await executor.handle_ipc(
                replace(retry_request(child, "original"), response_deadline=time.monotonic() + 5)
            )
            assert retried.unconfirmed is not None and retried.unconfirmed.publication == "queued"
            assert retried.unconfirmed.submission_id == original.submission_id == "original"
            assert executor.spool.pending() == (original,)

    asyncio.run(run())


def test_duplicate_reservation_precedes_blocked_journal_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered, release = threading.Event(), threading.Event()
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):
            lookup = executor.spool.lookup
            calls = 0

            def blocked_lookup(identity: str) -> object:
                nonlocal calls
                calls += 1
                entered.set()
                assert release.wait(5)
                return lookup(identity)

            monkeypatch.setattr(executor.spool, "lookup", blocked_lookup)
            request = write_request(child, "same", budget=0.08)
            first = asyncio.create_task(executor.handle_ipc(request))
            second = asyncio.create_task(executor.handle_ipc(request))
            try:
                responses = await asyncio.gather(first, second)
                assert entered.is_set() and calls >= 1
                assert executor.registry._source_work[child.reference] == 1
                assert all(row.unconfirmed is not None for row in responses)
                tracker = executor._publications["same"]
                conflict = await executor.handle_ipc(
                    replace(
                        request,
                        payload={
                            "operation": "ticket.edit",
                            "arguments": {"different": True},
                        },
                    )
                )
                assert conflict.error is not None and conflict.error["code"] == "validation"
                assert executor._publications["same"] is tracker
                release.set()
                await tracker.task
                assert len(executor.spool.pending()) == 1
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing_blob", "disk_full"])
def test_failed_add_reports_unknown_and_never_proves_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        async with ipc_service(tmp_path, monkeypatch) as (executor, child):

            def fail_add(*args: object, **kwargs: object) -> object:
                if failure == "missing_blob":
                    raise FileNotFoundError("captured blob vanished")
                raise OSError(errno.ENOSPC, "outbox disk full")

            monkeypatch.setattr(executor.spool, "add", fail_add)
            response = await executor.handle_ipc(write_request(child, "lost-output"))
            assert response.unconfirmed is not None
            assert response.unconfirmed.publication == "unknown"
            assert not executor.spool.pending()
            # Even if the native child is already absent, lost authored output
            # prevents the final stop proof from claiming complete delivery.
            executor.registry.children.clear()
            stopped = await executor.registry._stop_exact_child(
                ChildCommand("stop", child.reference, ChildOperation.stop, None, StopRun())
            )
            assert stopped.disposition is CommandDisposition.uncertain
            assert stopped.result == {"reason": "child_has_unsettled_work"}

    asyncio.run(run())
