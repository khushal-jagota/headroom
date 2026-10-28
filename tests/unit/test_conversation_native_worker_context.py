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
from pydantic import TypeAdapter
from tests.support.conversation_claude_agent_sdk_bench import (
    SESSION_ID,
    _ScriptedClaudeSdkClient,
)
from tests.support.conversation_scripted_acp_agent import (
    ScriptedAcpAgentControl,
    scripted_acp_agent_launch,
)

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
    MessageContent,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.executor.codec import encode_value
from planner.executor.configuration import NATIVE_WORKER_CONTEXT, ExecutorPaths
from planner.executor.contracts import (
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandOutcome,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.remote import RemoteBackendChildFactory
from planner.executor.runtime import NativeChildRegistry


@pytest.mark.parametrize("backend", [ConversationBackendKey.claude, ConversationBackendKey.hermes])
@pytest.mark.parametrize("retained", [False, True], ids=["new", "retained"])
def test_native_worker_context_reaches_actual_provider_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: ConversationBackendKey, retained: bool
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
        finally:
            connection.close()
        store = ConversationStore(db_path)
        identity = (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_native"))
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
        cursor = (
            SESSION_ID if backend is ConversationBackendKey.claude else "original-hermes-session"
        )
        if retained:
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
        native_starts: list[ResolvedConversationStart] = []
        commands: list[tuple[ChildCommand, CommandOutcome]] = []
        control = ScriptedAcpAgentControl(str(tmp_path / "agent.sock"))

        def make_client(options: Any) -> _ScriptedClaudeSdkClient:
            client = _ScriptedClaudeSdkClient(
                options, handshake={"commands": [{"name": "inspect", "description": "Inspect"}]}
            )
            clients.append(client)
            return client

        def factory(
            start: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            native_starts.append(start)
            if backend is ConversationBackendKey.claude:
                return ClaudeAgentSdkBackendChildFactory(
                    ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")),
                    client_factory=make_client,
                )
            return HermesAcpBackendChildFactory(
                scripted_acp_agent_launch(control_socket_path=control.socket_path)
            )

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_native")
        spool = FilesystemSpool(tmp_path / "spool")
        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(db_path),
        )

        async def send(command: ChildCommand) -> CommandOutcome:
            outcome = await registry.execute_command(command)
            commands.append((command, outcome))
            return outcome

        remote = RemoteBackendChildFactory(
            lambda start, sink: reference, send, lambda start: snapshot
        )
        system = SqliteProcessConversationSystem(
            store=store,
            message_files=ConversationMessageFiles(db_path),
            backend_child_factories={key: remote for key in ConversationBackendKey},
        )
        recipient = Principal(PrincipalKind.ticket, "t_native")

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
            assert (
                await deliver("direct steer authored bytes", mode=PromptDeliveryMode.steer)
                == PromptDeliveryInjected()
            )
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
                environment = clients[0].options.env
                assert clients[0].options.resume == (cursor if retained else None)
            else:
                report = await account()
                prompt_texts = [write["text"] for write in report["prompt_writes"]] + [
                    write["text"] for write in report["steer_writes"]
                ]
                environment = report["environment"]
                assert report["loaded_from"] == (cursor if retained else None)
            assert len(prompt_texts) == 3
            for prompt, authored in zip(
                prompt_texts,
                (
                    "ordinary authored bytes",
                    "direct steer authored bytes",
                    "held steer authored bytes",
                ),
                strict=True,
            ):
                assert prompt.startswith("[Authenticated Panels reply requirement]\n")
                assert prompt.count(NATIVE_WORKER_CONTEXT) == 1
                assert prompt.index(NATIVE_WORKER_CONTEXT) < prompt.index("Chief:\n" + authored)
                assert prompt.endswith("Chief:\n" + authored)
            assert ("Old persisted Worker role." in prompt_texts[0]) is (not retained)
            assert all("Old persisted Worker role." not in prompt for prompt in prompt_texts[1:])
            assert environment is not None
            generation = Path(environment["PLAN_NATIVE_SKILLS_DIR"])
            assert (generation / "panels/SKILL.md").read_text() == snapshot.sources[0].source_text
            assert environment["PLAN_TICKET_FILES_DIR"] == str(
                tmp_path / "executor/artifacts/tickets/t_native"
            )
            assert native_starts[0].role_materials is not None
            assert native_starts[0].role_materials.role_text == "Old persisted Worker role."
            delivered = [
                command
                for command, _ in commands
                if command.operation in (ChildOperation.write_prompt, ChildOperation.steer)
            ]
            assert [command.arguments["sender_content"] for command in delivered] == [
                encode_value(text_message_content(text))
                for text in (
                    "ordinary authored bytes",
                    "direct steer authored bytes",
                    "held steer authored bytes",
                )
            ]
            if backend is ConversationBackendKey.hermes:
                await control.send(
                    {
                        "command": "emit_available_commands",
                        "commands": [{"name": "inspect", "description": "Inspect"}],
                    }
                )
                native = registry.children[reference.child_id].native
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


@pytest.mark.parametrize(
    ("identity", "recompose", "expected_context"),
    [
        ((("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_native")), True, True),
        ((("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_native")), False, True),
        ((("PLAN_ACTOR", "chief"), ("PLAN_TICKET_ID", "t_native")), True, False),
        ((("PLAN_ACTOR", "worker"),), True, False),
        ((), True, False),
    ],
    ids=["recompose-worker", "reuse-worker", "other-actor", "missing-ticket", "no-identity"],
)
def test_native_context_scope_and_rebind_do_not_duplicate_composed_content(
    tmp_path: Path, identity: tuple[tuple[str, str], ...], recompose: bool, expected_context: bool
) -> None:
    from planner.conversation.backends.contracts import TurnToken
    from planner.conversation.contracts import ConversationStartRequest
    from planner.executor.contracts import CommandDisposition

    async def exercise() -> None:
        db_path = str(tmp_path / "panels.sqlite")
        connection = connect(db_path)
        try:
            create_schema(connection)
        finally:
            connection.close()
        snapshot = CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\ntext")
                for name in ("panels", "panels-worker")
            ),
            ("panels", "panels-worker"),
        )
        commands: list[ChildCommand] = []
        writes = 0

        async def dispatch(command: ChildCommand) -> CommandOutcome:
            nonlocal writes
            commands.append(command)
            if command.operation is ChildOperation.write_prompt:
                writes += 1
                if writes == 1:
                    return CommandOutcome(
                        command.command_id,
                        command.child,
                        CommandDisposition.refused,
                        {"error_type": "NeedsRebind", "error_message": "new child"},
                    )
            return CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.succeeded,
                {"composed_content_delivered": True},
            )

        remote = RemoteBackendChildFactory(
            lambda start, sink: ChildReference("test-incarnation", str(uuid4()), "c"),
            dispatch,
            lambda start: snapshot,
        )
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            message_files=ConversationMessageFiles(db_path),
            backend_child_factories={key: remote for key in ConversationBackendKey},
        )
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    "c",
                    "a-model",
                    role_materials=ConversationRoleMaterials("Worker role", identity),
                    workspace_folder=tmp_path,
                )
            )
            content = text_message_content("sender bytes")
            if recompose:
                assert (
                    await system.send(
                        "c",
                        content,
                        sender_label="Chief",
                        sender=CHIEF_PRINCIPAL,
                        recipient=Principal(PrincipalKind.ticket, "t_native"),
                    )
                    == PromptDeliveryStarted()
                )
            else:
                attempt = await system._deliver_prompt_unsettled(
                    system._conversations["c"],
                    TurnToken("c", 1),
                    content=content,
                    sender_label="Chief",
                    sender_message_count=1,
                    mode=PromptDeliveryMode.queue,
                    model_change=None,
                    reasoning_effort_change=None,
                    automatic_compaction=False,
                    reply_senders=(CHIEF_PRINCIPAL,),
                )
                assert attempt.refusal_reason is None
            written = [
                command for command in commands if command.operation is ChildOperation.write_prompt
            ]
            assert len(written) == 2
            assert written[0].arguments["content"] == written[1].arguments["content"]
            for command in written:
                assert command.arguments["sender_content"] == encode_value(content)
                decoded_content: MessageContent = TypeAdapter(MessageContent).validate_python(
                    command.arguments["content"]
                )
                text = message_content_text(decoded_content)
                assert text.startswith("[Authenticated Panels reply requirement]\n")
                assert text.count(NATIVE_WORKER_CONTEXT) == int(expected_context)
            assert len({command.child.child_id for command in written}) == 2
        finally:
            await system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize(
    "outcome",
    [
        "succeeded",
        "cleanup_failed",
        "second_refusal",
        "kill_cleanup",
        "kill_start",
        "competing_send",
    ],
)
def test_confirmed_refusal_keeps_reservation_until_exact_child_cleanup(
    tmp_path: Path, outcome: str
) -> None:
    """A refused write cannot deadlock on its own reservation or bypass failed cleanup."""
    from planner.conversation.contracts import (
        ConversationStartRequest,
        PromptDeliveryQueued,
        PromptDeliveryRefused,
        PromptDeliverySuppressed,
        PromptDeliveryUncertain,
    )
    from planner.executor.contracts import CommandDisposition
    from planner.executor.store import ExecutorStore

    async def exercise() -> None:
        db_path = str(tmp_path / "panels.sqlite")
        connection = connect(db_path)
        try:
            create_schema(connection)
        finally:
            connection.close()
        commands: list[ChildCommand] = []
        cleanup_entered = asyncio.Event()
        release_cleanup = asyncio.Event()
        replacement_start_entered = asyncio.Event()
        release_start = asyncio.Event()
        executor_store = ExecutorStore(db_path)
        snapshot = CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\ntext")
                for name in ("panels", "panels-worker")
            ),
            ("panels", "panels-worker"),
        )
        writes = 0
        system: SqliteProcessConversationSystem

        async def dispatch(command: ChildCommand) -> CommandOutcome:
            nonlocal writes
            commands.append(command)
            disposition = CommandDisposition.succeeded
            result: dict[str, Any] = {"composed_content_delivered": True}
            if command.operation is ChildOperation.write_prompt:
                writes += 1
                if writes == 1 or outcome == "second_refusal":
                    disposition = CommandDisposition.refused
                    result = {"error_type": "NeedsRebind", "error_message": "model changed"}
            elif command.operation is ChildOperation.stop and not cleanup_entered.is_set():
                state = system._conversations["c"]
                assert state.reserved_turn is not None
                assert not state.reserved_turn.resolved.is_set()
                assert state.child is not None and state.child.reference == command.child
                assert state.child_is_quarantined
                cleanup_entered.set()
                await release_cleanup.wait()
                if outcome == "cleanup_failed":
                    raise RuntimeError("cleanup did not confirm exit")
            elif command.operation is ChildOperation.start and cleanup_entered.is_set():
                replacement_start_entered.set()
                if outcome == "kill_start":
                    await release_start.wait()
            return CommandOutcome(command.command_id, command.child, disposition, result)

        def reference(start: ResolvedConversationStart, sink: Any) -> ChildReference:
            child = ChildReference("test-incarnation", str(uuid4()), "c")
            executor_store.register_child(child, "ticket", "t_native", 1)
            return child

        remote = RemoteBackendChildFactory(
            reference,
            dispatch,
            lambda start: snapshot,
        )
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            message_files=ConversationMessageFiles(db_path),
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
            state = system._conversations["c"]
            reservation = state.reserved_turn
            assert reservation is not None
            assert not delivery.done()
            assert [command.operation for command in commands] == [
                ChildOperation.start,
                ChildOperation.write_prompt,
                ChildOperation.stop,
            ]
            # Cleanup can progress while this exact reservation still owns delivery.
            assert state.reserved_turn is reservation and not reservation.resolved.is_set()
            if outcome == "competing_send":
                competing = await system.send(
                    "c", text_message_content("queued bytes"), sender_label="Other"
                )
                assert isinstance(competing, PromptDeliveryQueued)
                assert state.reserved_turn is reservation and not reservation.resolved.is_set()
            if outcome == "kill_cleanup":
                killed = asyncio.create_task(system.kill("c"))
                await reservation.resolved.wait()
                assert reservation.shutdown_owned
                release_cleanup.set()
                await killed
            else:
                release_cleanup.set()
            if outcome == "kill_start":
                await replacement_start_entered.wait()
                killed = asyncio.create_task(system.kill("c"))
                await reservation.resolved.wait()
                assert reservation.shutdown_owned
                await killed
                release_start.set()
            result = await delivery
            if outcome == "second_refusal":
                assert state.initial_shutdown_task is not None
                assert await state.initial_shutdown_task
            # The detached delivery owner must also finish its post-start check.
            if outcome == "kill_start":
                await asyncio.gather(*tuple(state.settlement_tasks))
            await system.wait_until_quiescent()
            starts = [c for c in commands if c.operation is ChildOperation.start]
            written = [c for c in commands if c.operation is ChildOperation.write_prompt]
            if outcome in {"kill_cleanup", "kill_start"}:
                expected = (
                    PromptDeliveryUncertain
                    if outcome == "kill_cleanup"
                    else PromptDeliverySuppressed
                )
                assert isinstance(result, expected)
                assert len(starts) == (2 if outcome == "kill_start" else 1)
                assert len(written) == 1
                assert state.child is None
                assert state.running_turn is None and state.reserved_turn is None
            elif outcome == "cleanup_failed":
                assert isinstance(result, PromptDeliveryRefused)
                assert len(starts) == len(written) == 1
                assert state.child is not None and state.child_is_quarantined
            else:
                assert len(starts) == len(written) == 2
                assert starts[0].child != starts[1].child
                assert written[0].turn == written[1].turn == reservation.token
                assert written[0].arguments["content"] == written[1].arguments["content"]
                if outcome == "second_refusal":
                    assert isinstance(result, PromptDeliveryRefused)
                    assert state.child is None
                else:
                    assert result == PromptDeliveryStarted()
                    assert state.running_turn is not None
                    assert state.running_turn.token == reservation.token
            assert reservation.resolved.is_set()
        finally:
            release_cleanup.set()
            release_start.set()
            await system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 10))
