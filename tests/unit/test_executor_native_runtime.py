"""Native callback continuity is tested against the actual executor registry."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ToolCallStatus
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import encode_value
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AuthorizedAction,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    DurableAnswer,
    EnvelopeKind,
    ExecutorIdentity,
    IpcOperation,
    IpcRequest,
    JsonObject,
    QueuedEnvelope,
    Quiescence,
    QuiesceRequest,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.mac import MacExecutor, as_object
from planner.executor.runtime import NativeChildRegistry


class ScriptedChild:
    def __init__(self) -> None:
        self.answers: list[tuple[str, str]] = []
        self.starts = 0
        self.sink: BackendEventSink | None = None

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        self.starts += 1
        await asyncio.sleep(0)

    async def write_prompt(
        self, turn: TurnToken, content: MessageContent, **kwargs: object
    ) -> BackendPromptAccepted:
        return BackendPromptAccepted(True)

    async def steer(self, turn: TurnToken, content: MessageContent, **kwargs: object) -> None:
        raise RuntimeError("wire response lost after write")

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self.answers.append((ask_id, option_id))
        await asyncio.sleep(0)

    async def stop(self) -> None:
        return None


def test_pending_callback_and_native_command_survive_connection_task_cancel(tmp_path: Path) -> None:
    async def run() -> None:
        spool = FilesystemSpool(tmp_path / "spool")
        native = ScriptedChild()
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_example"),)),
            tmp_path,
            ConversationAccess.full,
        )
        start = ChildCommand(
            "start",
            reference,
            ChildOperation.start,
            None,
            {"resolved_start": encode_value(resolved)},
        )
        caller = asyncio.create_task(registry.execute_command(start))
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        outcome = await registry.execute_command(start)
        assert outcome.disposition == CommandDisposition.succeeded
        assert native.starts == 1
        with pytest.raises(ValueError, match="different content"):
            await registry.execute_command(replace(start, arguments={}))
        assert native.sink is not None
        turn = TurnToken("c_one", 1)
        prompt = ChildCommand(
            "prompt",
            reference,
            ChildOperation.write_prompt,
            turn,
            {
                "content": encode_value(text_message_content("go")),
                "sender_content": encode_value(text_message_content("go")),
                "sender_label": "Owner",
                "mode": PromptDeliveryMode.queue.value,
            },
        )
        await registry.execute_command(prompt)
        assert (await registry.quiesce()).active_turn_count == 1
        artifact = tmp_path / "artifacts" / "tickets" / "t_example" / "result.bin"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"whole output")
        await native.sink.tool_call_finished(
            turn, tool_call_id="tool", tool_call_status=ToolCallStatus.completed, detail=None
        )
        output = spool.pending()[-1]
        assert output.file_dependencies[0].relative_path == "result.bin"
        assert spool.snapshots(output.submission_id)[0].local_path.read_bytes() == b"whole output"
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("ask", "Allow?", None, ())
        )
        identity = ExecutorIdentity(1, "a" * 40, 1, reference.executor_incarnation, 2)
        ask = registry.inventory(identity).children[0].pending_asks[0]
        assert native.answers == []  # absence of an answer remains a wait
        assert (await registry.quiesce()).pending_callback_count == 1
        missing = replace(ask, child=replace(reference, child_id=str(uuid4())))
        assert await registry.apply_answer(DurableAnswer("gone", missing, "allow", 1)) is None
        assert native.answers == []
        answer = DurableAnswer("answer", ask, "allow", 1)
        first = await registry.apply_answer(answer)
        assert first is not None
        assert await registry.apply_answer(answer) == first
        assert native.answers == [("ask", "allow")]
        assert registry.inventory(identity).children[0].pending_asks == ()
        await registry.report_authorized_action(AuthorizedAction("answer", ask, "tool", 10))
        with pytest.raises(ValueError, match="correlation"):
            await registry.report_authorized_action(AuthorizedAction("wrong", ask, "tool", 10))
        kinds = [entry.kind for entry in spool.pending()]
        assert EnvelopeKind.answer_application in kinds
        assert EnvelopeKind.authorized_action in kinds
        uncertain = ChildCommand(
            "uncertain",
            reference,
            ChildOperation.steer,
            turn,
            {"content": encode_value(text_message_content("steer")), "sender_label": "Owner"},
        )
        assert (
            await registry.execute_command(uncertain)
        ).disposition == CommandDisposition.uncertain
        quiescence = await registry.quiesce()
        assert quiescence.uncertain_operation_count == 1
        assert quiescence.unsent_entry_count > 0
        assert not quiescence.ready
        spool.close()

    asyncio.run(run())


def test_stale_quiesce_rejects_restart_and_exit_is_identity_bound(tmp_path: Path) -> None:
    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_argv=("false",),
            panels_server_url="http://unused",
        )
        forbidden = await executor.handle_ipc(
            IpcRequest(
                "bad-child",
                IpcOperation.write,
                "unknown",
                "forged",
                {"operation": "ticket.note", "arguments": {}},
            )
        )
        assert forbidden.error is not None
        assert executor.spool.pending() == ()
        observed = executor.identity
        executor.identity = replace(observed, executor_incarnation=str(uuid4()))
        stale = QuiesceRequest(observed, os.getpid(), True)
        rejected = await executor.handle_ipc(
            IpcRequest("old", IpcOperation.quiesce, None, None, as_object(stale))
        )
        assert rejected.error is not None
        assert not executor.registry.admission_stopped
        fresh = QuiesceRequest(executor.identity, os.getpid(), True)
        accepted = await executor.handle_ipc(
            IpcRequest("fresh", IpcOperation.quiesce, None, None, as_object(fresh))
        )
        assert accepted.error is None
        assert accepted.result is not None
        assert accepted.result["exit_committed"] is True
        assert executor._stop.is_set()
        executor.spool.close()

    asyncio.run(run())


def test_incompatible_spool_does_not_mutate_any_path(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    root.mkdir()
    (root / "metadata.json").write_text(json.dumps({"format": 999}))
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")}
    with pytest.raises(ValueError, match="incompatible"):
        FilesystemSpool(root)
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")}
    assert after == before


def test_generation_change_during_quiesce_cannot_commit_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_argv=("false",),
            panels_server_url="http://unused",
        )
        original = executor.registry.quiesce

        async def changed_generation() -> Quiescence:
            result = await original()
            executor.identity = replace(executor.identity, connection_generation=1)
            return result

        monkeypatch.setattr(executor.registry, "quiesce", changed_generation)
        expected = QuiesceRequest(executor.identity, os.getpid(), True)
        response = await executor.handle_ipc(
            IpcRequest("race", IpcOperation.quiesce, None, None, as_object(expected))
        )
        assert response.error is not None
        assert not executor._stop.is_set()
        executor.spool.close()

    asyncio.run(run())


def test_quiesce_waits_for_start_and_rechecks_stop_emitted_journal(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        released = asyncio.Event()
        native = ScriptedChild()
        native_stops: list[bool] = []

        class StartingChild(ScriptedChild):
            async def start(
                self,
                resolved_start: ResolvedConversationStart,
                *,
                vendor_session_cursor: str | None,
            ) -> None:
                entered.set()
                await released.wait()

            async def stop(self) -> None:
                native_stops.append(True)
                assert self.sink is not None
                await self.sink.vendor_session_cursor_rebound("cursor-at-stop")

        native = StartingChild()
        spool = FilesystemSpool(tmp_path / "spool")
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        task = asyncio.create_task(
            registry.execute_command(
                ChildCommand(
                    "start",
                    reference,
                    ChildOperation.start,
                    None,
                    {"resolved_start": encode_value(resolved)},
                )
            )
        )
        await entered.wait()
        blocked = await registry.quiesce()
        assert not blocked.ready
        assert blocked.uncertain_operation_count > 0
        assert native_stops == []
        released.set()
        await task
        from planner.executor.contracts import AcceptanceReceipt, ReceiptDisposition

        for row in spool.pending():
            spool.acknowledge(
                AcceptanceReceipt(
                    row.sender_id,
                    row.submission_id,
                    row.payload_sha256,
                    ReceiptDisposition.accepted,
                    {},
                    1,
                )
            )
        stopped_with_event = await registry.quiesce()
        assert native_stops == [True]
        assert not stopped_with_event.ready
        assert stopped_with_event.unsent_entry_count == 1
        spool.close()

    asyncio.run(run())


def test_applied_answer_recovers_when_outcome_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        from planner.executor.contracts import FileSnapshot

        spool = FilesystemSpool(tmp_path / "spool")
        native = ScriptedChild()
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {"resolved_start": encode_value(resolved)},
            )
        )
        assert native.sink is not None
        turn = TurnToken("c_one", 1)
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("ask", "Allow?", None, ())
        )
        identity = ExecutorIdentity(1, "a" * 40, 1, reference.executor_incarnation, 1)
        ask = registry.inventory(identity).children[0].pending_asks[0]
        answer = DurableAnswer("answer", ask, "allow", 1)
        original = spool.mint_envelope
        failed = False

        def fail_outcome(
            *,
            conversation_id: str,
            kind: EnvelopeKind,
            payload: JsonObject,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> QueuedEnvelope:
            nonlocal failed
            if (
                kind == EnvelopeKind.command_outcome
                and payload.get("command_id") == "answer"
                and not failed
            ):
                failed = True
                raise OSError("disk failed after callback")
            return original(
                conversation_id=conversation_id,
                kind=kind,
                payload=payload,
                snapshots=snapshots,
                submission_id=submission_id,
            )

        monkeypatch.setattr(spool, "mint_envelope", fail_outcome)
        with pytest.raises(OSError, match="after callback"):
            await registry.apply_answer(answer)
        assert native.answers == [("ask", "allow")]
        assert registry.inventory(identity).children[0].pending_asks == ()
        assert not (await registry.quiesce()).ready
        recovered = await registry.apply_answer(answer)
        assert recovered is not None
        assert native.answers == [("ask", "allow")]
        assert registry.outcomes["answer"].result["answer_application"] == encode_value(recovered)
        assert any(row.kind == EnvelopeKind.answer_application for row in spool.pending())
        spool.close()

    asyncio.run(run())


def test_replay_failure_blocks_only_its_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        from planner.executor.contracts import (
            AcceptanceReceipt,
            ReceiptDisposition,
            WireMessage,
            WireMessageKind,
        )

        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_argv=("false",),
            panels_server_url="http://unused",
        )
        for conversation in ("c_a", "c_a", "c_b"):
            executor.spool.mint_envelope(
                conversation_id=conversation, kind=EnvelopeKind.cli_write, payload={}
            )
        visited: list[str] = []

        async def transport(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            row = TypeAdapter(QueuedEnvelope).validate_python(payload)
            visited.append(row.conversation_id)
            if row.conversation_id == "c_a":
                raise ValueError("one conversation cannot be accepted")
            return WireMessage(
                WireMessageKind.receipt,
                "reply",
                as_object(
                    AcceptanceReceipt(
                        row.sender_id,
                        row.submission_id,
                        row.payload_sha256,
                        ReceiptDisposition.accepted,
                        {"original": True},
                        1,
                    )
                ),
            )

        monkeypatch.setattr(executor, "_request", transport)
        await executor._replay()
        assert visited == ["c_a", "c_b"]
        assert [row.conversation_id for row in executor.spool.pending()] == ["c_a", "c_a"]
        executor.spool.close()

    asyncio.run(run())


def test_ipc_snapshot_owner_is_bound_and_refusal_is_an_error(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        from planner.executor.contracts import AcceptanceReceipt, ReceiptDisposition

        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_argv=("false",),
            panels_server_url="http://unused",
        )
        native = ScriptedChild()

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            return cast(BackendChildFactory, lambda **kwargs: native)

        executor.registry.factory = factory
        reference = ChildReference(executor.identity.executor_incarnation, str(uuid4()), "c_one")
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_own"),)),
            tmp_path,
            ConversationAccess.full,
        )
        await executor.registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {"resolved_start": encode_value(resolved)},
            )
        )
        for owner in ("t_own", "t_other"):
            root = executor.paths.artifacts / owner
            root.mkdir(parents=True)
            (root / "file").write_text(owner)
        child = executor.registry.children[reference.child_id]
        request = IpcRequest(
            "write",
            IpcOperation.write,
            reference.child_id,
            child.capability,
            {"operation": "ticket.note", "arguments": {"ticket_id": "t_other"}},
        )
        task = asyncio.create_task(executor.handle_ipc(request))
        while "write" not in executor._receipts:
            await asyncio.sleep(0)
        row = next(row for row in executor.spool.pending() if row.submission_id == "write")
        assert {manifest.owner_id for manifest in row.file_dependencies} == {"t_own"}
        failure: JsonObject = {"code": "agent_forbidden", "message": "stale claim", "detail": {}}
        executor._receipts["write"].set_result(
            AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.refused,
                {"error": failure},
                1,
            )
        )
        response = await task
        assert response.result is None
        assert response.queued is None
        assert response.error == failure
        executor.spool.close()

    asyncio.run(run())


def test_real_daemon_controlled_quiesce_exits_zero(tmp_path: Path) -> None:
    import socket
    import subprocess
    import sys
    import time

    from planner.executor.contracts import IpcResponse
    from planner.executor.wire import encode_frame

    root = tmp_path / "daemon"
    script = (
        "import asyncio; from pathlib import Path; "
        "from planner.executor.mac import MacExecutor; "
        "from planner.executor.configuration import ExecutorPaths; "
        f"service=MacExecutor(paths=ExecutorPaths(Path({str(root)!r})),revision={'a' * 40!r},"
        "ssh_argv=('false',),panels_server_url='http://unused'); "
        "asyncio.run(service.run()); "
        "assert service.exit_committed"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    def exchange(request: IpcRequest) -> IpcResponse:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(5)
            stream.connect(str(root / "run" / "executor.sock"))
            stream.sendall(encode_frame(request))

            def exact(size: int) -> bytes:
                result = b""
                while len(result) < size:
                    data = stream.recv(size - len(result))
                    if not data:
                        raise AssertionError("daemon closed without response")
                    result += data
                return result

            length = int.from_bytes(exact(4), "big")
            return TypeAdapter(IpcResponse).validate_json(exact(length))

    try:
        deadline = time.monotonic() + 10
        while not (root / "run" / "executor.sock").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        health = exchange(IpcRequest("health", IpcOperation.health, None, None, {}))
        assert health.result is not None
        identity = TypeAdapter(ExecutorIdentity).validate_python(health.result["identity"])
        request = QuiesceRequest(identity, process.pid, True)
        result = exchange(IpcRequest("exit", IpcOperation.quiesce, None, None, as_object(request)))
        assert result.result is not None
        assert result.result["exit_committed"] is True
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
