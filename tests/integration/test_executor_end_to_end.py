"""Real Mac/VPS dispatch over isolated OpenSSH and temporary Unix sockets.

The scripted native adapter never calls a provider. Every event still traverses
native registry, durable spool, framed transport, VPS acceptance and conversation
core. These are process/SQLite gates; no browser is needed for this boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import socket
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.support.executor_sshd import isolated_sshd

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    ToolCallStatus,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    AskKind,
    AskReachability,
    AskReference,
    ChildReference,
    CommandDisposition,
    ConnectionState,
    EnvelopeKind,
    ExecutorIdentity,
    JsonObject,
    JsonValue,
    QueuedEnvelope,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor
from planner.executor.publication import PublicationReceiver
from planner.executor.runtime import NativeChildRegistry
from planner.executor.vps import ExecutorEndpoint


def production_relay_argv(socket_path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "planner.executor.entrypoint",
        "executor",
        "relay",
        "--socket-path",
        str(socket_path),
    )


class ScriptedNativeChild:
    def __init__(self) -> None:
        self.sink: BackendEventSink | None = None
        self.turn: TurnToken | None = None
        self.start_count = 0
        self.prompt_count = 0
        self.answers: list[tuple[str, str]] = []
        self.user_answers: list[tuple[str, tuple[UserInputAnswer, ...]]] = []
        self.required_input_path: Path | None = None
        self.required_input_contents: bytes | None = None

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        del resolved_start, vendor_session_cursor
        self.start_count += 1

    async def write_prompt(
        self, turn_token: TurnToken, content: MessageContent, **arguments: object
    ) -> BackendPromptAccepted:
        del content, arguments
        if self.required_input_path is not None:
            assert self.required_input_path.read_bytes() == self.required_input_contents
        self.prompt_count += 1
        self.turn = turn_token
        return BackendPromptAccepted(True)

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self.answers.append((ask_id, option_id))

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        self.user_answers.append((request_id, answers))

    async def raise_user_input(self, request_id: str = "request_input") -> None:
        assert self.sink is not None and self.turn is not None
        await self.sink.user_input_requested(
            self.turn,
            BackendUserInputRequest(
                request_id,
                (
                    UserInputQuestion(
                        "q_one",
                        "First",
                        "Choose one",
                        (UserInputOption("Yes", "Proceed"), UserInputOption("No", "Decline")),
                        False,
                        False,
                    ),
                    UserInputQuestion(
                        "q_two",
                        "Colors",
                        "Choose colors",
                        (UserInputOption("Red", "Warm"), UserInputOption("Blue", "Cool")),
                        True,
                        False,
                    ),
                ),
            ),
        )

    async def stop(self) -> None:
        return None

    async def raise_ask(self) -> None:
        assert self.sink is not None and self.turn is not None
        await self.sink.permission_ask_raised(
            self.turn,
            BackendPermissionAsk(
                "ask_permission",
                "Run the scripted action?",
                None,
                (
                    PermissionAskOption("allow", "Allow", "allow_once"),
                    PermissionAskOption("deny", "Deny", "reject_once"),
                ),
            ),
        )


class CutReceiptEndpoint(ExecutorEndpoint):
    """Cut after the real canonical transaction, before its receipt reaches Mac."""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, RealClock())
        self.cut_submission: str | None = None
        self.cut_count = 0
        self.cut_user_input_application = False
        self.cut_input = False
        self.input_cut_count = 0

    async def _accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        if self.cut_user_input_application:
            application: JsonValue = None
            if envelope.kind is EnvelopeKind.answer_application:
                application = envelope.payload
            elif envelope.kind is EnvelopeKind.command_outcome:
                result = envelope.payload.get("result")
                if isinstance(result, dict):
                    application = result.get("answer_application")
            if isinstance(application, dict):
                ask = application.get("ask")
                if isinstance(ask, dict) and ask.get("kind") == "user_input":
                    self.cut_user_input_application = False
                    self.cut_submission = envelope.submission_id
        return await super()._accept_envelope(envelope, identity)

    async def _send(self, message: WireMessage) -> None:
        if message.kind is WireMessageKind.file_slot and self.cut_input:
            self.cut_input = False
            self.input_cut_count += 1
            await super()._send(message)
            assert self.writer is not None
            self.writer.close()
            return
        if (
            message.kind == WireMessageKind.receipt
            and message.payload.get("submission_id") == self.cut_submission
        ):
            self.cut_submission = None
            self.cut_count += 1
            assert self.writer is not None
            self.writer.close()
            return
        await super()._send(message)


async def eventually(condition: Callable[[], bool], detail: str, seconds: float = 8) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(detail)
        await asyncio.sleep(0.01)


@dataclass
class ExecutorHarness:
    root: Path
    endpoint: CutReceiptEndpoint
    mac: MacExecutor
    system: SqliteProcessConversationSystem
    native: ScriptedNativeChild
    task: asyncio.Task[None]
    old_systems: list[SqliteProcessConversationSystem]
    ssh_stack: contextlib.ExitStack

    def events(self, kind: str) -> int:
        connection = connect(self.endpoint.db_path)
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM conversation_events WHERE conversation_id=? AND kind=?",
                    ("c_scripted", kind),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    @property
    def ask(self) -> AskReference:
        child = next(iter(self.mac.registry.children.values()))
        assert self.native.turn is not None
        return AskReference(child.reference, self.native.turn, "ask_permission")

    @property
    def input_ask(self) -> AskReference:
        child = next(iter(self.mac.registry.children.values()))
        assert self.native.turn is not None
        return AskReference(child.reference, self.native.turn, "request_input", AskKind.user_input)

    async def wait_input_reachable(self) -> None:
        def reachable() -> bool:
            evidence = self.endpoint.store.ask_evidence(self.input_ask)
            return evidence is not None and evidence.reachability is AskReachability.reachable

        await eventually(reachable, "Input callback did not get current evidence")

    async def wait_ask_reachable(self) -> None:
        def reachable() -> bool:
            evidence = self.endpoint.store.ask_evidence(self.ask)
            return evidence is not None and evidence.reachability == AskReachability.reachable

        await eventually(reachable, "No current callback evidence for the real pending ask")

    async def restart_vps_core(self) -> None:
        await asyncio.wait_for(self.endpoint.close(), 2)
        self.old_systems.append(self.system)
        self.endpoint = CutReceiptEndpoint(self.endpoint.db_path)
        self.system = make_core(self.endpoint)
        self.endpoint.runtime = SimpleNamespace(
            system=self.system, message_files=ConversationMessageFiles(self.endpoint.db_path)
        )
        self.endpoint.file_receiver = PublicationReceiver(
            self.endpoint.store,
            self.root / "vps/files",
            self.root / "vps/quarantine",
        )
        await self.endpoint.start()
        await eventually(lambda: self.endpoint.ready, "VPS core restart did not reconcile")

    async def close(self) -> None:
        try:
            await self._close()
        finally:
            self.ssh_stack.close()

    async def _close(self) -> None:
        self.mac._stop.set()
        if self.endpoint.writer is not None:
            self.endpoint.writer.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.task, 3)
        if not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.wait_for(self.endpoint.close(), 2)
        # A simulated VPS crash must not call the backend stop operation. Remove
        # only test-process references before draining its housekeeping tasks.
        for system in (*self.old_systems, self.system):
            for state in system._conversations.values():
                state.child = None
            await asyncio.wait_for(system.shutdown(), 2)


def make_core(endpoint: ExecutorEndpoint) -> SqliteProcessConversationSystem:
    return SqliteProcessConversationSystem(
        store=ConversationStore(endpoint.db_path),
        backend_child_factories={key: endpoint.factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(endpoint.db_path),
    )


async def boot(root: Path, monkeypatch: pytest.MonkeyPatch) -> ExecutorHarness:
    from planner.executor import mac as mac_module

    monkeypatch.setattr(mac_module, "RECONNECT_INTERVAL_SECONDS", 0.02)
    vps_root = root / "vps"
    vps_root.mkdir()
    db_path = str(vps_root / "panels.sqlite")
    connection = connect(db_path)
    try:
        create_schema(connection)
    finally:
        connection.close()
    socket_path = root / "vps.sock"
    monkeypatch.setenv("PLAN_EXECUTOR_VPS_SOCKET", str(socket_path))
    endpoint = CutReceiptEndpoint(db_path)
    system = make_core(endpoint)
    endpoint.runtime = SimpleNamespace(
        system=system, message_files=ConversationMessageFiles(endpoint.db_path)
    )
    endpoint.file_receiver = PublicationReceiver(
        endpoint.store,
        vps_root / "files",
        vps_root / "quarantine",
    )
    await endpoint.start()
    native = ScriptedNativeChild()
    ssh_stack = contextlib.ExitStack()
    ssh_config = ssh_stack.enter_context(isolated_sshd(root / "ssh"))
    mac = MacExecutor(
        paths=ExecutorPaths(root / "mac"),
        revision="a" * 40,
        ssh_config=ssh_config,
        remote_command=shlex.join(production_relay_argv(socket_path)),
        panels_server_url="http://unused",
    )

    def factory(
        resolved: ResolvedConversationStart, capability: str, child: ChildReference
    ) -> BackendChildFactory:
        del resolved, capability, child

        def create(**arguments: object) -> BackendChild:
            native.sink = cast(BackendEventSink, arguments["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    mac.registry = NativeChildRegistry(
        paths=mac.paths,
        incarnation=mac.identity.executor_incarnation,
        spool=mac.spool,
        factory=factory,
        message_files=ConversationMessageFiles(root / "mac/unused"),
    )
    task = asyncio.create_task(mac.run())
    harness = ExecutorHarness(root, endpoint, mac, system, native, task, [], ssh_stack)
    try:
        await eventually(
            lambda: endpoint.ready and mac.state == ConnectionState.ready,
            "Initial Mac/VPS handshake did not become ready",
        )
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c_scripted",
                model="scripted",
                workspace_folder=root,
                role_materials=ConversationRoleMaterials(
                    "A scripted test worker.",
                    (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_scripted")),
                ),
            )
        )
        await asyncio.wait_for(
            system.send(
                "c_scripted",
                text_message_content("Start"),
                sender_label="Test",
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.ticket, "t_scripted"),
            ),
            5,
        )
        await eventually(lambda: not mac.spool.pending(), "Initial command outcomes did not drain")
        return harness
    except BaseException:
        await harness.close()
        raise


def test_active_child_pending_ask_and_durable_old_answer_survive_link_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            generation = harness.mac.identity.connection_generation
            answer = harness.endpoint.store.choose_answer(
                harness.ask,
                "allow",
                generation,
                1000,
            )
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "Link did not reconcile",
            )
            await eventually(
                lambda: harness.native.answers == [("ask_permission", "allow")],
                "Durable answer did not reach original native callback",
            )

            def application_recorded() -> bool:
                evidence = harness.endpoint.store.ask_evidence(harness.ask)
                return evidence is not None and evidence.applied_at_mac_ms is not None

            await eventually(application_recorded, "Callback application did not become canonical")
            assert harness.native.start_count == harness.native.prompt_count == 1
            assert harness.endpoint.store.recorded_answer(harness.ask) == answer
            assert harness.events("permission_asked") == 1
            timing = harness.endpoint.store.action_timing(harness.ask)
            assert timing is not None and timing.unknown_reason == "action not observed"
        finally:
            await harness.close()

    asyncio.run(run())


def test_commit_before_ack_replay_returns_first_receipt_and_one_domain_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_ask()
            pending = [
                entry
                for entry in harness.mac.spool.pending()
                if entry.kind == EnvelopeKind.backend_event
            ]
            assert len(pending) == 1
            harness.endpoint.cut_submission = pending[0].submission_id
            await eventually(
                lambda: harness.endpoint.cut_count == 1, "Receipt cut was not exercised"
            )
            await eventually(
                lambda: not harness.mac.spool.pending() and harness.endpoint.ready,
                "Committed entry did not replay and acknowledge",
            )
            assert harness.events("permission_asked") == 1
            assert harness.native.answers == []
            receipt = harness.endpoint.store.lookup_receipt(
                pending[0].sender_id, pending[0].submission_id
            )
            assert receipt is not None
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_core_recreation_adopts_live_inventory_before_replaying_new_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await asyncio.wait_for(harness.endpoint.close(), 2)
            await harness.native.raise_ask()
            await harness.restart_vps_core()
            await harness.wait_ask_reachable()
            assert await harness.system.is_running("c_scripted")
            assert await harness.system.has_pending_permission_ask("c_scripted")
            assert harness.native.start_count == harness.native.prompt_count == 1
            assert harness.events("permission_asked") == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_binary_artifact_is_complete_before_dependent_backend_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            artifact = harness.mac.paths.artifacts / "t_scripted" / "binary.dat"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            contents = bytes(range(256)) * 2000
            artifact.write_bytes(contents)
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.tool_call_finished(
                harness.native.turn,
                tool_call_id="tool",
                tool_call_status=ToolCallStatus.completed,
                detail="Binary artifact is ready.",
            )
            await eventually(
                lambda: harness.events("tool_call_finished") == 1,
                "File-dependent backend event did not commit",
            )
            assert (tmp_path / "vps/files/tickets/t_scripted/binary.dat").read_bytes() == contents
            await eventually(
                lambda: not harness.mac.spool.pending(),
                "Committed file event receipt did not drain",
            )
        finally:
            await harness.close()

    asyncio.run(run())


def test_remote_replayed_usage_compaction_and_ending_preserve_core_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            sink = harness.native.sink
            turn = harness.native.turn
            assert sink is not None and turn is not None
            await sink.token_usage_reported(
                turn, input_tokens=12, output_tokens=5, cached_input_tokens=None, cost_usd=None
            )
            await sink.context_compacted(turn)
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            await harness.restart_vps_core()
            await sink.turn_ended(
                turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "ending did not commit")
            await eventually(
                lambda: not harness.mac.spool.pending(), "ending receipt did not drain"
            )
            assert harness.events("token_usage") == 1
            assert harness.events("context_compacted") == 1
            assert harness.events("explicit_reply_missing") == 1
            assert not await harness.system.is_running("c_scripted")
            evidence = harness.endpoint.store.ask_evidence(harness.ask)
            assert evidence is not None and evidence.reachability != AskReachability.reachable
            with pytest.raises(PlannerError):
                harness.endpoint.store.choose_answer(
                    harness.ask, "allow", harness.mac.identity.connection_generation, 1000
                )
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_input_attachment_bytes_arrive_before_native_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile, MessageText

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.runtime.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = (
                harness.mac.paths.root
                / "artifacts/files/conversations/c_scripted"
                / kept.stored_file_id
            )
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            await asyncio.wait_for(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageText("Inspect the bytes"),
                        MessageFile(
                            kept.stored_file_id,
                            "application/octet-stream",
                            "input.bin",
                            len(contents),
                        ),
                    ),
                    sender_label="Test",
                ),
                5,
            )
            assert harness.native.prompt_count == 2
            assert native_path.read_bytes() == contents
        finally:
            await harness.close()

    asyncio.run(run())


def test_reused_backend_ask_id_is_scoped_to_exact_turn_and_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            assert await harness.endpoint.answer_permission("c_scripted", "ask_permission", "allow")
            await eventually(
                lambda: harness.events("permission_answered") == 1, "first answer did not commit"
            )
            first_ask = harness.ask
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "first turn did not end")
            await harness.system.send(
                "c_scripted", text_message_content("Next turn"), sender_label="Test"
            )
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            assert harness.ask != first_ask and harness.ask.ask_id == first_ask.ask_id
            assert (
                harness.endpoint.ask_status("c_scripted", "ask_permission")["answer_available"]
                is True
            )
            assert await harness.endpoint.answer_permission("c_scripted", "ask_permission", "deny")
            await eventually(
                lambda: harness.events("permission_answered") == 2,
                "second exact ask did not get its own answer row",
            )
            await eventually(
                lambda: not harness.mac.spool.pending(), "answer receipts did not drain"
            )
            assert harness.native.answers == [
                ("ask_permission", "allow"),
                ("ask_permission", "deny"),
            ]
            assert harness.events("permission_answered") == 2
        finally:
            await harness.close()

    asyncio.run(run())


def test_input_transfer_restarts_before_pending_command_after_link_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile, MessageText

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.runtime.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = (
                harness.mac.paths.root
                / "artifacts/files/conversations/c_scripted"
                / kept.stored_file_id
            )
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            generation = harness.mac.identity.connection_generation
            harness.endpoint.cut_input = True
            await asyncio.wait_for(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageText("Inspect"),
                        MessageFile(
                            kept.stored_file_id,
                            "application/octet-stream",
                            "input.bin",
                            len(contents),
                        ),
                    ),
                    sender_label="Test",
                ),
                8,
            )
            assert harness.endpoint.input_cut_count == 1
            assert harness.mac.identity.connection_generation > generation
            assert harness.native.prompt_count == 2
            assert native_path.read_bytes() == contents
        finally:
            await harness.close()

    asyncio.run(run())


def test_concurrent_identical_inputs_keep_distinct_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile
    from planner.executor.codec import encode_value
    from planner.executor.contracts import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            contents = bytes(range(256)) * 3000
            commands = []
            paths = []
            for index in range(2):
                conversation_id = f"c_input_{index}"
                kept = await harness.endpoint.runtime.message_files.keep(
                    conversation_id, contents, media_type="application/octet-stream"
                )
                child = ChildReference(
                    harness.mac.identity.executor_incarnation,
                    f"input_child_{index}",
                    conversation_id,
                )
                commands.append(
                    ChildCommand(
                        f"input_command_{index}",
                        child,
                        ChildOperation.write_prompt,
                        None,
                        {
                            "content": encode_value(
                                (
                                    MessageFile(
                                        kept.stored_file_id,
                                        "application/octet-stream",
                                        "input.bin",
                                        len(contents),
                                    ),
                                )
                            )
                        },
                    )
                )
                paths.append(
                    harness.mac.paths.root
                    / "artifacts/files/conversations"
                    / conversation_id
                    / kept.stored_file_id
                )
            await asyncio.wait_for(
                asyncio.gather(
                    *(harness.endpoint._transfer_command_inputs(command) for command in commands)
                ),
                5,
            )
            assert [path.read_bytes() for path in paths] == [contents, contents]
        finally:
            await harness.close()

    asyncio.run(run())


def test_real_server_lifespan_restart_reaccepts_same_mac_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core.config import load_config
    from planner.core.server import create_app
    from planner.executor import mac as mac_module

    async def run() -> None:
        monkeypatch.setattr(mac_module, "RECONNECT_INTERVAL_SECONDS", 0.02)
        monkeypatch.setenv("PLAN_EXECUTOR_MODE", "remote")
        socket_path = tmp_path / "server.sock"
        monkeypatch.setenv("PLAN_EXECUTOR_VPS_SOCKET", str(socket_path))
        db_path = str(tmp_path / "server/planning.db")
        Path(db_path).parent.mkdir()
        connection = connect(db_path)
        create_schema(connection)
        connection.close()
        config = load_config(
            path=None,
            env={
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": db_path,
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_APP_SHA": "a" * 40,
            },
        )
        ssh_stack = contextlib.ExitStack()
        ssh_config = ssh_stack.enter_context(isolated_sshd(tmp_path / "ssh"))
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path / "mac"),
            revision="a" * 40,
            ssh_config=ssh_config,
            remote_command=shlex.join(production_relay_argv(socket_path)),
            panels_server_url="http://unused",
        )
        task = asyncio.create_task(mac.run())
        try:
            first_app = create_app(config, RealClock(), lambda: connect(db_path))
            async with first_app.router.lifespan_context(first_app):
                await eventually(
                    lambda: first_app.state.executor_endpoint.ready,
                    "first server did not reconcile",
                )
                identity = mac.identity
            second_app = create_app(config, RealClock(), lambda: connect(db_path))
            async with second_app.router.lifespan_context(second_app):
                await eventually(
                    lambda: second_app.state.executor_endpoint.ready,
                    "restarted server rejected surviving Mac incarnation",
                )
                assert mac.identity.executor_incarnation == identity.executor_incarnation
                assert mac.identity.connection_generation > identity.connection_generation
                mac._stop.set()
                if second_app.state.executor_endpoint.writer is not None:
                    second_app.state.executor_endpoint.writer.close()
                await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            ssh_stack.close()

    asyncio.run(run())


def test_historical_permission_evidence_cannot_overflow_reconnect_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            child = next(iter(harness.mac.registry.children.values())).reference
            for index in range(600):
                # This history exceeds the 1MiB frame bound when evidence is
                # echoed as one list. It remains available in the VPS read store.
                ask = AskReference(
                    child, TurnToken("c_scripted", index + 2), f"historical_{index}_" + "x" * 2048
                )
                harness.endpoint.store.publish_ask(ask, ("allow", "deny"), 1000)
            await harness.restart_vps_core()
            assert harness.endpoint.ready
            assert len(harness.endpoint.store.evidence_for_conversation("c_scripted")) == 600
        finally:
            await harness.close()

    asyncio.run(run())


INPUT_ANSWERS = (UserInputAnswer("q_two", ("Red", "Blue")), UserInputAnswer("q_one", ("Yes",)))
ORDERED_INPUT_ANSWERS = (INPUT_ANSWERS[1], INPUT_ANSWERS[0])


def test_complete_user_input_map_is_durable_before_drop_and_normalized_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_user_input()
            await harness.wait_input_reachable()
            invalid_maps = (
                (INPUT_ANSWERS[0],),
                (UserInputAnswer("q_one", ("Yes", "No")), INPUT_ANSWERS[0]),
                (UserInputAnswer("q_one", ("Other",)), INPUT_ANSWERS[0]),
                (INPUT_ANSWERS[0], INPUT_ANSWERS[0]),
            )
            for answers in invalid_maps:
                assert not await harness.endpoint.answer_user_input(
                    "c_scripted", "request_input", answers
                )
            assert harness.endpoint.store.recorded_answer(harness.input_ask) is None
            generation = harness.mac.identity.connection_generation
            answer = harness.endpoint.store.choose_user_input_answer(
                harness.input_ask, INPUT_ANSWERS, generation, 1000
            )
            assert answer.option_id is None and answer.user_input_answers == ORDERED_INPUT_ANSWERS
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "input did not reconcile",
            )
            await eventually(
                lambda: harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)],
                "stored map did not reach native callback",
            )
            await eventually(
                lambda: harness.events("user_input_answered") == 1,
                "application did not become canonical",
            )
            assert harness.endpoint.store.recorded_answer(harness.input_ask) == answer
            await harness.restart_vps_core()
            await eventually(
                lambda: not harness.mac.spool.pending(), "input application receipts did not drain"
            )
            assert harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)]
            assert harness.events("user_input_answered") == 1
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("loss", ["callback", "child"])
def test_selected_user_input_map_survives_missing_callback_without_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loss: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_user_input()
            await harness.wait_input_reachable()
            ask = harness.input_ask
            answer = harness.endpoint.store.choose_user_input_answer(
                ask, INPUT_ANSWERS, harness.mac.identity.connection_generation, 1000
            )
            child = next(iter(harness.mac.registry.children.values()))
            if loss == "callback":
                child.user_inputs.clear()
            else:
                harness.mac.registry.children.clear()
            generation = harness.mac.identity.connection_generation
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "missing input did not reconcile",
            )
            evidence = harness.endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.reachability is (
                AskReachability.callback_gone if loss == "callback" else AskReachability.child_gone
            )
            assert evidence.applied_at_mac_ms is None
            assert harness.endpoint.store.recorded_answer(ask) == answer
            assert harness.native.user_answers == []
            assert harness.events("user_input_answered") == 0
        finally:
            await harness.close()

    asyncio.run(run())


def test_user_input_request_id_reuse_gets_separate_answer_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_user_input()
            await harness.wait_input_reachable()
            first = harness.input_ask
            assert await harness.endpoint.answer_user_input(
                "c_scripted", "request_input", INPUT_ANSWERS
            )
            await eventually(
                lambda: harness.events("user_input_answered") == 1,
                "first input answer did not apply",
            )
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(
                lambda: harness.events("turn_ended") == 1, "first input turn did not end"
            )
            await harness.system.send(
                "c_scripted", text_message_content("Next input turn"), sender_label="Test"
            )
            await harness.native.raise_user_input()
            await harness.wait_input_reachable()
            second_answers = (
                UserInputAnswer("q_one", ("No",)),
                UserInputAnswer("q_two", ("Blue",)),
            )
            assert harness.input_ask != first
            assert await harness.endpoint.answer_user_input(
                "c_scripted", "request_input", second_answers
            )
            await eventually(
                lambda: harness.events("user_input_answered") == 2,
                "reused input request lacked new history",
            )
            await eventually(lambda: not harness.mac.spool.pending(), "input results did not drain")
            assert harness.native.user_answers == [
                ("request_input", ORDERED_INPUT_ANSWERS),
                ("request_input", second_answers),
            ]
            assert harness.events("user_input_answered") == 2
        finally:
            await harness.close()

    asyncio.run(run())


def test_user_input_callback_application_survives_lost_receipt_without_reanswer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.api import ConversationRuntime, _pending_user_input
    from planner.conversation.backend_usage import BackendUsageService
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.snapshot import BackendSnapshotService

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_user_input()
            await harness.wait_input_reachable()
            runtime = ConversationRuntime(
                system=harness.system,
                store=harness.system._store,
                live_tail=ConversationLiveTail(),
                backend_snapshots=BackendSnapshotService(),
                backend_usage=BackendUsageService({}),
                message_files=ConversationMessageFiles(harness.endpoint.db_path),
                database_path=harness.endpoint.db_path,
                sse_heartbeat_ms=1000,
                executor_endpoint=harness.endpoint,
            )
            pending = await _pending_user_input(runtime, "c_scripted")
            assert pending is not None and pending["answer_available"] is True
            assert len(pending["questions"]) == 2
            generation = harness.mac.identity.connection_generation
            harness.endpoint.cut_user_input_application = True
            assert await harness.endpoint.answer_user_input(
                "c_scripted", "request_input", INPUT_ANSWERS
            )
            await eventually(
                lambda: harness.endpoint.cut_count == 1,
                "input application acknowledgment was not cut",
            )
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "input application did not reconcile",
            )
            await eventually(
                lambda: not harness.mac.spool.pending(), "input application replay did not drain"
            )
            assert harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)]
            assert harness.events("user_input_answered") == 1
            replacement = (UserInputAnswer("q_one", ("No",)), UserInputAnswer("q_two", ("Blue",)))
            assert not await harness.endpoint.answer_user_input(
                "c_scripted", "request_input", replacement
            )
            assert harness.events("user_input_answered") == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_refuses_protocol_one_before_replay_without_fencing_current_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from planner.executor.codec import encode_value
    from planner.executor.contracts import ExecutorHealth, ExecutorIdentity
    from planner.executor.vps import vps_socket_path
    from planner.executor.wire import write_frame

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            identity = harness.endpoint.identity
            connection = connect(harness.endpoint.db_path)
            before = connection.execute("SELECT count(*) FROM executor_receipts").fetchone()[0]
            reader, writer = await asyncio.open_unix_connection(
                str(vps_socket_path(harness.endpoint.db_path))
            )
            old = ExecutorHealth(
                ExecutorIdentity(1, "a" * 40, 1, str(uuid4()), 1),
                42,
                ConnectionState.disconnected,
                False,
                1000,
                None,
            )
            payload = encode_value(old)
            assert isinstance(payload, dict)
            await write_frame(writer, WireMessage(WireMessageKind.hello, "old_protocol", payload))
            assert await asyncio.wait_for(reader.read(1), 1) == b""
            writer.close()
            await writer.wait_closed()
            assert harness.endpoint.identity == identity and harness.endpoint.ready
            assert (
                connection.execute("SELECT count(*) FROM executor_receipts").fetchone()[0] == before
            )
            connection.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("termination", ["eof", "reset"])
def test_production_relay_exits_when_peer_ends_while_stdin_stays_open(
    tmp_path: Path, termination: str
) -> None:
    async def run() -> None:
        socket_path = tmp_path / "relay.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen()
        listener.setblocking(False)
        process = await asyncio.create_subprocess_exec(
            *production_relay_argv(socket_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        peer: socket.socket | None = None
        try:
            loop = asyncio.get_running_loop()
            peer, _ = await asyncio.wait_for(loop.sock_accept(listener), 2)
            assert peer is not None
            assert process.stdin is not None
            assert not process.stdin.is_closing()
            if termination == "reset":
                # Leave bytes unread at the Unix peer, so its abrupt close causes
                # ECONNRESET in the production relay's socket receive task.
                process.stdin.write(b"leave these peer bytes unread")
                await process.stdin.drain()
                assert await asyncio.wait_for(loop.sock_recv(peer, 1), 2) == b"l"
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            else:
                await loop.sock_sendall(peer, b"production relay output")
            # Deliberately keep the subprocess stdin writer open. Input EOF must
            # not be what lets either peer-termination case finish.
            assert not process.stdin.is_closing()
            peer.close()
            peer = None
            returncode = await asyncio.wait_for(process.wait(), 2)
            assert process.stdout is not None and process.stderr is not None
            stdout, stderr = await asyncio.gather(process.stdout.read(), process.stderr.read())
            if termination == "eof":
                assert returncode == 0
                assert stdout == b"production relay output"
                assert stderr == b""
            else:
                assert returncode != 0
                assert b"ConnectionResetError" in stderr
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.kill()
                await process.wait()
            if peer is not None:
                peer.close()
            listener.close()

    asyncio.run(run())


@pytest.mark.parametrize("invalid_ack", ["empty", "mismatched"])
def test_artifact_slot_must_validate_before_publication_and_backend_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_ack: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            reject = True
            rejected = asyncio.Event()
            completed = 0
            send = harness.endpoint._send
            replay = harness.mac._replay_envelope

            async def corrupt_ack(message: WireMessage) -> None:
                if reject and message.kind is WireMessageKind.file_slot:
                    payload = {} if invalid_ack == "empty" else dict(message.payload)
                    if invalid_ack == "mismatched":
                        payload["direction"] = "download"
                    message = WireMessage(message.kind, message.request_id, payload)
                await send(message)

            async def observe_rejection(envelope: QueuedEnvelope) -> None:
                try:
                    await replay(envelope)
                except ValueError:
                    rejected.set()
                    raise

            request = harness.mac._request

            async def count_completions(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                nonlocal completed
                if kind is WireMessageKind.file_complete:
                    completed += 1
                return await request(kind, payload)

            monkeypatch.setattr(harness.endpoint, "_send", corrupt_ack)
            monkeypatch.setattr(harness.mac, "_replay_envelope", observe_rejection)
            monkeypatch.setattr(harness.mac, "_request", count_completions)
            artifact = harness.mac.paths.artifacts / "t_scripted" / "typed-ack.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            contents = bytes(range(256)) * 2000
            artifact.write_bytes(contents)
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.tool_call_finished(
                harness.native.turn,
                tool_call_id="typed_ack",
                tool_call_status=ToolCallStatus.completed,
                detail="The file needs an authorized upload slot.",
            )
            await asyncio.wait_for(rejected.wait(), 8)
            target = tmp_path / "vps/files/tickets/t_scripted/typed-ack.bin"
            assert not target.exists()
            assert harness.events("tool_call_finished") == 0
            assert completed == 0
            assert any(
                row.kind is EnvelopeKind.backend_event for row in harness.mac.spool.pending()
            )
            assert artifact.read_bytes() == contents
            reject = False
            await eventually(
                lambda: harness.events("tool_call_finished") == 1,
                "Corrected artifact acknowledgment did not allow the dependent event",
            )
            assert target.read_bytes() == contents
            await eventually(
                lambda: not harness.mac.spool.pending(), "Artifact receipt did not drain"
            )
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("invalid_ack", ["empty", "mismatched", "completion_refused"])
def test_input_receipt_must_validate_before_command_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_ack: str
) -> None:
    from planner.conversation.message_content import MessageFile
    from planner.executor.codec import encode_value
    from planner.executor.contracts import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        caller = None
        try:
            reject = True
            rejected = asyncio.Event()
            completions = 0
            commands = 0
            send = harness.mac._send
            request = harness.endpoint._request
            endpoint_send = harness.endpoint._send

            async def corrupt_ack(message: WireMessage) -> None:
                if reject and message.kind is WireMessageKind.file_accepted:
                    payload = {} if invalid_ack == "empty" else dict(message.payload)
                    if invalid_ack == "mismatched":
                        payload["byte_count"] = -1
                    message = WireMessage(
                        WireMessageKind.error
                        if invalid_ack == "completion_refused"
                        else message.kind,
                        message.request_id,
                        payload,
                    )
                    rejected.set()
                await send(message)

            async def count_completions(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                nonlocal completions
                if kind is WireMessageKind.file_slot:
                    completions += 1
                return await request(kind, payload)

            async def count_commands(message: WireMessage) -> None:
                nonlocal commands
                if message.kind is WireMessageKind.command:
                    commands += 1
                await endpoint_send(message)

            monkeypatch.setattr(harness.mac, "_send", corrupt_ack)
            monkeypatch.setattr(harness.endpoint, "_request", count_completions)
            monkeypatch.setattr(harness.endpoint, "_send", count_commands)
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.runtime.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            command = ChildCommand(
                "input_ack_barrier",
                harness.ask.child,
                ChildOperation.write_prompt,
                harness.native.turn,
                {
                    "content": encode_value(
                        (
                            MessageFile(
                                kept.stored_file_id,
                                "application/octet-stream",
                                "input.bin",
                                len(contents),
                            ),
                        )
                    ),
                    "sender_content": encode_value(text_message_content("Inspect the bytes")),
                    "sender_label": "Test",
                    "mode": "queue",
                },
            )
            target = (
                harness.mac.paths.root
                / "artifacts/files/conversations/c_scripted"
                / kept.stored_file_id
            )
            generation = harness.mac.identity.connection_generation
            harness.native.required_input_path = target
            harness.native.required_input_contents = contents
            caller = asyncio.create_task(harness.endpoint.send_command(command))
            await asyncio.wait_for(rejected.wait(), 8)
            await eventually(
                lambda: harness.mac.identity.connection_generation > generation,
                "Invalid input acknowledgment did not reconnect",
            )
            assert not caller.done()
            pending = harness.endpoint.pending_commands[command.command_id]
            assert pending[0] == command and not pending[1].done()
            assert commands == 0
            assert completions > 0
            assert harness.native.prompt_count == 1
            assert target.exists()
            assert (
                await harness.endpoint.runtime.message_files.read("c_scripted", kept.stored_file_id)
                == contents
            )
            rejected_completions = completions
            reject = False
            outcome = await asyncio.wait_for(asyncio.shield(caller), 8)
            assert outcome.command_id == command.command_id
            assert outcome.disposition is CommandDisposition.succeeded
            assert target.read_bytes() == contents
            assert completions == rejected_completions + 1 and commands == 1
            assert harness.native.prompt_count == 2
            assert command.command_id not in harness.endpoint.pending_commands

        finally:
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["prompt", "user_input_answer"])
def test_reconnect_acknowledges_settled_registry_outcomes_after_direct_ack_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from planner.executor.codec import encode_value
    from planner.executor.contracts import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await eventually(
                lambda: (
                    not harness.mac.registry.inventory(
                        harness.mac.identity
                    ).retained_command_outcomes
                ),
                "Initial registry outcomes did not settle",
            )
            if operation == "user_input_answer":
                await harness.native.raise_user_input()
                await harness.wait_input_reachable()
            generation = harness.mac.identity.connection_generation
            dropped: set[str] = set()
            send = harness.endpoint._send

            async def lose_old_generation_ack(message: WireMessage) -> None:
                if (
                    message.kind is WireMessageKind.command_outcome_ack
                    and harness.mac.identity.connection_generation == generation
                ):
                    dropped.add(str(message.payload["command_id"]))
                    return
                await send(message)

            monkeypatch.setattr(harness.endpoint, "_send", lose_old_generation_ack)
            if operation == "prompt":
                await harness.endpoint.send_command(
                    ChildCommand(
                        "settled_prompt",
                        harness.ask.child,
                        ChildOperation.write_prompt,
                        harness.native.turn,
                        {
                            "content": encode_value(text_message_content("One more prompt")),
                            "sender_content": encode_value(text_message_content("One more prompt")),
                            "sender_label": "Test",
                            "mode": "queue",
                        },
                    )
                )
            else:
                assert await harness.endpoint.answer_user_input(
                    "c_scripted", "request_input", INPUT_ANSWERS
                )
            await eventually(lambda: bool(dropped), "Direct command acknowledgment was not lost")
            await eventually(
                lambda: not harness.mac.spool.pending(), "Filesystem outcome spool did not drain"
            )
            # A durable receipt removes the filesystem entry, but cannot confirm
            # the live registry's retained result: these are separate inventories.
            retained = harness.mac.registry.inventory(
                harness.mac.identity
            ).retained_command_outcomes
            assert dropped <= {outcome.command_id for outcome in retained}
            assert len(retained) == (2 if operation == "user_input_answer" else 1)
            assert all(outcome.disposition is CommandDisposition.succeeded for outcome in retained)
            assert any("answer_application" in outcome.result for outcome in retained) == (
                operation == "user_input_answer"
            )
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "Link did not reconnect after direct acknowledgment loss",
            )
            await eventually(
                lambda: (
                    not harness.mac.registry.inventory(
                        harness.mac.identity
                    ).retained_command_outcomes
                ),
                "Accepted outcomes remained in the in-memory registry inventory",
            )
            assert not harness.mac.spool.pending()
            # Reconcile another generation to prove settled results stay absent.
            generation = harness.mac.identity.connection_generation
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "Second inventory did not reconcile",
            )
            assert not harness.mac.registry.inventory(
                harness.mac.identity
            ).retained_command_outcomes
            assert not harness.mac.spool.pending()
            assert harness.native.start_count == 1
            assert harness.native.prompt_count == (2 if operation == "prompt" else 1)
            assert harness.native.user_answers == (
                [("request_input", ORDERED_INPUT_ANSWERS)]
                if operation == "user_input_answer"
                else []
            )
            assert harness.events("user_input_answered") == (
                1 if operation == "user_input_answer" else 0
            )
        finally:
            await harness.close()

    asyncio.run(run())
