"""Native callback continuity is tested against the actual executor registry."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
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
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION, ExecutorPaths
from planner.executor.contracts import (
    AuthorizedAction,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    ConnectionState,
    DurableAnswer,
    EnvelopeKind,
    ExecutorIdentity,
    ExecutorSshConfig,
    IpcOperation,
    IpcRequest,
    JsonObject,
    QueuedEnvelope,
    Quiescence,
    QuiesceRequest,
    ReconciliationResult,
    WireMessage,
    WireMessageKind,
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
        artifact.parent.mkdir(parents=True, exist_ok=True)
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
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 2
        )
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
            ssh_config=ExecutorSshConfig(
                "unused", 1, "unused", tmp_path / "key", tmp_path / "hosts"
            ),
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
    (root / "metadata.json").write_text(json.dumps({"format": 999, "sender_id": str(uuid4())}))
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")}
    with pytest.raises(ValueError, match="incompatible|metadata fields"):
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
            ssh_config=ExecutorSshConfig(
                "unused", 1, "unused", tmp_path / "key", tmp_path / "hosts"
            ),
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


@pytest.mark.parametrize("wrapper", [False, True])
def test_applied_answer_recovers_when_outcome_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: bool,
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
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
        )
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
        outer = ChildCommand(
            "outer",
            reference,
            ChildOperation.answer_permission_ask,
            turn,
            {"answer": encode_value(answer)},
        )
        with pytest.raises(OSError, match="after callback"):
            if wrapper:
                await registry.execute_command(outer)
            else:
                await registry.apply_answer(answer)
        monkeypatch.setattr(spool, "mint_envelope", original)
        assert native.answers == [("ask", "allow")]
        assert registry.inventory(identity).children[0].pending_asks == ()
        assert not (await registry.quiesce()).ready
        if wrapper:
            outcome = await registry.execute_command(outer)
            assert outcome.disposition == CommandDisposition.succeeded
            assert outcome.result == {"applied": True}
            assert await registry.execute_command(outer) == outcome
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
            ssh_config=ExecutorSshConfig(
                "unused", 1, "unused", tmp_path / "key", tmp_path / "hosts"
            ),
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
            ssh_config=ExecutorSshConfig(
                "unused", 1, "unused", tmp_path / "key", tmp_path / "hosts"
            ),
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
            root.mkdir(parents=True, exist_ok=True)
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
        "from planner.executor.configuration import ExecutorPaths, PROTOCOL_VERSION; "
        f"service=MacExecutor(paths=ExecutorPaths(Path({str(root)!r})),revision={'a' * 40!r},"
        "ssh_config=__import__('planner.executor.contracts',"
        "fromlist=['ExecutorSshConfig']).ExecutorSshConfig("
        "'127.0.0.1',1,'unused',Path('/missing-key'),Path('/missing-hosts')),"
        "panels_server_url='http://unused'); "
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


def test_native_start_localizes_vps_identity_and_home_relative_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        vps_home, mac_home = tmp_path / "vps-home", tmp_path / "mac-home"
        workspace = mac_home / "projects" / "same-checkout"
        workspace.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(mac_home))
        monkeypatch.setenv("CODEX_HOME", str(mac_home / ".codex"))
        paths = ExecutorPaths(mac_home / "executor-data")
        spool = FilesystemSpool(paths.spool)
        received: list[ResolvedConversationStart] = []
        constructed: list[ResolvedConversationStart] = []

        class InspectingChild(ScriptedChild):
            async def start(
                self,
                resolved_start: ResolvedConversationStart,
                *,
                vendor_session_cursor: str | None,
            ) -> None:
                received.append(resolved_start)

        native = InspectingChild()

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            constructed.append(resolved)
            return cast(BackendChildFactory, lambda **kwargs: native)

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            paths=paths,
            workspace_home=mac_home,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        remote_identity = (
            ("PLAN_TICKET_ID", "t_own"),
            ("PLAN_TICKET_FILES_DIR", str(vps_home / "files" / "tickets" / "t_own")),
            ("PLAN_EXECUTOR_ROOT", str(vps_home / "wrong-root")),
            ("PLAN_EXECUTOR_CHILD_ID", "forged-child"),
            ("PLAN_EXECUTOR_CAPABILITY", "forged-capability"),
            ("HOME", str(vps_home)),
            ("CODEX_HOME", str(vps_home / ".codex")),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            ConversationRoleMaterials("worker", remote_identity),
            vps_home / "projects" / "same-checkout",
            ConversationAccess.full,
        )
        from planner.executor.remote import RemoteBackendChild

        forwarded: list[ChildCommand] = []

        async def send(command: ChildCommand) -> CommandOutcome:
            forwarded.append(command)
            return await registry.execute_command(command)

        # Exercise VPS encoding and Mac hydration with distinct home directories.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: vps_home))
        remote = RemoteBackendChild(reference, send)
        await remote.start(resolved, vendor_session_cursor=None)
        assert forwarded[0].arguments["workspace_home_relative"] == "projects/same-checkout"
        assert (
            registry.outcomes[forwarded[0].command_id].disposition == CommandDisposition.succeeded
        )
        assert received == constructed
        assert received[0].workspace_folder == workspace
        assert received[0].role_materials is not None
        identity = dict(received[0].role_materials.identity_environment_variables)
        assert identity["PLAN_TICKET_FILES_DIR"] == str(paths.artifacts / "t_own")
        assert identity["PLAN_EXECUTOR_ROOT"] == str(paths.root)
        assert identity["PLAN_EXECUTOR_CHILD_ID"] == reference.child_id
        assert (
            identity["PLAN_EXECUTOR_CAPABILITY"] == registry.children[reference.child_id].capability
        )
        assert identity["PLAN_EXECUTOR_CAPABILITY"] != "forged-capability"
        assert "HOME" not in identity and "CODEX_HOME" not in identity
        assert os.environ["HOME"] == str(mac_home)
        assert os.environ["CODEX_HOME"] == str(mac_home / ".codex")
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("relative", ["projects/missing", "../escape", "/absolute", "a\\b"])
def test_unavailable_or_unsafe_workspace_refuses_before_native_creation(
    tmp_path: Path,
    relative: str,
) -> None:
    async def run() -> None:
        spool = FilesystemSpool(tmp_path / "spool")
        created: list[bool] = []

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            created.append(True)
            return cast(BackendChildFactory, lambda **kwargs: ScriptedChild())

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            workspace_home=tmp_path,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            Path("/home/vps/projects/absent"),
            ConversationAccess.full,
        )
        result = await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {"resolved_start": encode_value(resolved), "workspace_home_relative": relative},
            )
        )
        assert result.disposition == CommandDisposition.refused
        assert result.result["error_type"] == "BackendSpawnFailed"
        assert created == []
        assert registry.children == {}
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "command_outcome", "answer_application"])
def test_user_input_recorded_map_survives_lost_ack_and_durability_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from planner.conversation.backends.contracts import BackendUserInputRequest
    from planner.conversation.events import UserInputAnswer, UserInputOption, UserInputQuestion
    from planner.executor.contracts import AnswerApplication, AskKind, AskReference, FileSnapshot

    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        maps: list[tuple[str, tuple[UserInputAnswer, ...]]] = []

        class QuestionChild(ScriptedChild):
            async def answer_user_input(
                self, request_id: str, answers: tuple[UserInputAnswer, ...]
            ) -> None:
                maps.append((request_id, answers))
                entered.set()
                await release.wait()

        native = QuestionChild()
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
        await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {"resolved_start": encode_value(resolved)},
            )
        )
        turn = TurnToken("c_one", 1)
        await registry.execute_command(
            ChildCommand(
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
        )
        questions = (
            UserInputQuestion(
                "choice",
                "Choice",
                "Select one",
                (UserInputOption("Yes", ""), UserInputOption("No", "")),
                False,
                False,
            ),
            UserInputQuestion("text", "Text", "Explain", (), False, True),
        )
        assert native.sink is not None
        await native.sink.user_input_requested(turn, BackendUserInputRequest("same", questions))
        # Provider IDs can coincide across the two ask kinds.
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("same", "Allow?", None, ())
        )
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 2
        )
        inventory = registry.inventory(identity).children[0]
        assert inventory.active_turn == turn
        assert inventory.pending_user_input_ids == ("same",)
        ask = AskReference(reference, turn, "same", AskKind.user_input)
        answers = (UserInputAnswer("choice", ("Yes",)), UserInputAnswer("text", ("exact text",)))
        answer = DurableAnswer("map-answer", ask, None, 1, answers)
        assert maps == []  # Unanswered requests remain waits, including reconnect inventory.
        assert (await registry.quiesce()).pending_callback_count == 2
        missing = replace(ask, child=replace(reference, child_id=str(uuid4())))
        assert await registry.apply_answer(replace(answer, ask=missing)) is None
        for invalid in (answers[:1], tuple(reversed(answers))):
            with pytest.raises(ValueError, match="canonical"):
                await registry.apply_answer(replace(answer, user_input_answers=invalid))
        assert maps == []
        original = spool.mint_envelope
        failed = False

        def failing(
            *,
            conversation_id: str,
            kind: EnvelopeKind,
            payload: JsonObject,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> QueuedEnvelope:
            nonlocal failed
            if (
                failure is not None
                and kind.value == failure
                and not failed
                and (
                    kind == EnvelopeKind.answer_application
                    or payload.get("command_id") == answer.answer_id
                )
            ):
                failed = True
                raise OSError("disk failed after native map application")
            return original(
                conversation_id=conversation_id,
                kind=kind,
                payload=payload,
                snapshots=snapshots,
                submission_id=submission_id,
            )

        monkeypatch.setattr(spool, "mint_envelope", failing)
        outer = ChildCommand(
            "outer-map",
            reference,
            ChildOperation.answer_user_input,
            turn,
            {"answer": encode_value(answer)},
        )
        caller = asyncio.create_task(registry.execute_command(outer))
        await entered.wait()
        caller.cancel()  # The socket/caller disappears; native operation remains owned.
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        if failure is not None:
            with pytest.raises(OSError, match="after native"):
                await registry.execute_command(outer)
            monkeypatch.setattr(spool, "mint_envelope", original)
        outcome = await registry.execute_command(outer)
        assert outcome.disposition == CommandDisposition.succeeded
        assert await registry.execute_command(outer) == outcome
        assert maps == [("same", answers)]
        inventory = registry.inventory(identity).children[0]
        assert inventory.pending_user_input_ids == ()
        assert inventory.pending_asks[0].kind == AskKind.permission
        application = await registry.apply_answer(answer)
        assert application is not None
        assert application.ask.kind == AskKind.user_input
        evidence = registry.outcomes[answer.answer_id].result["answer_application"]
        assert TypeAdapter(AnswerApplication).validate_python(evidence) == application
        with pytest.raises(ValueError, match="different recorded"):
            await registry.apply_answer(
                replace(answer, user_input_answers=(UserInputAnswer("choice", ("No",)), answers[1]))
            )
        assert maps == [("same", answers)]
        spool.close()

    asyncio.run(run())


def test_old_peer_is_refused_before_work_or_replay_and_preserves_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.contracts import WireMessage, WireMessageKind
    from planner.executor.wire import ProtocolRefused

    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        original = peer.reply

        async def old_reply(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            if kind is WireMessageKind.hello:
                return WireMessage(
                    WireMessageKind.hello_accepted,
                    "hello",
                    as_object(replace(executor.identity, protocol_version=1)),
                )
            return await original(kind, payload)

        monkeypatch.setattr(executor, "_request", old_reply)
        row = executor.spool.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"original": True}
        )
        with pytest.raises(ProtocolRefused):
            await executor._connected()
        assert executor.spool.pending() == (row,)
        assert not executor._command_tasks and peer.closed
        executor.spool.close()

    asyncio.run(run())


def make_executor(root: Path) -> MacExecutor:
    return MacExecutor(
        paths=ExecutorPaths(root),
        revision="a" * 40,
        ssh_config=ExecutorSshConfig("unused", 1, "unused", root / "key", root / "hosts"),
        panels_server_url="http://unused",
    )


class ControlPeer:
    """Control-only boundary. Real SSH/SFTP behavior is covered by connected tests."""

    def __init__(self, executor: MacExecutor) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        self.executor = executor
        self.reader = asyncio.StreamReader()
        self.process = SimpleNamespace(
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()), stdout=self.reader
        )
        self.closed = False
        self.failure: Exception | None = None

    async def create_process(self, *args: object, **kwargs: object) -> object:
        return self.process

    async def reply(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        from planner.executor.contracts import WireMessage, WireMessageKind

        result: JsonObject
        if kind is WireMessageKind.hello:
            result = as_object(self.executor.identity)
            response = WireMessageKind.hello_accepted
        elif kind is WireMessageKind.transport_open:
            result = {
                "endpoint": {
                    "socket_path": "/unused",
                    "server_public_key": "unused",
                    "username": "unused",
                },
                "preview_root": "/unused",
            }
            response = WireMessageKind.transport_opened
        elif kind is WireMessageKind.reconcile_inventory:
            result = as_object(ReconciliationResult(self.executor.identity, (), (), (), ()))
            response = WireMessageKind.reconcile_result
        else:
            assert kind is WireMessageKind.reconciliation_ready
            result = {"ready": True}
            response = kind
        return WireMessage(response, "reply", result)


def install_control_peer(executor: MacExecutor, monkeypatch: pytest.MonkeyPatch) -> ControlPeer:
    from contextlib import asynccontextmanager
    from typing import Any

    from planner.executor import mac as module

    peer = ControlPeer(executor)

    async def connect(config: object) -> Any:
        if peer.failure is not None:
            raise peer.failure
        peer.closed = False
        peer.reader = asyncio.StreamReader()
        peer.process.stdout = peer.reader
        return peer

    async def close(connection: object) -> None:
        peer.closed = True
        peer.reader.feed_eof()

    @asynccontextmanager
    async def sftp(*args: object) -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(module, "connect_outer", connect)
    monkeypatch.setattr(module, "close_connection", close)
    monkeypatch.setattr(module, "connect_sftp", sftp)
    monkeypatch.setattr(executor, "_request", peer.reply)
    return peer


def test_process_open_failure_closes_outer_and_records_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)

        async def refuse(*args: object, **kwargs: object) -> None:
            raise ConnectionError("exec refused")

        monkeypatch.setattr(peer, "create_process", refuse)
        with pytest.raises(ConnectionError, match="exec refused"):
            await executor._connected()
        assert peer.closed and executor.lost_at_ms is not None
        assert executor.state is ConnectionState.disconnected
        executor.spool.close()

    asyncio.run(run())


def test_controlled_stop_wakes_pending_handshake_without_peer_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        monkeypatch.setattr(executor, "_request", MacExecutor._request.__get__(executor))
        task = asyncio.create_task(executor._connected())
        try:
            while executor._writer is None:
                await asyncio.sleep(0)
            executor._stop.set()
            await asyncio.wait_for(task, 1)
            assert executor._writer is None and peer.closed
            assert not executor.spool.pending()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["valid", "no_handshake", "bad_payload", "stale_request"])
def test_ready_ack_clears_loss_before_fulfilling_current_request(
    tmp_path: Path, case: str
) -> None:
    from planner.executor.contracts import ConnectionState, WireMessage, WireMessageKind
    from planner.executor.wire import ProtocolRefused, encode_frame

    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_argv=("false",),
            panels_server_url="http://unused",
        )
        executor.state = ConnectionState.reconciling
        executor.lost_at_ms = 123
        executor._handshake_accepted = case != "no_handshake"
        observed: list[tuple[ConnectionState, int | None]] = []

        class ObservedFuture(asyncio.Future[WireMessage]):
            def set_result(self, result: WireMessage) -> None:
                # Observe synchronously before fulfillment can resume the session.
                observed.append((executor.state, executor.lost_at_ms))
                super().set_result(result)

        pending = ObservedFuture()
        executor._pending["current-request"] = pending
        reader = asyncio.StreamReader()
        reader.feed_data(
            encode_frame(
                WireMessage(
                    WireMessageKind.reconciliation_ready,
                    "old-request" if case == "stale_request" else "current-request",
                    {"ready": False} if case == "bad_payload" else {"ready": True},
                )
            )
        )
        reader.feed_eof()
        try:
            if case in ("no_handshake", "bad_payload"):
                with pytest.raises(ProtocolRefused, match="invalid reconciliation"):
                    await executor._read_loop(reader)
            else:
                with pytest.raises(asyncio.IncompleteReadError):
                    await executor._read_loop(reader)
            if case == "valid":
                assert observed == [(ConnectionState.ready, None)]
                assert pending.done()
            else:
                assert observed == []
                assert not pending.done()
                assert executor.state == ConnectionState.reconciling
                assert executor.lost_at_ms == 123
        finally:
            pending.cancel()
            executor.spool.close()

    asyncio.run(run())


def test_current_loss_survives_failed_reconnect_and_clears_only_when_reconciled_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import mac as module

    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        monkeypatch.setattr(module, "RECONNECT_INTERVAL_SECONDS", 0.001)

        async def ready() -> None:
            while executor.state is not ConnectionState.ready:
                await asyncio.sleep(0)

        task = asyncio.create_task(executor._connected())
        await asyncio.wait_for(ready(), 2)
        assert executor.lost_at_ms is None
        peer.reader.feed_eof()
        with pytest.raises(asyncio.IncompleteReadError):
            await task
        loss = executor.lost_at_ms
        assert loss is not None
        peer.failure = OSError("peer unavailable")
        with pytest.raises(OSError):
            await executor._connected()
        assert executor.lost_at_ms == loss
        assert executor.state is ConnectionState.disconnected
        peer.failure = None
        task = asyncio.create_task(executor._connected())
        try:
            await asyncio.wait_for(ready(), 2)
            assert executor.lost_at_ms is None
            assert executor.identity.connection_generation == 3
            executor._stop.set()
            await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_replay_keeps_one_head_per_conversation_and_progresses_past_bulk_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled file upload cannot hold another conversation's control envelope."""
    from planner.executor import mac as module
    from planner.executor.contracts import AcceptanceReceipt, FileScope, ReceiptDisposition
    from planner.executor.files import snapshot_file

    async def run() -> None:
        executor = make_executor(tmp_path)
        source = tmp_path / "artifacts"
        source.mkdir()
        (source / "image").write_bytes(b"immutable")
        snapshot = snapshot_file(
            root=source, relative_path="image", scope=FileScope.ticket, owner_id="t_one"
        )
        child = ChildReference(executor.identity.executor_incarnation, str(uuid4()), "c_a")
        first = executor.spool.mint_envelope(
            conversation_id="c_a",
            kind=EnvelopeKind.cli_write,
            payload={"child": as_object(child)},
            snapshots=(snapshot,),
        )
        second = executor.spool.mint_envelope(
            conversation_id="c_a", kind=EnvelopeKind.cli_write, payload={}
        )
        independent = executor.spool.mint_envelope(
            conversation_id="c_b", kind=EnvelopeKind.cli_write, payload={}
        )
        durable = executor.spool.snapshots(first.submission_id)[0].local_path
        uploading, release, progressed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent: list[str] = []
        executor._sftp = cast(module.asyncssh.SFTPClient, object())

        async def upload(client: object, source: Path, token: str) -> None:
            uploading.set()
            await release.wait()
            assert source.read_bytes() == b"immutable"

        def receipt(row: QueuedEnvelope) -> AcceptanceReceipt:
            return AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.accepted,
                {},
                1,
            )

        async def transport(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            if kind is WireMessageKind.file_manifest:
                return WireMessage(
                    WireMessageKind.file_slot,
                    "slot",
                    {"token": "authorized", "direction": "upload", "manifest": payload["manifest"]},
                )
            if kind is WireMessageKind.file_complete:
                return WireMessage(WireMessageKind.file_accepted, "complete", payload["manifest"])
            row = TypeAdapter(QueuedEnvelope).validate_python(payload)
            sent.append(row.submission_id)
            if row == independent:
                progressed.set()
            return WireMessage(WireMessageKind.receipt, "receipt", as_object(receipt(row)))

        monkeypatch.setattr(module, "upload_snapshot", upload)
        monkeypatch.setattr(executor, "_request", transport)
        task = asyncio.create_task(executor._replay())
        try:
            await asyncio.wait_for(uploading.wait(), 2)
            await asyncio.wait_for(progressed.wait(), 2)
            assert not task.done() and second.submission_id not in sent
            # Reconciliation can persist a receipt during a live transfer. The pin
            # protects its source bytes until the transfer coroutine leaves.
            executor.spool.acknowledge(receipt(first))
            assert durable.exists()
            release.set()
            await asyncio.wait_for(task, 2)
            assert not durable.exists()
            assert executor.spool.pending() == (second,)
            await executor._replay()
            assert executor.spool.pending() == ()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_quiesce_counts_admitted_input_task_before_it_starts_and_refuses_new_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain receipt cannot overtake an admitted but unscheduled transfer."""
    from unittest.mock import AsyncMock

    async def run() -> None:
        executor = make_executor(tmp_path)
        pending = asyncio.create_task(asyncio.Event().wait())
        executor._transfer_tasks.add(cast(asyncio.Task[None], pending))
        send = AsyncMock()
        monkeypatch.setattr(executor, "_send", send)
        try:
            request = QuiesceRequest(executor.identity, os.getpid(), True)
            response = await executor.handle_ipc(
                IpcRequest("drain", IpcOperation.quiesce, None, None, as_object(request))
            )
            assert response.error is None and response.result is not None
            assert response.result["exit_committed"] is False
            assert not executor._stop.is_set()
            await executor._input(WireMessage(WireMessageKind.file_slot, "late-input", {}))
            message = send.call_args.args[0]
            assert message.kind is WireMessageKind.error
            assert message.request_id == "late-input"
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())
