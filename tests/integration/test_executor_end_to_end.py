"""Real Mac/VPS dispatch over subprocess stdio and a temporary Unix socket.

The scripted native adapter never calls a provider. Every event still traverses
native registry, durable spool, framed transport, VPS acceptance and conversation
core. These are process/SQLite gates; no browser is needed for this boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    ResolvedConversationStart,
)
from planner.conversation.events import PermissionAskOption, ToolCallStatus
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AskReachability,
    AskReference,
    ChildReference,
    ConnectionState,
    EnvelopeKind,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor
from planner.executor.publication import PublicationReceiver
from planner.executor.runtime import NativeChildRegistry
from planner.executor.vps import ExecutorEndpoint

# This relay transports bytes only, like the resident's SSH stdio relay. It is a
# separate process, so EOF and a lost acknowledgment exercise the actual framing.
RELAY = """
import os, socket, sys, threading
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(sys.argv[1])
def receive():
    while True:
        data = connection.recv(65536)
        if not data:
            os._exit(0)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
threading.Thread(target=receive, daemon=True).start()
while True:
    data = os.read(0, 65536)
    if not data:
        break
    connection.sendall(data)
"""


class ScriptedNativeChild:
    def __init__(self) -> None:
        self.sink: BackendEventSink | None = None
        self.turn: TurnToken | None = None
        self.start_count = 0
        self.prompt_count = 0
        self.answers: list[tuple[str, str]] = []

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        del resolved_start, vendor_session_cursor
        self.start_count += 1

    async def write_prompt(
        self, turn_token: TurnToken, content: MessageContent, **arguments: object
    ) -> BackendPromptAccepted:
        del content, arguments
        self.prompt_count += 1
        self.turn = turn_token
        return BackendPromptAccepted(True)

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self.answers.append((ask_id, option_id))

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

    async def _send(self, message: WireMessage) -> None:
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
        self.endpoint.runtime = SimpleNamespace(system=self.system)
        self.endpoint.file_receiver = PublicationReceiver(
            self.endpoint.store,
            self.root / "vps/files",
            self.root / "vps/quarantine",
        )
        await self.endpoint.start()
        await eventually(lambda: self.endpoint.ready, "VPS core restart did not reconcile")

    async def close(self) -> None:
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
    endpoint.runtime = SimpleNamespace(system=system)
    endpoint.file_receiver = PublicationReceiver(
        endpoint.store,
        vps_root / "files",
        vps_root / "quarantine",
    )
    await endpoint.start()
    native = ScriptedNativeChild()
    mac = MacExecutor(
        paths=ExecutorPaths(root / "mac"),
        revision="a" * 40,
        ssh_argv=(sys.executable, "-c", RELAY, str(socket_path)),
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
    harness = ExecutorHarness(root, endpoint, mac, system, native, task, [])
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
            system.send("c_scripted", text_message_content("Start"), sender_label="Test"), 5
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
            assert not harness.mac.spool.pending()
        finally:
            await harness.close()

    asyncio.run(run())
