"""Real Claude cleanup failures must survive executor and canonical retirement gates."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.support.conversation_claude_agent_sdk_bench import _ScriptedClaudeSdkClient
from tests.unit.test_executor_native_runtime import canonical_snapshot
from tests.unit.test_executor_source_retirement import IDENTITY, drain
from tests.unit.test_ticket_move_lifecycle import endpoint

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    MoveRetirementDisposition,
    ResolvedConversationStart,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.executor.codec import encode_value
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ReceiptDisposition,
    SourceRetirementRequest,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry


def claude_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[NativeChildRegistry, list[_ScriptedClaudeSdkClient]]:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    clients: list[_ScriptedClaudeSdkClient] = []

    def client(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
        scripted = _ScriptedClaudeSdkClient(options)
        clients.append(scripted)
        return scripted

    def factory(
        resolved: ResolvedConversationStart, capability: str, reference: ChildReference
    ) -> BackendChildFactory:
        return ClaudeAgentSdkBackendChildFactory(
            ClaudeAgentSdkChildLaunch(claude_executable=Path("/unused/claude")),
            client_factory=client,
        )

    return NativeChildRegistry(
        incarnation=IDENTITY.executor_incarnation,
        spool=FilesystemSpool(tmp_path / "spool"),
        factory=factory,
        paths=ExecutorPaths(tmp_path / "executor"),
        message_files=ConversationMessageFiles(tmp_path / "unused.db"),
    ), clients


async def disconnect_failure() -> None:
    raise RuntimeError("disconnect failed before native termination")


@pytest.mark.parametrize("fails", [True, False])
def test_claude_stop_result_controls_canonical_retirement(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    """Gap: scripted native stop hid Claude's swallowed disconnect failures."""

    async def run() -> None:
        remote = endpoint(tmp_db)
        registry, clients = claude_registry(tmp_path, monkeypatch)
        commands: list[ChildCommand] = []

        async def send(command: ChildCommand) -> CommandOutcome:
            commands.append(command)
            outcome = await registry.execute_command(command)
            for envelope in registry.spool.pending():
                receipt = await remote.accept_envelope(envelope, IDENTITY)
                assert receipt.disposition is ReceiptDisposition.accepted
                registry.spool.acknowledge(receipt)
                if envelope.kind is EnvelopeKind.command_outcome:
                    registry.confirm_command(str(envelope.payload["command_id"]))
            return outcome

        remote.factory.send_command = send
        remote.factory.skill_snapshot_supplier = lambda _: canonical_snapshot()
        system = SqliteProcessConversationSystem(
            store=ConversationStore(remote.db_path),
            backend_child_factories=dict.fromkeys(ConversationBackendKey, remote.factory),
            message_files=ConversationMessageFiles(remote.db_path),
        )
        remote.runtime = cast(Any, SimpleNamespace(system=system))
        native: ClaudeAgentSdkBackendChild | None = None
        original_disconnect = None
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    "c_cleanup",
                    "test",
                    backend_key=ConversationBackendKey.claude,
                    workspace_folder=tmp_path,
                    role_materials=ConversationRoleMaterials(
                        "worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_cleanup"))
                    ),
                )
            )
            await system.send("c_cleanup", text_message_content("hello"), sender_label="Owner")
            state = system._conversations["c_cleanup"]
            reference = state.executor_child_reference
            assert reference is not None
            child = registry.children[reference.child_id]
            native = cast(ClaudeAgentSdkBackendChild, child.native)
            client = clients[0]
            original_disconnect = client.disconnect
            if fails:
                monkeypatch.setattr(client, "disconnect", disconnect_failure)
            await system.kill("c_cleanup")
            assert state.initial_shutdown_task is not None
            assert await state.initial_shutdown_task is (not fails)
            stop = next(command for command in commands if command.operation is ChildOperation.stop)
            first = registry.outcomes[stop.command_id]
            assert first.disposition is (
                CommandDisposition.uncertain if fails else CommandDisposition.succeeded
            )
            metadata = await system._store.read_executor_turn("c_cleanup")
            assert metadata is not None
            assert json.loads(metadata[2])["source_retired"] is (not fails)
            if fails:
                assert registry.children[reference.child_id] is child
                assert child.native is native and native._client is client
                assert state.child_is_quarantined and state.executor_child_reference == reference
                assert remote.store.child_assignment(reference) == ("ticket", "t_cleanup")
                with pytest.raises(PermissionError):
                    registry.authenticate(reference.child_id, child.capability)
                before = list(client.prompts), list(client.streamed_messages)
                await system.send(
                    "c_cleanup", text_message_content("blocked"), sender_label="Owner"
                )
                assert (client.prompts, client.streamed_messages) == before
                assert len(clients) == 1
                # A later positive cleanup never rewrites the first failed command.
                monkeypatch.setattr(client, "disconnect", original_disconnect)
                fresh = replace(stop, command_id="explicit-cleanup-proof")
                assert (await send(fresh)).disposition is CommandDisposition.succeeded
                assert await registry.execute_command(stop) == first
                stored = tmp_db.execute(
                    "SELECT outcome_json FROM executor_command_outcomes WHERE command_id=?",
                    (stop.command_id,),
                ).fetchone()
                assert stored is not None and json.loads(stored[0])["disposition"] == "uncertain"
            else:
                assert client.disconnected and native._client is None
                assert reference.child_id not in registry.children
                assert remote.store.child_assignment(reference) is None
                assert not state.child_is_quarantined and state.executor_child_reference is None
        finally:
            if clients and original_disconnect is not None:
                monkeypatch.setattr(clients[0], "disconnect", original_disconnect)
            await system.shutdown()
            if native is not None:
                await native.stop()
            cast(FilesystemSpool, registry.spool).close()

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["move", "quiesce"])
def test_claude_disconnect_failure_cannot_confirm_source_move_or_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Gap: these gates previously exercised only a fake native stop exception."""

    async def run() -> None:
        registry, clients = claude_registry(tmp_path, monkeypatch)
        reference = ChildReference(IDENTITY.executor_incarnation, "cleanup-child", "c_source")
        resolved = ResolvedConversationStart(
            "c_source",
            ConversationBackendKey.claude,
            "test",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_source"),)),
            tmp_path,
            ConversationAccess.full,
        )
        start = ChildCommand(
            "start",
            reference,
            ChildOperation.start,
            None,
            {
                "resolved_start": encode_value(resolved),
                "canonical_skill_snapshot": encode_value(canonical_snapshot()),
            },
        )
        assert (await registry.execute_command(start)).disposition is CommandDisposition.succeeded
        child = registry.children[reference.child_id]
        native = cast(ClaudeAgentSdkBackendChild, child.native)
        client = clients[0]
        original_disconnect = client.disconnect
        monkeypatch.setattr(client, "disconnect", disconnect_failure)
        drain(registry)
        try:
            if boundary == "move":
                request = SourceRetirementRequest(IDENTITY, "t_source", "c_source", (reference,))
                receipt = await registry.retire_source("move", request, IDENTITY)
                assert receipt.result.disposition is MoveRetirementDisposition.uncertain
                assert await registry.retire_source("move", request, IDENTITY) == receipt
                delayed = replace(start, command_id="delayed-start")
                assert (
                    await registry.execute_command(delayed)
                ).disposition is CommandDisposition.refused
            else:
                with pytest.raises(RuntimeError, match="before native termination"):
                    await registry.quiesce()
                assert registry.admission_stopped
            assert registry.children[reference.child_id] is child
            assert child.native is native and native._client is client
            assert not client.disconnected
        finally:
            monkeypatch.setattr(client, "disconnect", original_disconnect)
            await native.stop()
            cast(FilesystemSpool, registry.spool).close()

    asyncio.run(run())
