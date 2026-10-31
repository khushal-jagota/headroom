"""Native context must reach retained Workers through actual provider adapters.

Commands cross the real remote adapter and native registry in process. Claude uses
its scripted SDK client. Hermes uses its existing separate ACP agent process.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from tests.support.conversation_claude_agent_sdk_bench import (
    SESSION_ID,
    _ScriptedClaudeSdkClient,
)
from tests.support.conversation_scripted_acp_agent import (
    ScriptedAcpAgentControl,
    scripted_acp_agent_launch,
)
from tests.support.executor_catalog import ScriptedCatalogObserver
from tests.support.executor_turns import MacRemoteChildFactory

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.backends.hermes_acp import (
    HermesAcpBackendChild,
    HermesAcpBackendChildFactory,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    HeldPromptPromotionMode,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryStarted,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import PromptEventPayload
from planner.conversation.message_content import (
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.configuration import NATIVE_WORKER_CONTEXT, ExecutorPaths
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    CommandOutcome,
    WritePrompt,
)
from planner.executor.vps_transport import VpsExecutor
from planner.projects.data import create_project
from planner.tickets import data as tickets
from planner.tickets.contracts import TicketLocale


@pytest.mark.parametrize("backend", [ConversationBackendKey.claude, ConversationBackendKey.hermes])
def test_native_worker_context_reaches_actual_provider_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: ConversationBackendKey
) -> None:
    async def exercise() -> None:
        for name, directory in (
            ("CODEX_HOME", "codex"),
            ("PLAN_HERMES_HOME", "hermes"),
            ("CLAUDE_CONFIG_DIR", "claude"),
        ):
            monkeypatch.setenv(name, str(tmp_path / directory))
        db_path = str(tmp_path / "panels.sqlite")
        connection = connect(db_path)
        try:
            create_schema(connection)
            project = create_project(connection, name="Native context", folder_path=tmp_path, now=1)
            ticket = tickets.create_ticket(
                connection,
                title="Actual prompt context",
                worker_type="coding",
                principal=OWNER_PRINCIPAL,
                now=1,
                title_max_chars=200,
                employee_backend=backend.value,
                employee_launch_model="a-model",
                selection_kind="concrete",
                locale=TicketLocale.macos,
                project_id=project.id,
            )
        finally:
            connection.close()
        store = ConversationStore(db_path)
        identity = (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id))
        resolved = ResolvedConversationStart(
            "c_native",
            backend,
            "a-model",
            None,
            ConversationRoleMaterials("Old persisted Worker role.", identity),
            tmp_path,
            ConversationAccess.full,
            workspace_path_policy=WorkspacePathPolicy.native_absolute,
        )
        await store.create_conversation(resolved)
        connection = connect(db_path)
        try:
            tickets.write_ticket_conversation_start(
                connection,
                ticket.id,
                conversation_id="c_native",
                backend=backend.value,
                model="a-model",
                reasoning_effort=None,
                now=2,
            )
        finally:
            connection.close()
        cursor = (
            SESSION_ID if backend is ConversationBackendKey.claude else "original-hermes-session"
        )
        await store.append_delivered_prompt(
            "c_native",
            prompt=PromptEventPayload(
                text_message_content("Previous delivered prompt"),
                "Panels",
                PromptDeliveryMode.queue,
            ),
            model_change=None,
        )
        await store.update_vendor_session_cursor("c_native", cursor)
        names = ("panels", "panels-worker", "panels-worker-coding")
        snapshot = CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(
                    name, f"---\nname: {name}\ndescription: test\n---\n{name} canonical text\n"
                )
                for name in names
            ),
            names,
        )
        clients: list[_ScriptedClaudeSdkClient] = []
        commands: list[tuple[ChildCommand, CommandOutcome]] = []
        control = ScriptedAcpAgentControl(str(tmp_path / "agent.sock"))

        def make_client(options: Any, *, emission_check: Any = None) -> _ScriptedClaudeSdkClient:
            client = _ScriptedClaudeSdkClient(
                options, handshake={"commands": [{"name": "inspect", "description": "Inspect"}]}
            )
            clients.append(client)
            return client

        def factory(
            start: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            if backend is ConversationBackendKey.claude:
                return ClaudeAgentSdkBackendChildFactory(
                    ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")),
                    client_factory=make_client,
                )
            return HermesAcpBackendChildFactory(
                scripted_acp_agent_launch(control_socket_path=control.socket_path)
            )

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_native")
        spool = MacOutbox(tmp_path / "spool")
        registry = MacRunner(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(db_path),
        )

        async def send(command: ChildCommand) -> CommandOutcome:
            outcome = await registry.run(command)
            commands.append((command, outcome))
            return outcome

        remote = MacRemoteChildFactory(
            lambda start, sink: reference, send, lambda start: snapshot, db_path=db_path
        )
        system = SqliteProcessConversationSystem(
            store=store,
            message_files=ConversationMessageFiles(db_path),
            backend_child_factories={key: remote for key in ConversationBackendKey},
        )
        endpoint = VpsExecutor(db_path, RealClock())
        executor_identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
        )
        endpoint.runs.identity = executor_identity
        endpoint.runs.ready = True
        endpoint.runs.children[reference.child_id] = remote.recover(reference)
        observer = ScriptedCatalogObserver(lambda: executor_identity)
        system.configure_destination_validation(observer, endpoint=endpoint.catalog)
        recipient = Principal(PrincipalKind.ticket, ticket.id)

        async def deliver(text: str, **options: Any) -> Any:
            return await system.send(
                "c_native",
                text_message_content(text),
                sender_label="Chief",
                sender=CHIEF_PRINCIPAL,
                recipient=recipient,
                reply_requested=True,
                **options,
            )

        async def account() -> dict[str, Any]:
            result = await control.send({"command": "report"})
            assert result is not None
            return result

        try:
            assert await deliver("ordinary authored bytes") == PromptDeliveryStarted()
            await deliver("held steer authored bytes")
            held = (await system.held_prompts("c_native"))[0]
            assert (
                await system.promote_held_prompt(
                    "c_native", held.held_prompt_id, HeldPromptPromotionMode.steer
                )
                == PromptDeliveryInjected()
            )
            if backend is ConversationBackendKey.claude:
                prompt_texts = clients[0].prompts + [
                    "".join(block["text"] for block in message["message"]["content"])
                    for message in clients[0].streamed_messages
                ]
                assert clients[0].options.resume == cursor
            else:
                report = await account()
                prompt_texts = [write["text"] for write in report["prompt_writes"]] + [
                    write["text"] for write in report["steer_writes"]
                ]
                assert report["loaded_from"] == cursor
            assert len(prompt_texts) == 2
            for prompt, authored in zip(
                prompt_texts,
                (
                    "ordinary authored bytes",
                    "held steer authored bytes",
                ),
                strict=True,
            ):
                assert prompt.startswith("[Authenticated Panels reply requirement]\n")
                assert prompt.count(NATIVE_WORKER_CONTEXT) == 1
                assert prompt.index(NATIVE_WORKER_CONTEXT) < prompt.index("Chief:\n" + authored)
                assert prompt.endswith("Chief:\n" + authored)
            assert all("Old persisted Worker role." not in prompt for prompt in prompt_texts)
            if backend is ConversationBackendKey.hermes:
                await control.send(
                    {
                        "command": "emit_available_commands",
                        "commands": [{"name": "inspect", "description": "Inspect"}],
                    }
                )
                native = registry._children[reference.child_id].native
                assert isinstance(native, HermesAcpBackendChild)
                async with asyncio.timeout(2):
                    while not native._is_catalog_command(text_message_content("/inspect")):
                        await asyncio.sleep(0.001)
            assert (
                await deliver("/inspect", mode=PromptDeliveryMode.steer) == PromptDeliveryInjected()
            )
            _, command_result = commands[-1]
            assert command_result.result["composed_content_delivered"] is False
            if backend is ConversationBackendKey.claude:
                assert clients[0].streamed_messages[-1]["message"]["content"] == [
                    {"type": "text", "text": "/inspect"}
                ]
            else:
                assert (await account())["steer_writes"][-1]["text"] == "/inspect"
        finally:
            await system.shutdown()
            spool.close()

    asyncio.run(asyncio.wait_for(exercise(), 20))


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_refused_native_write_rebinds_only_after_confirmed_cleanup(
    tmp_path: Path, cleanup_fails: bool
) -> None:
    """The original rebind deadlock and failure must be visible at the send seam."""
    from planner.conversation.contracts import (
        ConversationStartRequest,
        PromptDeliveryQueued,
        PromptDeliveryRefused,
    )
    from planner.executor.protocol import CommandDisposition
    from planner.executor.vps_inbox import InboxStore

    async def exercise() -> None:
        db_path = str(tmp_path / "panels.sqlite")
        connection = connect(db_path)
        create_schema(connection)
        connection.close()
        commands: list[ChildCommand] = []
        cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event()
        executor_store = InboxStore(db_path)
        snapshot = CanonicalSkillSnapshot(
            tuple(CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\ntext")
                  for name in ("panels", "panels-worker")),
            ("panels", "panels-worker"),
        )

        async def dispatch(command: ChildCommand) -> CommandOutcome:
            commands.append(command)
            if command.operation is ChildOperation.write_prompt and len(commands) == 2:
                return CommandOutcome(
                    command.command_id, command.child, CommandDisposition.refused,
                    {"error_type": "NeedsRebind", "error_message": "model changed"},
                )
            if command.operation is ChildOperation.stop and not cleanup_entered.is_set():
                cleanup_entered.set()
                await release_cleanup.wait()
                if cleanup_fails:
                    raise RuntimeError("cleanup did not confirm exit")
            return CommandOutcome(command.command_id, command.child, CommandDisposition.succeeded,
                                  {"composed_content_delivered": True})

        def reference(start: ResolvedConversationStart, sink: Any) -> ChildReference:
            child = ChildReference("test-incarnation", str(uuid4()), "c")
            executor_store.runs.register_child(child, "ticket", "t_native", 1)
            return child

        remote = MacRemoteChildFactory(reference, dispatch, lambda start: snapshot, db_path=db_path)
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path), message_files=ConversationMessageFiles(db_path),
            backend_child_factories={key: remote for key in ConversationBackendKey},
        )
        try:
            await system.start_conversation(
                ConversationStartRequest("c", "model", workspace_folder=tmp_path)
            )
            delivery = asyncio.create_task(
                system.send("c", text_message_content("original bytes"), sender_label="Test")
            )
            await cleanup_entered.wait()
            assert not delivery.done()
            assert isinstance(await system.send(
                "c", text_message_content("queued bytes"), sender_label="Other"
            ), PromptDeliveryQueued)
            assert [c.operation for c in commands] == [
                ChildOperation.start, ChildOperation.write_prompt, ChildOperation.stop,
            ]
            release_cleanup.set()
            result = await delivery
            written = [c for c in commands if c.operation is ChildOperation.write_prompt]
            if cleanup_fails:
                assert isinstance(result, PromptDeliveryRefused)
                assert len(written) == 1
            else:
                assert result == PromptDeliveryStarted()
                assert len(written) == 2 and written[0].child != written[1].child
                assert isinstance(written[0].arguments, WritePrompt)
                assert isinstance(written[1].arguments, WritePrompt)
                assert written[0].arguments.content == written[1].arguments.content
        finally:
            release_cleanup.set()
            await system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 10))
