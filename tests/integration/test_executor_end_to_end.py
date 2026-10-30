"""Real Mac/VPS dispatch over isolated app HTTPS and WebSockets.

The scripted native adapter never calls a provider. Every event still traverses
native registry, durable spool, framed transport, VPS acceptance and conversation
core. These are process/SQLite gates; no browser is needed for this boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.support.executor_control import paused_outbox
from tests.support.executor_https import HttpsProxy, serve_executor_app

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
    PromptDeliveryFate,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    ToolCallStatus,
    TurnEndedEventPayload,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.executor.configuration import PROTOCOL_VERSION, ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    AskKind,
    AskReachability,
    AskReference,
    ChildReference,
    CommandDisposition,
    ConnectionState,
    ControlMessageWriter,
    EnvelopeKind,
    ExecutorIdentity,
    IpcOperation,
    IpcRequest,
    JsonObject,
    JsonValue,
    OutboxEntry,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor
from planner.executor.publication import PublicationReceiver
from planner.executor.runtime import NativeChildRegistry
from planner.executor.vps import ExecutorEndpoint

pytestmark = pytest.mark.usefixtures("executor_transport_home")


class ScriptedNativeChild:
    def __init__(self) -> None:
        self.sink: BackendEventSink | None = None
        self.turn: TurnToken | None = None
        self.start_count = 0
        self.prompt_count = 0
        self.stop_count = 0
        self.answers: list[tuple[str, str]] = []
        self.user_answers: list[tuple[str, tuple[UserInputAnswer, ...]]] = []
        self.required_input_path: Path | None = None
        self.required_input_contents: bytes | None = None
        self.emit_early_event = False

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
        if self.emit_early_event:
            assert self.sink is not None
            await self.sink.tool_call_started(
                turn_token,
                tool_call_id=f"early-{self.prompt_count}",
                title="Before prompt returns",
                tool_kind="other",
                detail=None,
            )
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
        self.stop_count += 1

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
            if isinstance(application, dict):
                ask = application.get("ask")
                if isinstance(ask, dict) and ask.get("kind") == "user_input":
                    self.cut_user_input_application = False
                    self.cut_submission = envelope.submission_id
        return await super()._accept_envelope(envelope, identity)

    async def _send_reconciliation(
        self, message: WireMessage, identity: ExecutorIdentity, writer: ControlMessageWriter
    ) -> bool:
        sent = await super()._send_reconciliation(message, identity, writer)
        if message.kind is WireMessageKind.file_slot and self.cut_input:
            self.cut_input = False
            self.input_cut_count += 1
            writer.close()
        return sent

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


def public_ask_id(ask: AskReference) -> str:
    """Public views scope native callback IDs to the exact turn and ask kind."""
    return f"{ask.turn.turn_number}:{ask.kind.value}:{ask.ask_id}"


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
    transport_stack: contextlib.AsyncExitStack
    transport_app: Any
    proxy: HttpsProxy

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
        return AskReference(child.reference, self.native.turn, "ask_permission", AskKind.permission)

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

    async def restart_vps_core(self, *, wait_ready: bool = True) -> None:
        await asyncio.wait_for(self.endpoint.close(), 2)
        self.old_systems.append(self.system)
        # A process exit releases the lifecycle lease without a native stop.
        # This in-process crash fixture must model that kernel-owned release.
        self.system._runtime_ownership.close()
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
        self.transport_app.state.executor_endpoint = self.endpoint
        if wait_ready:
            await eventually(lambda: self.endpoint.ready, "VPS core restart did not reconcile")

    async def close(self) -> None:
        try:
            await self._close()
        finally:
            await self.transport_stack.aclose()

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
    system = SqliteProcessConversationSystem(
        store=ConversationStore(endpoint.db_path),
        backend_child_factories={key: endpoint.factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(endpoint.db_path),
    )
    # All transport fixtures are native. Local discovery is never called here.
    system._catalog_endpoint = endpoint
    return system


async def boot(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_factory: Callable[[ResolvedConversationStart, str, ChildReference], BackendChildFactory]
    | None = None,
    backend_key: ConversationBackendKey = ConversationBackendKey.codex,
    model: str = "scripted",
    reasoning_effort: str | None = None,
    emit_early_event: bool = False,
    reconnect_interval: float | None = None,
) -> ExecutorHarness:
    if reconnect_interval is not None:
        # Only fixtures with short reconnect deadlines accelerate this timer.
        monkeypatch.setattr("planner.executor.mac.RECONNECT_INTERVAL_SECONDS", reconnect_interval)
    monkeypatch.setenv("CODEX_HOME", str(root / "native-codex-home"))
    vps_root = root / "vps"
    vps_root.mkdir()
    db_path = str(vps_root / "panels.sqlite")
    connection = connect(db_path)
    try:
        create_schema(connection)
        from planner.projects.data import create_project
        from planner.tickets import data
        from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale

        project = create_project(connection, name="Transport fixture", folder_path=root, now=1)
        ticket = data.create_ticket(
            connection,
            title="Reconnect worker",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            locale=TicketLocale.macos,
            project_id=project.id,
            employee_backend=backend_key.value,
            employee_launch_model=model,
            selection_kind="concrete",
            employee_launch_reasoning_effort=reasoning_effort,
        )
        worker_ticket_id = ticket.id
    finally:
        connection.close()
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
    native = ScriptedNativeChild()
    native.emit_early_event = emit_early_event
    from fastapi import FastAPI

    from planner.core.response_compression import (
        CompressExceptEventStreams,
        event_stream_route_patterns,
    )
    from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
    from planner.executor.app_transport import build_executor_router

    transport_app = FastAPI()
    transport_app.state.executor_id = "test-executor"
    transport_app.state.executor_endpoint = endpoint
    transport_app.include_router(build_executor_router())
    transport_app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@example.com", "https://localhost"),
    )
    transport_app.add_middleware(
        CompressExceptEventStreams,
        event_stream_patterns=event_stream_route_patterns(transport_app.routes),
        minimum_size=1024,
        compresslevel=4,
    )
    transport_stack = contextlib.AsyncExitStack()
    proxy = await transport_stack.enter_async_context(
        serve_executor_app(transport_app, root / "tls")
    )
    mac = MacExecutor(
        paths=ExecutorPaths(root / "mac"),
        revision="a" * 40,
        executor_id="test-executor",
        panels_server_url=proxy.origin,
        session_factory=proxy.session_factory,
    )
    from tests.support.executor_catalog import ScriptedCatalogObserver

    observer = ScriptedCatalogObserver(lambda: mac.identity)
    observer.model = model
    if backend_key is ConversationBackendKey.claude:
        observer.models = (model, "claude-new")
    observer.efforts = ("low", "medium", "high", "xhigh", "max")
    mac._catalog_observer = observer

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
        factory=native_factory or factory,
        message_files=ConversationMessageFiles(root / "mac/unused"),
    )
    task = asyncio.create_task(mac.run())
    harness = ExecutorHarness(
        root, endpoint, mac, system, native, task, [], transport_stack, transport_app, proxy
    )
    try:
        await eventually(
            lambda: endpoint.ready and mac.state == ConnectionState.ready,
            "Initial Mac/VPS handshake did not become ready",
        )
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c_scripted",
                model=model,
                backend_key=backend_key,
                reasoning_effort=reasoning_effort,
                workspace_folder=root,
                workspace_path_policy=WorkspacePathPolicy.native_absolute,
                role_materials=ConversationRoleMaterials(
                    "A scripted test worker.",
                    (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", worker_ticket_id)),
                ),
            )
        )
        with contextlib.closing(connect(db_path)) as connection:
            data.write_ticket_conversation_start(
                connection,
                worker_ticket_id,
                conversation_id="c_scripted",
                backend=backend_key.value,
                model=model,
                reasoning_effort=reasoning_effort,
                now=2,
            )
        initial_delivery = await asyncio.wait_for(
            system.send(
                "c_scripted",
                text_message_content("Start"),
                sender_label="Test",
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.ticket, worker_ticket_id),
            ),
            5,
        )
        if native_factory is None:
            assert native.start_count == 1, initial_delivery
        await eventually(lambda: not mac.spool.pending(), "Initial command outcomes did not drain")
        return harness
    except BaseException:
        await harness.close()
        raise


def test_early_prompt_event_and_idle_publications_do_not_wait_for_retry_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real FIFO/event admission must progress at the shipped five-second interval."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, emit_early_event=True)
        try:
            generation = harness.mac.identity.connection_generation
            assert harness.events("tool_call_started") == 1
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(
                lambda: harness.system._conversations["c_scripted"].running_turn is None,
                "First turn did not finish promptly",
                seconds=2,
            )
            result = await asyncio.wait_for(
                harness.system.send(
                    "c_scripted", text_message_content("Again"), sender_label="Owner"
                ),
                2,
            )
            assert isinstance(result, PromptDeliveryStarted)
            await eventually(
                lambda: harness.events("tool_call_started") == 2,
                "Event before prompt result stalled FIFO replay",
                seconds=2,
            )
            # Let the sender become idle; an event alone must wake it too.
            await asyncio.sleep(0.1)
            assert harness.native.turn is not None
            await harness.native.sink.tool_call_started(
                harness.native.turn,
                tool_call_id="idle",
                title="While idle",
                tool_kind="other",
                detail=None,
            )
            await eventually(
                lambda: harness.events("tool_call_started") == 3,
                "Idle backend event waited for the retry timer",
                seconds=2,
            )
            await asyncio.sleep(0.1)
            child = next(iter(harness.mac.registry.children.values()))
            response = await asyncio.wait_for(
                harness.mac.handle_ipc(
                    IpcRequest(
                        "idle-write",
                        IpcOperation.write,
                        child.reference.child_id,
                        child.capability,
                        {
                            "operation": "ticket.edit",
                            "arguments": {"ticket_id": child.ticket_id, "body": {"recap": "awake"}},
                        },
                    )
                ),
                2,
            )
            assert response.error is None
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                assert (
                    connection.execute(
                        "SELECT recap FROM tickets WHERE id=?", (child.ticket_id,)
                    ).fetchone()[0]
                    == "awake"
                )
            assert harness.mac.identity.connection_generation == generation
            assert harness.mac.state is ConnectionState.ready and harness.endpoint.ready
            assert harness.native.prompt_count == 2
        finally:
            await harness.close()

    asyncio.run(run())


def test_claude_configuration_refusal_rebinds_only_after_exact_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual adapter refusal crosses registry/HTTPS/outcome/core before one admission."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
    from tests.support.conversation_claude_agent_sdk_bench import _ScriptedClaudeSdkClient

    from planner.conversation.backends.claude_agent_sdk import (
        ClaudeAgentSdkBackendChildFactory,
        ClaudeAgentSdkChildLaunch,
    )
    from planner.executor.configuration import NATIVE_WORKER_CONTEXT
    from planner.executor.contracts import ChildOperation
    from planner.runtime.conversation_start import send_to_ticket_conversation
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

    async def run() -> None:
        clients: list[_ScriptedClaudeSdkClient] = []
        cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()

        def client_factory(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
            if clients:
                assert clients[-1].disconnected, "replacement started before exact cleanup"
            client = _ScriptedClaudeSdkClient(options)
            disconnect = client.disconnect

            async def delayed_disconnect() -> None:
                cleanup_entered.set()
                await cleanup_release.wait()
                await disconnect()

            monkeypatch.setattr(client, "disconnect", delayed_disconnect)
            clients.append(client)
            return client

        adapter = ClaudeAgentSdkBackendChildFactory(
            ClaudeAgentSdkChildLaunch(), client_factory=client_factory
        )

        def native_factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            assert resolved.backend_key is ConversationBackendKey.claude
            del capability, child
            return adapter

        harness = await boot(
            tmp_path,
            monkeypatch,
            native_factory=native_factory,
            backend_key=ConversationBackendKey.claude,
            model="claude-old",
            reasoning_effort="low",
        )

        def prompts(client: _ScriptedClaudeSdkClient) -> list[str]:
            return client.prompts + [
                "\n".join(block["text"] for block in message["message"]["content"])
                for message in client.streamed_messages
            ]

        sending = None
        conn = connect(harness.endpoint.db_path)
        try:
            session = clients[0].options.session_id
            assert session is not None

            async def finish_turn() -> None:
                ended = harness.events("turn_ended")
                clients[-1].say(
                    ResultMessage(
                        subtype="success",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id=session,
                    )
                )
                await eventually(
                    lambda: harness.events("turn_ended") == ended + 1,
                    "scripted Claude result did not cross to core",
                )
                await eventually(
                    lambda: not harness.mac.spool.pending(), "result receipt did not drain"
                )

            for index in (1, 2):
                await finish_turn()
                old = clients[-1]
                old_prompt = prompts(old)[-1]
                old_reference = next(iter(harness.mac.registry.children.values())).reference
                cleanup_entered.clear()
                cleanup_release.clear()
                sender_text = f"Keep sender bytes {index}: café\nsecond line"
                sending = asyncio.create_task(
                    send_to_ticket_conversation(
                        harness.system,
                        conn,
                        old.options.env["PLAN_TICKET_ID"],
                        text_message_content(sender_text),
                        conversation_id="c_scripted",
                        sender_label="Chief",
                        sender=CHIEF_PRINCIPAL,
                        recipient=Principal(
                            PrincipalKind.ticket, old.options.env["PLAN_TICKET_ID"]
                        ),
                        runs_under=ConversationStartOverrides(
                            model="claude-new",
                            reasoning_effort="low" if index == 1 else "high",
                        ),
                        now=3 + index,
                    )
                )
                await asyncio.wait_for(cleanup_entered.wait(), 5)
                assert not sending.done()
                assert len(clients) == index
                assert len(prompts(old)) == 1 and prompts(old)[-1] == old_prompt
                assert not old.disconnected
                assert harness.endpoint.store.child_assignment(old_reference) is not None
                cleanup_release.set()
                await asyncio.wait_for(sending, 5)
                await eventually(
                    lambda: not harness.mac.spool.pending(), "rebind outcomes did not drain"
                )
                replacement = clients[-1]
                assert len(clients) == index + 1 and old.disconnected
                assert replacement.options.resume == session
                assert replacement.options.model == "claude-new"
                assert replacement.options.effort == ("low" if index == 1 else "high")
                assert harness.endpoint.store.child_assignment(old_reference) is None
                assert len(prompts(replacement)) == 1
                prompt = prompts(replacement)[0]
                assert prompt.startswith("[Authenticated Panels reply requirement]\n")
                assert prompt.count(NATIVE_WORKER_CONTEXT) == 1
                assert prompt.count(sender_text) == 1
                assert prompt.index("Chief:") < prompt.index(sender_text)

                writes = [
                    command
                    for command in harness.mac.registry._commands.values()
                    if command.operation is ChildOperation.write_prompt
                    and command.arguments.get("sender_label") == "Chief"
                    and command.arguments.get("model_change") == "claude-new"
                    and command.arguments.get("reasoning_effort_change")
                    == ("low" if index == 1 else "high")
                ]
                assert len(writes) == 2
                refused, accepted = [harness.mac.registry.outcomes[c.command_id] for c in writes]
                assert refused.disposition is CommandDisposition.refused
                assert refused.result["error_type"] == "NeedsRebind"
                assert accepted.disposition is CommandDisposition.succeeded
                assert refused.child == old_reference
                assert accepted.child != old_reference
                assert writes[0].arguments["content"] == writes[1].arguments["content"]
                assert (
                    writes[0].arguments["sender_content"] == writes[1].arguments["sender_content"]
                )
                assert all(
                    harness.mac.registry.ask((outcome.command_id,))[0].outcome == outcome
                    for outcome in (refused, accepted)
                )
            assert sum(len(prompts(client)) for client in clients) == 3
        finally:
            cleanup_release.set()
            if sending is not None and not sending.done():
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
            conn.close()
            await harness.close()
            for client in clients:
                await client.disconnect()

    asyncio.run(run())


def test_lifecycle_expectation_crosses_authenticated_native_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authored kind crosses the real lifecycle/TLS boundary without inferred intent."""
    import time

    from planner.conversation.message_content import MessageText
    from planner.conversation.model_resolution.contracts import SelectionKind
    from planner.executor.contracts import ChildOperation
    from planner.runtime.conversation_start import (
        conversation_link_lock,
        send_to_ticket_conversation,
    )
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
    from planner.tickets import data
    from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale
    from planner.tickets.validation.data import read_launch_selection

    class CapturedNativeChild(ScriptedNativeChild):
        def __init__(self) -> None:
            super().__init__()
            self.delivered_text: list[str] = []

        async def write_prompt(
            self, turn_token: TurnToken, content: MessageContent, **arguments: object
        ) -> BackendPromptAccepted:
            self.delivered_text.append(
                "\n".join(piece.text for piece in content if isinstance(piece, MessageText))
            )
            return await super().write_prompt(turn_token, content, **arguments)

    async def run() -> None:
        started = time.monotonic()
        harness = await boot(tmp_path, monkeypatch)
        conn = connect(harness.endpoint.db_path)
        try:
            first = next(iter(harness.mac.registry.children.values()))
            second = CapturedNativeChild()

            def factory(
                resolved: ResolvedConversationStart, capability: str, child: ChildReference
            ) -> BackendChildFactory:
                del resolved, capability, child

                def create(**arguments: object) -> BackendChild:
                    second.sink = cast(BackendEventSink, arguments["event_sink"])
                    return cast(BackendChild, second)

                return cast(BackendChildFactory, create)

            harness.mac.registry.factory = factory
            project_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
            ticket = data.create_ticket(
                conn,
                title="Combined admission",
                worker_type="coding",
                principal=OWNER_PRINCIPAL,
                now=int(time.time()),
                title_max_chars=TITLE_MAX_CHARS,
                locale=TicketLocale.macos,
                project_id=project_id,
                employee_backend="codex",
                employee_launch_model="scripted",
                selection_kind="legacy_unclassified",
            )
            from planner.runtime.conversation_start import resolve_ticket_launch_selection

            _, binding = resolve_ticket_launch_selection(conn, ticket)
            from tests.support.executor_catalog import ScriptedCatalogObserver

            observer = harness.mac._catalog_observer
            assert isinstance(observer, ScriptedCatalogObserver)
            snapshots = await harness.system.destination_backend_snapshot(
                ConversationBackendKey.codex,
                binding,
                "macos",
                refresh=True,
            )
            assert snapshots.available_models and observer.calls > 0
            observer.calls = 0
            observer.block = True
            refreshed_at = time.monotonic()
            original = read_launch_selection(conn, ticket.id)
            assert original.selection_kind is SelectionKind.legacy_unclassified
            exact_text = "Exact lifecycle selection: café\nsecond line"
            async with conversation_link_lock(f"ticket:{ticket.id}"):
                delivery = await send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content(exact_text),
                    conversation_id=None,
                    created_conversation_id="c_combined_admission",
                    runs_under=ConversationStartOverrides(
                        model="scripted", selection_kind=SelectionKind.concrete
                    ),
                    sender_label="Test",
                    sender=CHIEF_PRINCIPAL,
                    recipient=Principal(PrincipalKind.ticket, ticket.id),
                    now=int(time.time()),
                )
            fate = delivery.fate
            conversation_id = delivery.conversation_id
            assert conversation_id == "c_combined_admission"
            assert second.start_count == second.prompt_count == 1, fate
            assert len(second.delivered_text) == 1
            assert exact_text in second.delivered_text[0]
            selected = read_launch_selection(conn, ticket.id)
            assert selected.selection_kind is SelectionKind.concrete
            assert selected.submitted_token == original.submitted_token == "scripted"
            assert selected.backend_key is ConversationBackendKey.codex
            commands = [
                command
                for command in harness.mac.registry._commands.values()
                if command.child.conversation_id == conversation_id
            ]
            assert [command.operation for command in commands] == [
                ChildOperation.start,
                ChildOperation.write_prompt,
            ]
            for command in commands:
                expectation = command.arguments["admission_expectation"]
                assert isinstance(expectation, dict)
                assert expectation["conversation_id"] == conversation_id
                selection = expectation["selection"]
                assert isinstance(selection, dict)
                assert selection["selection_kind"] == "concrete"
                assert selection["submitted_token"] == "scripted"
                assert selection["backend_key"] == "codex"
                assert expectation["resolved_model_id"] is None
                assert expectation["launch_token"] == "scripted"
                assert expectation["child"] == {
                    "executor_incarnation": command.child.executor_incarnation,
                    "child_id": command.child.child_id,
                    "conversation_id": conversation_id,
                }
                assert expectation["turn"] == (
                    None
                    if command.turn is None
                    else {
                        "conversation_id": conversation_id,
                        "turn_number": command.turn.turn_number,
                    }
                )
            first_delivery_at = time.monotonic()
            assert observer.calls == 0
            assert second.sink is not None and second.turn is not None
            await second.sink.turn_ended(
                second.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(
                lambda: harness.system._conversations[conversation_id].phase.value == "idle",
                "First workflow turn did not settle",
            )
            async with conversation_link_lock(f"ticket:{ticket.id}"):
                changed = await send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("Second exact choice"),
                    conversation_id=conversation_id,
                    runs_under=ConversationStartOverrides(
                        model="gpt-6.1-terra",
                        reasoning_effort="future-effort",
                        reasoning_effort_is_set=True,
                    ),
                    sender_label="Test",
                    sender=CHIEF_PRINCIPAL,
                    recipient=Principal(PrincipalKind.ticket, ticket.id),
                    now=int(time.time()),
                )
            assert second.prompt_count == 2, changed
            assert "Second exact choice" in second.delivered_text[1]
            assert observer.calls == 0
            writes = [
                command
                for command in harness.mac.registry._commands.values()
                if command.child.conversation_id == conversation_id
                and command.operation is ChildOperation.write_prompt
            ]
            assert writes[-1].arguments["model_change"] == "gpt-6.1-terra"
            assert writes[-1].arguments["reasoning_effort_change"] == "future-effort"
            print(
                f"synthetic native refresh-to-first-send: {first_delivery_at - refreshed_at:.3f}s; "
                f"change-to-second-send: {time.monotonic() - first_delivery_at:.3f}s"
            )
            assert harness.mac.registry.children[first.reference.child_id] is first
            assert harness.native.stop_count == 0
            assert harness.native.sink is not None and harness.native.turn is not None
            before = harness.events("tool_call_finished")
            await harness.native.sink.tool_call_finished(
                harness.native.turn,
                tool_call_id="other-child-after-admission",
                tool_call_status=ToolCallStatus.completed,
                detail="Independent child remains active.",
            )
            await eventually(
                lambda: harness.events("tool_call_finished") == before + 1,
                "Unrelated child lost its authenticated event path",
            )
            print(f"lifecycle admission TLS scenario elapsed: {time.monotonic() - started:.3f}s")
        finally:
            conn.close()
            await harness.close()

    asyncio.run(run())


def test_live_events_bypass_storage_and_completed_message_survives_link_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation import system as conversation_module
    from planner.conversation.events import (
        AgentMessageDeltaFrame,
        ModelThinkingFrame,
        ToolCallProgressFrame,
    )
    from planner.conversation.live_tail import ConversationLiveTail

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        resume = None
        try:
            tail = ConversationLiveTail()
            monkeypatch.setattr(harness.system, "_live_tail", tail)
            sink, turn = harness.native.sink, harness.native.turn
            assert sink is not None and turn is not None
            completed = []
            complete = conversation_module.completed_backend_text_event

            def observe_completion(content: MessageContent) -> None:
                completed.append(content)
                complete(content)

            monkeypatch.setattr(
                conversation_module, "completed_backend_text_event", observe_completion
            )
            minted = []
            mint = harness.mac.spool.add

            def observe_mint(*args: Any, **arguments: Any) -> QueuedEnvelope:
                envelope = mint(*args, **arguments)
                minted.append(envelope)
                return envelope

            monkeypatch.setattr(harness.mac.spool, "add", observe_mint)
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                before = connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[
                    0
                ]
                with tail.subscribe("c_scripted") as watcher:
                    await sink.agent_message_delta(turn, "Live text")
                    await sink.model_thinking_happened(turn)
                    await sink.tool_call_progress(turn, tool_call_id="tool", detail="Working")
                    assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                        text_delta="Live text"
                    )
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ModelThinkingFrame()
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ToolCallProgressFrame(
                        tool_call_id="tool", detail="Working"
                    )
                    assert not minted and not harness.mac.spool.pending()
                    assert (
                        connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
                        == before
                    )
                    resume = await hold_reconnection(harness, monkeypatch)
                    await eventually(lambda: harness.mac._writer is None, "Mac link stayed open")
                    await sink.agent_message_delta(turn, "Lost text")
                    await sink.model_thinking_happened(turn)
                    await sink.tool_call_progress(turn, tool_call_id="tool", detail="Lost progress")
                    assert not minted and not harness.mac.spool.pending()
                    await sink.agent_message_completed(turn, text_message_content("Full reply"))
                    pending = harness.mac.spool.pending()
                    assert len(pending) == 1
                    resume.set()
                    await eventually(
                        lambda: not harness.mac.spool.pending(), "Completed reply did not replay"
                    )
                    assert completed == [text_message_content("Full reply")]
                    # Nothing shown while disconnected is replayed to live watchers.
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(watcher.next_item(), 0.05)
                    assert (
                        harness.endpoint.store.lookup_receipt(
                            pending[0].sender_id, pending[0].submission_id
                        )
                        is not None
                    )
        finally:
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


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
        finally:
            await harness.close()

    asyncio.run(run())


def test_permanent_refusals_receive_durable_receipts_and_release_conversation_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.executor.contracts import CliWrite
        from planner.executor.mac import as_object

        harness = await boot(tmp_path, monkeypatch)
        try:
            child = next(iter(harness.mac.registry.children.values())).reference
            native_child = next(iter(harness.mac.registry.children.values()))
            assert native_child.ticket_id is not None
            artifact = harness.mac.paths.artifacts / native_child.ticket_id / "queued.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("a refused write does not wait for these bytes")
            async with paused_outbox(harness.mac.spool):
                malformed = harness.mac.spool.add(
                    OutboxEntry(
                        conversation_id=child.conversation_id,
                        kind=EnvelopeKind.child_stopped,
                        payload={"child": as_object(child), "disposition": "unsupported"},
                    )
                )
                unassigned = harness.mac.spool.add(
                    OutboxEntry(
                        conversation_id=child.conversation_id,
                        kind=EnvelopeKind.cli_write,
                        payload=as_object(
                            CliWrite(
                                "ticket.update", {}, replace(child, child_id="unassigned"), None
                            )
                        ),
                    ),
                    ticket_id=native_child.ticket_id,
                )
                assert unassigned.file_dependencies
                assigned_refusal = harness.mac.spool.add(
                    OutboxEntry(
                        conversation_id=child.conversation_id,
                        kind=EnvelopeKind.cli_write,
                        payload=as_object(CliWrite("ticket.unsupported", {}, child, None)),
                    )
                )
                await harness.native.raise_ask()
                following = harness.mac.spool.pending()[-1]
                assert following.sequence == assigned_refusal.sequence + 1

            await eventually(
                lambda: not harness.mac.spool.pending(),
                "Permanent refusal stranded the next item in the conversation",
            )
            # Only the last position remains on the VPS. The CLI refusal is retained
            # on the Mac for retry; obsolete sequences receive a plain acknowledgment.
            refused = harness.mac.spool.lookup(unassigned.submission_id)
            assert refused is not None and refused.receipt is not None
            assert refused.receipt.disposition is ReceiptDisposition.refused
            error = refused.receipt.result["error"]
            assert isinstance(error, dict) and error["code"] == "agent_forbidden"
            assigned = harness.mac.spool.lookup(assigned_refusal.submission_id)
            assert assigned is not None and assigned.receipt is not None
            assert assigned.receipt.disposition is ReceiptDisposition.refused
            for envelope in (malformed, unassigned, assigned_refusal):
                stale = await harness.endpoint.accept_envelope(envelope, harness.mac.identity)
                assert stale.disposition is ReceiptDisposition.accepted and stale.result == {}
            connection = connect(harness.endpoint.db_path)
            try:
                rows = connection.execute(
                    "SELECT payload FROM conversation_events WHERE conversation_id=? "
                    "AND kind='tool_call_finished'",
                    (child.conversation_id,),
                ).fetchall()
                refusals = [json.loads(row[0]) for row in rows if "outbox_refusal_" in row[0]]
                assert len(refusals) == 1
                assert refusals[0]["tool_call_status"] == "failed"
                assert "validation" in refusals[0]["detail"]
            finally:
                connection.close()
            accepted = harness.endpoint.store.lookup_receipt(
                following.sender_id, following.submission_id
            )
            assert accepted is not None and accepted.disposition is ReceiptDisposition.accepted
            assert harness.events("permission_asked") == 1
            assert harness.endpoint.store.cleanup_evidence_uncertain(child)
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("receipt_failure", ["disconnect", "unexpected", "silent"])
def test_commit_before_ack_replay_returns_first_receipt_and_one_domain_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_failure: str,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            if receipt_failure != "disconnect":
                original_send = harness.endpoint._send

                async def fail_receipt(message: WireMessage) -> None:
                    if (
                        message.kind is WireMessageKind.receipt
                        and message.payload.get("submission_id") == harness.endpoint.cut_submission
                    ):
                        harness.endpoint.cut_submission = None
                        harness.endpoint.cut_count += 1
                        if receipt_failure == "unexpected":
                            raise RuntimeError("receipt handler failed after durable acceptance")
                        return
                    await original_send(message)

                monkeypatch.setattr(harness.endpoint, "_send", fail_receipt)
                if receipt_failure == "silent":
                    monkeypatch.setattr("planner.executor.mac.REQUEST_TIMEOUT_SECONDS", 0.2)
                    writer = harness.mac._writer
                    assert writer is not None
                    close = writer.close

                    def restore_budget_and_close() -> None:
                        # Accelerate only the missing receipt, not the fresh
                        # generation's real SSH and reconciliation setup.
                        monkeypatch.setattr("planner.executor.mac.REQUEST_TIMEOUT_SECONDS", 30.0)
                        close()

                    monkeypatch.setattr(writer, "close", restore_budget_and_close)
            async with paused_outbox(harness.mac.spool):
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
            first_receipt = harness.endpoint.store.lookup_receipt(
                pending[0].sender_id, pending[0].submission_id
            )
            assert first_receipt is not None
            assert first_receipt.payload_sha256 == pending[0].payload_sha256
            await eventually(
                lambda: not harness.mac.spool.pending() and harness.endpoint.ready,
                "Committed entry did not replay and acknowledge",
            )
            assert harness.events("permission_asked") == 1
            assert harness.native.answers == []
            receipt = harness.endpoint.store.lookup_receipt(
                pending[0].sender_id, pending[0].submission_id
            )
            assert receipt == first_receipt

            async def duplicate_effect(*args: object, **kwargs: object) -> None:
                raise AssertionError("duplicate submission invoked backend event writer")

            monkeypatch.setattr(
                harness.endpoint.runtime.system, "receive_remote_backend_event", duplicate_effect
            )
            assert (
                await harness.endpoint.accept_envelope(pending[0], harness.mac.identity)
                == first_receipt
            )
            assert harness.native.start_count == harness.native.prompt_count == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_core_recreation_stops_old_child_before_fresh_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await asyncio.wait_for(harness.endpoint.close(), 2)
            await harness.native.raise_ask()
            await harness.restart_vps_core()
            await eventually(lambda: harness.native.stop_count == 1, "old child was not stopped")
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: not state.child_is_quarantined, "stop proof did not settle")
            assert not await harness.system.is_running("c_scripted")
            assert not await harness.system.has_pending_permission_ask("c_scripted")
            assert harness.native.start_count == harness.native.prompt_count == 1
            delivery = await harness.system.send(
                "c_scripted", text_message_content("Fresh"), sender_label="Test"
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            assert harness.native.start_count == harness.native.prompt_count == 2
        finally:
            await harness.close()

    asyncio.run(run())


def test_mac_replacement_interrupts_old_run_and_allows_fresh_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryUncertain
    from planner.executor.contracts import ChildCommand, ChildOperation, CommandOutcome

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        release = asyncio.Event()
        late_delivery: asyncio.Task[PromptDeliveryFate] | None = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            ask = harness.ask
            old_turn = harness.native.turn
            old_mac = harness.mac
            reached = asyncio.Event()
            send_command = harness.endpoint.send_command

            async def hold_command(command: ChildCommand) -> CommandOutcome:
                if command.operation is ChildOperation.steer:
                    reached.set()
                    await release.wait()
                return await send_command(command)

            monkeypatch.setattr(harness.endpoint, "send_command", hold_command)
            late_delivery = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    text_message_content("Unknown"),
                    sender_label="Test",
                    mode=PromptDeliveryMode.steer,
                )
            )
            await asyncio.wait_for(reached.wait(), 3)
            old_mac._stop.set()
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await asyncio.wait_for(harness.task, 3)

            # Model service death with a fresh registry and incarnation. This
            # does not prove launchd terminates real provider process groups.
            replacement = MacExecutor(
                paths=old_mac.paths,
                revision=old_mac.identity.application_revision,
                executor_id=old_mac.executor_id,
                panels_server_url=harness.proxy.origin,
                session_factory=harness.proxy.session_factory,
            )
            replacement._catalog_observer = ScriptedCatalogObserver(lambda: replacement.identity)
            replacement.registry = NativeChildRegistry(
                paths=replacement.paths,
                incarnation=replacement.identity.executor_incarnation,
                spool=replacement.spool,
                factory=old_mac.registry.factory,
                message_files=old_mac.registry.message_files,
            )
            harness.mac = replacement
            harness.task = asyncio.create_task(replacement.run())
            await eventually(
                lambda: harness.endpoint.identity == replacement.identity,
                "Replacement hello did not reach the VPS",
            )
            release.set()
            assert isinstance(await asyncio.wait_for(late_delivery, 3), PromptDeliveryUncertain)
            await eventually(
                lambda: harness.endpoint.ready and replacement.state is ConnectionState.ready,
                "Replacement Mac did not reconcile",
            )
            assert not await harness.system.is_running("c_scripted")
            assert harness.events("turn_ended") == 1
            endings = [
                event.payload
                for event in await harness.system._store.read_events_after("c_scripted", 0)
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert len(endings) == 1
            assert endings[0].ending is ConversationTurnEnding.interrupted
            assert harness.endpoint.store.has_closed_child_assignment(ask.child)
            evidence = harness.endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.reachability is AskReachability.child_gone
            state = harness.system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            assert state.executor_child_reference is None
            assert harness.native.start_count == harness.native.prompt_count == 1
            delivery = await harness.system.send(
                "c_scripted", text_message_content("Fresh"), sender_label="Test"
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            assert harness.native.start_count == harness.native.prompt_count == 2
            assert harness.native.turn != old_turn
            fresh_child = next(iter(replacement.registry.children.values())).reference
            assert fresh_child.executor_incarnation != ask.child.executor_incarnation
            assert harness.events("prompt_delivery_uncertain") == 1
        finally:
            release.set()
            if late_delivery is not None:
                await asyncio.gather(late_delivery, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_binary_artifact_is_complete_before_dependent_backend_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            ticket_id = next(iter(harness.mac.registry.children.values())).ticket_id
            assert ticket_id is not None
            artifact = harness.mac.paths.artifacts / ticket_id / "binary.dat"
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
            assert (
                tmp_path / "vps/files/tickets" / ticket_id / "binary.dat"
            ).read_bytes() == contents
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
            await harness.endpoint.close()
            await sink.turn_ended(
                turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.endpoint.ready, "link did not reconnect")
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
    from planner.executor import vps
    from planner.executor.contracts import ChildCommand
    from planner.executor.transfers import GenerationTransfers

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
            generation = harness.mac.identity.connection_generation
            monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.15)
            emitted: list[ChildCommand] = []
            emit = harness.endpoint._emit_command

            async def record_command(command: ChildCommand, *args: Any) -> bool:
                result = await emit(command, *args)
                if result:
                    emitted.append(command)
                return result

            monkeypatch.setattr(harness.endpoint, "_emit_command", record_command)
            first_chunk = asyncio.Event()
            claim_download = GenerationTransfers.claim_download

            def paced_download(service: GenerationTransfers, transfer_id: str) -> Any:
                async def chunks() -> Any:
                    async for chunk in claim_download(service, transfer_id):
                        await asyncio.sleep(0.08)
                        first_chunk.set()
                        yield chunk

                return chunks()

            monkeypatch.setattr(GenerationTransfers, "claim_download", paced_download)
            contents = bytes(range(256)) * 800
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
            started = asyncio.get_running_loop().time()
            sending = asyncio.create_task(
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
                )
            )
            await asyncio.wait_for(first_chunk.wait(), 2)
            control = await harness.endpoint._request(
                WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": 1000}}
            )
            assert control.kind is WireMessageKind.health
            result = await asyncio.wait_for(sending, 5)
            assert harness.native.prompt_count == 2, repr(result)
            assert asyncio.get_running_loop().time() - started > 0.15
            assert harness.mac.identity.connection_generation == generation
            assert native_path.read_bytes() == contents
            assert len(emitted) == 1
            assert harness.mac.registry._commands[emitted[0].command_id] == emitted[0]
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
            assert await harness.endpoint.answer_permission(
                "c_scripted", public_ask_id(harness.ask), "allow", executor_ask=harness.ask
            )
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
                harness.endpoint.ask_status("c_scripted", public_ask_id(harness.ask))[
                    "answer_available"
                ]
                is True
            )
            assert await harness.endpoint.answer_permission(
                "c_scripted", public_ask_id(harness.ask), "deny", executor_ask=harness.ask
            )
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


def test_input_transfer_drop_refuses_stale_context_then_explicit_send_delivers_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import TypeAdapter

    from planner.conversation.admission.contracts import AdmissionExpectation
    from planner.conversation.message_content import MessageFile, MessageText
    from planner.executor.contracts import ChildCommand, CommandOutcome

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
            content = (
                MessageText("Inspect"),
                MessageFile(
                    kept.stored_file_id, "application/octet-stream", "input.bin", len(contents)
                ),
            )
            commands: list[ChildCommand] = []
            send_command = harness.endpoint.send_command

            async def observe_command(command: ChildCommand) -> CommandOutcome:
                commands.append(command)
                return await send_command(command)

            monkeypatch.setattr(harness.endpoint, "send_command", observe_command)
            generation = harness.mac.identity.connection_generation
            harness.endpoint.cut_input = True
            fate = await asyncio.wait_for(
                harness.system.send("c_scripted", content, sender_label="Test"), 8
            )
            assert isinstance(fate, PromptDeliveryRefused), repr(fate)
            assert fate.model_validation is not None
            assert fate.model_validation["reason"] == "context_changed"
            assert fate.model_validation["evidence"] is None
            refused = TypeAdapter(AdmissionExpectation).validate_python(
                commands[0].arguments["admission_expectation"]
            )
            assert refused.executor_identity is not None
            assert refused.executor_identity.connection_generation == generation
            assert harness.endpoint.input_cut_count == 1
            assert harness.mac.identity.connection_generation > generation
            assert harness.native.prompt_count == 1
            # The first operation is definitively refused. This separate user send
            # must obtain fresh admission after reconnect, without replay of its authority.
            submitted = await asyncio.wait_for(
                harness.system.send("c_scripted", content, sender_label="Test"), 8
            )
            assert isinstance(submitted, PromptDeliveryStarted), repr(submitted)
            state = await harness.system._conversation_state("c_scripted")
            assert state is not None and state.running_turn is not None
            fresh = state.running_turn.launch_validation
            assert fresh is not None and fresh.evidence is None
            admitted = TypeAdapter(AdmissionExpectation).validate_python(
                commands[-1].arguments["admission_expectation"]
            )
            assert admitted.executor_identity == harness.mac.identity
            assert admitted.attempt_id != refused.attempt_id
            assert harness.native.prompt_count == 2
            assert native_path.read_bytes() == contents
        finally:
            await harness.close()

    asyncio.run(run())


def test_kill_stalled_initial_input_fences_reconnect_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile, MessageText
    from planner.executor.contracts import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        release_input = asyncio.Event()
        release_final_event = asyncio.Event()
        killing: asyncio.Task[None] | None = None
        sending: asyncio.Task[PromptDeliveryFate] | None = None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.turn_ended(
                harness.native.turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "first turn did not end")
            original_child = next(iter(harness.mac.registry.children.values())).reference
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.runtime.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            input_ready = asyncio.Event()
            transfer_attempts = 0
            target_command: str | None = None
            transfer = harness.endpoint._transfer_command_inputs
            transfer_locked = harness.endpoint._transfer_command_inputs_locked

            async def observe_transfer(command: ChildCommand) -> None:
                nonlocal transfer_attempts
                if command.operation is ChildOperation.write_prompt:
                    transfer_attempts += 1
                await transfer(command)

            async def stall_input(command: ChildCommand) -> None:
                nonlocal target_command
                await transfer_locked(command)
                if command.operation is ChildOperation.write_prompt and target_command is None:
                    target_command = command.command_id
                    input_ready.set()
                    await release_input.wait()

            monkeypatch.setattr(harness.endpoint, "_transfer_command_inputs", observe_transfer)
            monkeypatch.setattr(harness.endpoint, "_transfer_command_inputs_locked", stall_input)
            sending = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageText("Cancel this initial delivery"),
                        MessageFile(
                            kept.stored_file_id,
                            "application/octet-stream",
                            "input.bin",
                            len(contents),
                        ),
                    ),
                    sender_label="Test",
                )
            )
            await asyncio.wait_for(input_ready.wait(), 5)
            generation = harness.mac.identity.connection_generation
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and transfer_attempts >= 2
                ),
                "reconnect did not copy the pending initial command",
            )

            final_event_waiting = asyncio.Event()
            stop_hint_seen = asyncio.Event()
            native_stop = harness.native.stop
            accept_envelope = harness.endpoint.accept_envelope
            resolve_command = harness.endpoint._resolve_command

            async def stop_with_final_event() -> None:
                assert harness.native.sink is not None
                await harness.native.sink.vendor_session_cursor_rebound("retirement-final-cursor")
                await native_stop()

            async def withhold_final_event(
                envelope: QueuedEnvelope, identity: ExecutorIdentity
            ) -> AcceptanceReceipt:
                if (
                    envelope.kind is EnvelopeKind.backend_event
                    and envelope.payload.get("method") == "vendor_session_cursor_rebound"
                    and envelope.payload.get("arguments")
                    == {"vendor_session_cursor": "retirement-final-cursor"}
                ):
                    final_event_waiting.set()
                    await release_final_event.wait()
                return await accept_envelope(envelope, identity)

            async def observe_stop_hint(outcome: Any, *, stop_proven: bool = False) -> None:
                pending = harness.endpoint.pending_commands.get(outcome.command_id)
                if pending is not None and pending[0].operation is ChildOperation.stop:
                    stop_hint_seen.set()
                await resolve_command(outcome, stop_proven=stop_proven)

            monkeypatch.setattr(harness.native, "stop", stop_with_final_event)
            monkeypatch.setattr(harness.endpoint, "accept_envelope", withhold_final_event)
            monkeypatch.setattr(harness.endpoint, "_resolve_command", observe_stop_hint)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            fate = await asyncio.wait_for(sending, 2)
            await asyncio.wait_for(final_event_waiting.wait(), 5)
            cleanup_task = harness.system._conversations["c_scripted"].initial_shutdown_task
            assert cleanup_task is not None and not cleanup_task.done()
            await asyncio.wait_for(stop_hint_seen.wait(), 5)
            pending_stops = [
                (command, future)
                for command, future in harness.endpoint.pending_commands.values()
                if command.operation is ChildOperation.stop
            ]
            assert pending_stops and all(not future.done() for _, future in pending_stops)
            assert harness.endpoint.store.child_assignment(original_child) is not None
            assert not cleanup_task.done()
            release_final_event.set()
            await asyncio.wait_for(cleanup_task, 5)
            await killing
            assert isinstance(fate, PromptDeliverySuppressed)
            release_input.set()
            await eventually(
                lambda: harness.native.stop_count == 1 and harness.endpoint.ready,
                "the original child did not stop after cancellation",
            )
            await eventually(
                lambda: not harness.mac.spool.pending() and not harness.endpoint.pending_commands,
                "canceled delivery or stop receipts remained pending",
            )
            assert harness.native.start_count == 1
            assert harness.native.prompt_count == 1
            assert original_child.child_id not in harness.mac.registry.children
            assert original_child.child_id not in harness.endpoint.children
            assert original_child.child_id not in harness.endpoint.sinks
            assert harness.events("prompt_discarded") == 1
            assert harness.events("prompt_delivery_uncertain") == 0
            connection = connect(harness.endpoint.db_path)
            try:
                prompt_fact = connection.execute(
                    "SELECT status FROM conversation_lifecycle_facts "
                    "WHERE conversation_id=? AND operation='prompt' ORDER BY turn_number DESC",
                    ("c_scripted",),
                ).fetchone()
                assert prompt_fact is not None and prompt_fact[0] == "suppressed"
                assert (
                    connection.execute(
                        "SELECT 1 FROM executor_command_outcomes WHERE command_id=?",
                        (target_command,),
                    ).fetchone()
                    is None
                )
                assert (
                    connection.execute(
                        "SELECT closed_at_ms FROM executor_child_assignments "
                        "WHERE child_id=? AND conversation_id=?",
                        (original_child.child_id, "c_scripted"),
                    ).fetchone()[0]
                    is not None
                )
            finally:
                connection.close()
        finally:
            release_input.set()
            release_final_event.set()
            if killing is not None and not killing.done():
                killing.cancel()
                await asyncio.gather(killing, return_exceptions=True)
            if sending is not None and not sending.done():
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
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
                    child,
                    TurnToken("c_scripted", index + 2),
                    f"historical_{index}_" + "x" * 2048,
                    AskKind.permission,
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
                    "c_scripted",
                    public_ask_id(harness.input_ask),
                    answers,
                    executor_ask=harness.input_ask,
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
                "c_scripted",
                public_ask_id(harness.input_ask),
                INPUT_ANSWERS,
                executor_ask=harness.input_ask,
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
                "c_scripted",
                public_ask_id(harness.input_ask),
                second_answers,
                executor_ask=harness.input_ask,
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
                "c_scripted",
                public_ask_id(harness.input_ask),
                INPUT_ANSWERS,
                executor_ask=harness.input_ask,
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
                "c_scripted",
                public_ask_id(harness.input_ask),
                replacement,
                executor_ask=harness.input_ask,
            )
            assert harness.events("user_input_answered") == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_refuses_previous_protocol_before_replay_without_fencing_current_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from planner.executor.app_transport import ControlWebSocket, ExecutorAppClient
    from planner.executor.codec import encode_value
    from planner.executor.contracts import ExecutorHealth, ExecutorIdentity

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            identity = harness.endpoint.identity
            assert identity is not None
            connection = connect(harness.endpoint.db_path)
            before = connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            ws_context = client.websocket("/api/executor/connect", 1024 * 1024)
            peer = await ws_context.__aenter__()
            stream = ControlWebSocket(peer)
            old = ExecutorHealth(
                ExecutorIdentity(PROTOCOL_VERSION - 1, "a" * 40, 2, str(uuid4()), 1),
                42,
                ConnectionState.disconnected,
                False,
                1000,
                None,
            )
            payload = encode_value(old)
            assert isinstance(payload, dict)
            await stream.send(WireMessage(WireMessageKind.hello, "old_protocol", payload))
            assert await asyncio.wait_for(peer.receive_bytes(1024), 1) == b""
            await ws_context.__aexit__(None, None, None)
            await client.close()
            assert harness.endpoint.identity == identity and harness.endpoint.ready
            assert (
                connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
                == before
            )
            connection.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid_ack", ["empty", "mismatched", "unknown_slot", "traversal", "manifest", "wrong_kind"]
)
def test_artifact_descriptor_must_validate_before_publication_and_backend_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_ack: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            reject = True
            rejected = asyncio.Event()
            completed = 0
            send = harness.endpoint._send
            replay = harness.mac.spool._send_one

            async def corrupt_ack(message: WireMessage) -> None:
                if reject and message.kind is WireMessageKind.file_slot:
                    payload = {} if invalid_ack == "empty" else dict(message.payload)
                    if invalid_ack == "mismatched":
                        payload["direction"] = "download"
                    elif invalid_ack == "unknown_slot":
                        payload["transfer_id"] = "x" * 32
                    elif invalid_ack == "traversal":
                        payload["transfer_id"] = "../escape"
                    elif invalid_ack == "manifest":
                        payload["manifest"] = {}
                    message = WireMessage(
                        (
                            WireMessageKind.file_accepted
                            if invalid_ack == "wrong_kind"
                            else message.kind
                        ),
                        message.request_id,
                        payload,
                    )
                await send(message)

            async def observe_rejection(link: Any, envelope: QueuedEnvelope) -> None:
                try:
                    await replay(link, envelope)
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
            monkeypatch.setattr(harness.mac.spool, "_send_one", observe_rejection)
            monkeypatch.setattr(harness.mac, "_request", count_completions)
            ticket_id = next(iter(harness.mac.registry.children.values())).ticket_id
            assert ticket_id is not None
            artifact = harness.mac.paths.artifacts / ticket_id / "typed-ack.bin"
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
            target = tmp_path / "vps/files/tickets" / ticket_id / "typed-ack.bin"
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
    from planner.executor.contracts import ChildCommand, ChildOperation, RemoteInputTransferFailed

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        caller = None
        try:
            reject = True
            rejected = asyncio.Event()
            commands = 0
            assert harness.mac._writer is not None
            send = harness.mac._writer.send
            emit_command = harness.endpoint._emit_command

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

            async def count_commands(command: ChildCommand) -> bool:
                nonlocal commands
                emitted = await emit_command(command)
                if emitted:
                    commands += 1
                return emitted

            monkeypatch.setattr(harness.mac._writer, "send", corrupt_ack)
            monkeypatch.setattr(harness.endpoint, "_emit_command", count_commands)
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
            with pytest.raises(RemoteInputTransferFailed):
                await asyncio.wait_for(caller, 8)
            assert harness.mac.identity.connection_generation == generation
            assert commands == 0 and harness.native.prompt_count == 1
            assert command.command_id not in harness.endpoint.pending_commands
            assert target.read_bytes() == contents
            assert (
                await harness.endpoint.runtime.message_files.read("c_scripted", kept.stored_file_id)
                == contents
            )
            control = await harness.endpoint._request(
                WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": 1000}}
            )
            assert control.kind is WireMessageKind.health

        finally:
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


async def hold_reconnection(
    harness: ExecutorHarness, monkeypatch: pytest.MonkeyPatch
) -> asyncio.Event:
    resume = asyncio.Event()
    connected = harness.mac._connected

    async def paused() -> None:
        await resume.wait()
        await connected()

    monkeypatch.setattr(harness.mac, "_connected", paused)
    assert harness.endpoint.writer is not None
    harness.endpoint.writer.close()
    await eventually(lambda: not harness.endpoint.ready, "link did not disconnect")
    return resume


async def post_owner_answer(harness: ExecutorHarness, ask: AskReference) -> dict[str, bool]:
    import httpx
    from fastapi import FastAPI

    from planner.conversation.api import _runtime, router
    from planner.executor.codec import encode_value

    app = FastAPI()
    app.include_router(router, prefix="/api")
    runtime = SimpleNamespace(
        system=harness.system,
        database_path=harness.endpoint.db_path,
        executor_endpoint=harness.endpoint,
    )
    app.dependency_overrides[_runtime] = lambda: runtime
    body = {"executor_ask": encode_value(ask)}
    if ask.kind is AskKind.permission:
        path = "permission-answers"
        body.update(ask_id=public_ask_id(ask), option_id="allow")
    else:
        path = "user-input-answers"
        body.update(
            request_id=public_ask_id(ask),
            answers={
                answer.question_id: {"answers": list(answer.answers)} for answer in INPUT_ANSWERS
            },
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://isolated"
    ) as client:
        response = await client.post(f"/api/conversations/c_scripted/{path}", json=body)
    assert response.status_code == 200, response.text
    return cast(dict[str, bool], response.json())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_saved_answer_after_vps_restart_remains_unapplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        resume = None
        try:
            if kind is AskKind.permission:
                await harness.native.raise_ask()
                await harness.wait_ask_reachable()
                ask = harness.ask
            else:
                await harness.native.raise_user_input()
                await harness.wait_input_reachable()
                ask = harness.input_ask
            resume = await hold_reconnection(harness, monkeypatch)
            assert await post_owner_answer(harness, ask) == {"landed": True}
            answer = harness.endpoint.store.recorded_answer(ask)
            assert answer is not None
            assert harness.endpoint.pending_commands == {}
            assert harness.native.answers == [] and harness.native.user_answers == []
            # Both accepted receipt and durable remote provenance survive without inventory.
            await harness.restart_vps_core(wait_ready=False)
            assert harness.endpoint.owns_conversation("c_scripted")
            assert harness.endpoint.conversation_context("c_scripted") is not None
            assert await post_owner_answer(harness, ask) == {"landed": True}
            assert harness.endpoint.store.recorded_answer(ask) == answer
            assert harness.endpoint.pending_commands == {}
            resume.set()
            await eventually(lambda: harness.native.stop_count == 1, "old child was not stopped")
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: not state.child_is_quarantined, "stop proof did not settle")
            assert harness.native.answers == [] and harness.native.user_answers == []
            evidence = harness.endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.applied_at_mac_ms is None
            assert harness.endpoint.store.recorded_answer(ask) == answer
            assert not harness.endpoint.store.answer_target_is_open(ask)
            assert await post_owner_answer(harness, ask) == {"landed": True}
        finally:
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_ready_transition_sweeps_answer_saved_after_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        resume = None
        ready_release = asyncio.Event()
        reached = asyncio.Event()
        try:
            if kind is AskKind.permission:
                await harness.native.raise_ask()
                await harness.wait_ask_reachable()
                ask = harness.ask
            else:
                await harness.native.raise_user_input()
                await harness.wait_input_reachable()
                ask = harness.input_ask
            resume = await hold_reconnection(harness, monkeypatch)
            request = harness.mac._request

            async def pause_ready(
                message_kind: WireMessageKind, payload: JsonObject
            ) -> WireMessage:
                if message_kind is WireMessageKind.reconciliation_ready:
                    reached.set()
                    await ready_release.wait()
                return await request(message_kind, payload)

            monkeypatch.setattr(harness.mac, "_request", pause_ready)
            resume.set()
            await asyncio.wait_for(reached.wait(), 5)
            assert not harness.endpoint.ready
            assert await post_owner_answer(harness, ask) == {"landed": True}
            assert harness.endpoint.pending_commands == {}
            ready_release.set()
            event = "permission_answered" if kind is AskKind.permission else "user_input_answered"
            await eventually(
                lambda: harness.events(event) == 1, "ready sweep missed durable answer"
            )
        finally:
            ready_release.set()
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_local_cancel_revokes_saved_answer_before_native_wait_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        resume = None
        native_cancel = asyncio.Event()
        cancel_entered = asyncio.Event()
        interrupt = None
        try:
            if kind is AskKind.permission:
                await harness.native.raise_ask()
                await harness.wait_ask_reachable()
                ask = harness.ask
            else:
                await harness.native.raise_user_input()
                await harness.wait_input_reachable()
                ask = harness.input_ask
            resume = await hold_reconnection(harness, monkeypatch)
            assert await post_owner_answer(harness, ask) == {"landed": True}
            answer = harness.endpoint.store.recorded_answer(ask)

            async def stalled_cancel(*args: object) -> None:
                cancel_entered.set()
                await native_cancel.wait()

            monkeypatch.setattr(harness.system, "_cancel_child_turn", stalled_cancel)
            old_system = harness.system
            interrupt = asyncio.create_task(old_system.interrupt("c_scripted"))
            await asyncio.wait_for(cancel_entered.wait(), 2)
            evidence = harness.endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.reachability is AskReachability.callback_gone
            context = harness.endpoint.store.turn_context("c_scripted")
            assert context is not None and context.callbacks_revoked and not context.ended
            # A callback announced after revocation cannot create a newly answerable ask.
            await harness.native.raise_user_input("request_late")
            # Simulate a crash without permitting the pending native cancellation to finish.
            state = old_system._conversations["c_scripted"]
            for settlement in tuple(state.settlement_tasks):
                settlement.cancel()
            await asyncio.gather(*tuple(state.settlement_tasks), return_exceptions=True)
            await asyncio.gather(interrupt, return_exceptions=True)
            await harness.restart_vps_core(wait_ready=False)
            assert await post_owner_answer(harness, ask) == {"landed": True}
            assert harness.endpoint.store.recorded_answer(ask) == answer
            resume.set()
            await eventually(lambda: harness.endpoint.ready, "revoked turn did not reconcile")
            await eventually(lambda: not harness.mac.spool.pending(), "late callback did not drain")
            assert harness.native.answers == [] and harness.native.user_answers == []
            assert (
                harness.events("permission_answered") == harness.events("user_input_answered") == 0
            )
            assert (
                harness.endpoint._current_ask_evidence(
                    "c_scripted",
                    f"{ask.turn.turn_number}:user_input:request_late",
                    AskKind.user_input,
                )
                is None
            )
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: not state.child_is_quarantined, "restart stop did not settle")
            assert harness.native.stop_count == 1
            assert harness.endpoint.pending_commands == {}
            evidence = harness.endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.reachability is AskReachability.child_gone
            assert evidence.applied_at_mac_ms is None
            assert not await harness.system.is_running("c_scripted")
        finally:
            native_cancel.set()
            if interrupt is not None and not interrupt.done():
                interrupt.cancel()
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


def test_revocation_before_emission_blocks_immediate_send_and_pending_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from planner.executor.contracts import ChildCommand, ChildOperation

        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        prepared, emit, replay_prepared = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancel_entered, cancel_finish = asyncio.Event(), asyncio.Event()
        interrupt = None
        resume = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            transfer = harness.endpoint._transfer_command_inputs
            preparations = 0

            async def prepare(command: ChildCommand) -> None:
                nonlocal preparations
                await transfer(command)
                if command.operation is ChildOperation.answer_permission_ask:
                    preparations += 1
                    (prepared if preparations == 1 else replay_prepared).set()
                    await emit.wait()

            async def stalled_cancel(*args: object) -> None:
                cancel_entered.set()
                await cancel_finish.wait()

            monkeypatch.setattr(harness.endpoint, "_transfer_command_inputs", prepare)
            monkeypatch.setattr(harness.system, "_cancel_child_turn", stalled_cancel)
            assert await post_owner_answer(harness, harness.ask) == {"landed": True}
            await asyncio.wait_for(prepared.wait(), 2)
            answer = harness.endpoint.store.recorded_answer(harness.ask)
            assert answer is not None
            interrupt = asyncio.create_task(harness.system.interrupt("c_scripted"))
            await asyncio.wait_for(cancel_entered.wait(), 2)
            assert len(harness.endpoint.pending_commands) == 1
            resume = await hold_reconnection(harness, monkeypatch)
            resume.set()
            # Reach the real reconciliation_ready pending_commands replay while the
            # original immediate sender remains paused after its preparation.
            await asyncio.wait_for(replay_prepared.wait(), 5)
            emit.set()
            await eventually(lambda: not harness.endpoint.pending_commands, "closed task survived")
            assert preparations == 2
            assert harness.native.answers == []
            evidence = harness.endpoint.store.ask_evidence(harness.ask)
            assert evidence is not None and evidence.applied_at_mac_ms is None
            assert harness.endpoint.store.recorded_answer(harness.ask) == answer
            cancel_finish.set()
            await asyncio.wait_for(interrupt, 2)
        finally:
            emit.set()
            cancel_finish.set()
            if resume is not None:
                resume.set()
            if interrupt is not None:
                await asyncio.gather(interrupt, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_frame_before_revocation_preserves_native_application_after_assignment_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        applied, callback_entered = asyncio.Event(), asyncio.Event()
        cancel_entered, cancel_finish = asyncio.Event(), asyncio.Event()
        interrupt = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            native_answer = harness.native.answer_permission_ask

            async def delayed_native_answer(ask_id: str, option_id: str) -> None:
                callback_entered.set()
                await applied.wait()
                await native_answer(ask_id, option_id)

            async def stalled_cancel(*args: object) -> None:
                cancel_entered.set()
                await cancel_finish.wait()

            monkeypatch.setattr(harness.native, "answer_permission_ask", delayed_native_answer)
            monkeypatch.setattr(harness.system, "_cancel_child_turn", stalled_cancel)
            assert await post_owner_answer(harness, harness.ask) == {"landed": True}
            await asyncio.wait_for(callback_entered.wait(), 5)
            interrupt = asyncio.create_task(harness.system.interrupt("c_scripted"))
            await asyncio.wait_for(cancel_entered.wait(), 2)
            harness.endpoint.store.close_child(harness.ask.child, 1000)
            applied.set()
            await eventually(
                lambda: harness.events("permission_answered") == 1,
                "retrospective closed-child application was discarded",
            )
            await eventually(
                lambda: not harness.mac.spool.pending(),
                "nested outcome or standalone application was rejected",
            )
            assert harness.native.answers == [("ask_permission", "allow")]
            evidence = harness.endpoint.store.ask_evidence(harness.ask)
            assert evidence is not None and evidence.applied_at_mac_ms is not None
            assert await post_owner_answer(harness, harness.ask) == {"landed": True}
            cancel_finish.set()
            await asyncio.wait_for(interrupt, 2)
            assert harness.events("permission_answered") == 1
        finally:
            applied.set()
            cancel_finish.set()
            if interrupt is not None:
                await asyncio.gather(interrupt, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_generation_change_during_emission_preparation_keeps_pending_answer_for_ready_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from collections.abc import Coroutine

        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        first_entered, first_release, first_returned = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        replay_entered, replay_release = asyncio.Event(), asyncio.Event()
        resume = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            emit_original = harness.system.emit_executor_answer
            calls = 0

            async def pause_emission(
                conversation_id: str, emit: Callable[[], Coroutine[Any, Any, None]]
            ) -> None:
                nonlocal calls
                calls += 1
                first = calls == 1
                if first:
                    first_entered.set()
                    await first_release.wait()
                else:
                    replay_entered.set()
                    await replay_release.wait()
                await emit_original(conversation_id, emit)
                if first:
                    first_returned.set()

            monkeypatch.setattr(harness.system, "emit_executor_answer", pause_emission)
            assert await post_owner_answer(harness, harness.ask) == {"landed": True}
            await asyncio.wait_for(first_entered.wait(), 2)
            generation = harness.mac.identity.connection_generation
            resume = await hold_reconnection(harness, monkeypatch)
            resume.set()
            await asyncio.wait_for(replay_entered.wait(), 5)
            assert harness.mac.identity.connection_generation > generation
            first_release.set()
            await asyncio.wait_for(first_returned.wait(), 2)
            # Stale generation is temporary. It must not cancel the shared result future.
            command, future = next(iter(harness.endpoint.pending_commands.values()))
            assert not future.done()
            answer = harness.endpoint.store.recorded_answer(harness.ask)
            assert answer is not None and command.command_id == answer.answer_id
            replay_release.set()
            await eventually(
                lambda: harness.events("permission_answered") == 1,
                "current generation replay lost the durable answer",
            )
            assert harness.native.answers == [("ask_permission", "allow")]
        finally:
            first_release.set()
            replay_release.set()
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


def test_shutdown_waits_for_admitted_outcome_application_and_final_event_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.contracts import ChildOperation, CommandOutcome

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        release_application, release_event = asyncio.Event(), asyncio.Event()
        application_waiting, event_waiting, stop_hint = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        stopping = asyncio.Event()
        accepted: list[QueuedEnvelope] = []
        killing: asyncio.Task[None] | None = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            reference = harness.ask.child
            stop_exact = harness.mac.registry._stop_exact_child
            publish_application = harness.mac.registry._publish_application
            native_stop = harness.native.stop
            accept = harness.endpoint.accept_envelope
            resolve = harness.endpoint._resolve_command

            async def observe_native_stop(command: Any) -> CommandOutcome:
                stopping.set()
                return await stop_exact(command)

            async def late_application(application: Any) -> None:
                application_waiting.set()
                await release_application.wait()
                await publish_application(application)

            async def final_native_event() -> None:
                assert harness.native.sink is not None
                await harness.native.sink.vendor_session_cursor_rebound("cleanup-cursor")
                await native_stop()

            async def withheld_event(
                envelope: QueuedEnvelope, identity: ExecutorIdentity
            ) -> AcceptanceReceipt:
                if envelope.kind is EnvelopeKind.backend_event and envelope.payload.get(
                    "arguments"
                ) == {"vendor_session_cursor": "cleanup-cursor"}:
                    event_waiting.set()
                    await release_event.wait()
                receipt = await accept(envelope, identity)
                if receipt.disposition is ReceiptDisposition.accepted:
                    accepted.append(envelope)
                return receipt

            async def observed_hint(outcome: CommandOutcome, *, stop_proven: bool = False) -> None:
                pending = harness.endpoint.pending_commands.get(outcome.command_id)
                if pending is not None and pending[0].operation is ChildOperation.stop:
                    stop_hint.set()
                await resolve(outcome, stop_proven=stop_proven)

            async def cancel_native() -> None:
                pass

            monkeypatch.setattr(harness.mac.registry, "_stop_exact_child", observe_native_stop)
            monkeypatch.setattr(harness.mac.registry, "_publish_application", late_application)
            monkeypatch.setattr(harness.native, "cancel_running_turn", cancel_native, raising=False)
            monkeypatch.setattr(harness.native, "stop", final_native_event)
            monkeypatch.setattr(harness.endpoint, "accept_envelope", withheld_event)
            monkeypatch.setattr(harness.endpoint, "_resolve_command", observed_hint)
            assert await post_owner_answer(harness, harness.ask) == {"landed": True}
            await asyncio.wait_for(application_waiting.wait(), 5)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            await asyncio.wait_for(stopping.wait(), 5)
            assert not event_waiting.is_set() and not stop_hint.is_set() and not killing.done()
            assert not stop_hint.is_set() and not killing.done()
            release_application.set()
            await asyncio.wait_for(event_waiting.wait(), 5)
            await asyncio.wait_for(stop_hint.wait(), 5)
            stops = [
                (command, future)
                for command, future in harness.endpoint.pending_commands.values()
                if command.operation is ChildOperation.stop
            ]
            assert stops and all(not future.done() for _, future in stops)
            assert harness.endpoint.store.child_assignment(reference) is not None
            assert not killing.done()
            release_event.set()
            await asyncio.wait_for(killing, 5)
            await eventually(lambda: not harness.mac.spool.pending(), "cleanup FIFO did not drain")
            assert harness.endpoint.store.child_assignment(reference) is None
            assert harness.events("permission_answered") == 1
            assert harness.native.answers == [("ask_permission", "allow")]
            state = harness.system._conversations["c_scripted"]
            assert state.child is None and not state.child_is_quarantined
            connection = connect(harness.endpoint.db_path)
            try:
                stop = stops[0][0]
                import hashlib

                submission = "stopped_" + hashlib.sha256(stop.command_id.encode()).hexdigest()
                stop_sequence = connection.execute(
                    "SELECT sequence FROM mac_inbox_positions WHERE submission_id=?", (submission,)
                ).fetchone()[0]
                final_event = next(
                    row
                    for row in accepted
                    if row.kind is EnvelopeKind.backend_event
                    and row.payload.get("arguments") == {"vendor_session_cursor": "cleanup-cursor"}
                )
                application = next(
                    row for row in accepted if row.kind is EnvelopeKind.answer_application
                )
                assert all(row.sequence < stop_sequence for row in (application, final_event))
                for row in (application, final_event):
                    receipt = harness.endpoint.store.lookup_receipt(
                        row.sender_id, row.submission_id
                    )
                    assert receipt is None  # The later stop has advanced the single inbox position.
                assert not harness.endpoint.store.cleanup_evidence_uncertain(reference)
            finally:
                connection.close()
        finally:
            release_application.set()
            release_event.set()
            if killing is not None and not killing.done():
                killing.cancel()
                await asyncio.gather(killing, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_authenticated_catalog_exchange_timeout_preserves_active_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TLS metadata reads share transport safely and allocate no child/journal work."""
    import time

    from tests.support.executor_catalog import ScriptedCatalogObserver

    from planner.conversation.contracts import WorkspacePathPolicy
    from planner.conversation.model_resolution.contracts import (
        CatalogExpectedContext,
        CatalogObservation,
        CatalogRequest,
        CatalogUnavailable,
        CatalogWorkspaceBinding,
        ValidationReason,
    )
    from planner.executor import mac as mac_module
    from planner.executor import vps as vps_module

    async def run() -> None:
        started = time.monotonic()
        harness = await boot(tmp_path, monkeypatch)
        try:
            assert harness.endpoint.identity is not None
            identity = harness.endpoint.identity
            writer = harness.endpoint.writer
            observer = ScriptedCatalogObserver(lambda: harness.mac.identity)
            harness.mac._catalog_observer = observer
            request = CatalogRequest(
                ConversationBackendKey.codex,
                CatalogExpectedContext(
                    identity,
                    CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.native_absolute, None),
                ),
            )
            children_before = tuple(harness.mac.registry.children)
            commands_before = tuple(harness.mac.registry._commands)
            starts_before = harness.native.start_count
            prompts_before = harness.native.prompt_count
            journal_additions: list[EnvelopeKind] = []
            mint = harness.mac.spool.add

            def count_journal(entry: Any, **arguments: Any) -> QueuedEnvelope:
                journal_additions.append(entry.kind)
                return mint(entry, **arguments)

            monkeypatch.setattr(harness.mac.spool, "add", count_journal)
            result = await harness.endpoint.observe_catalog(request, attempt_id="connected-first")
            assert isinstance(result, CatalogObservation)
            assert result.rows[0].submitted_token == "gpt-scripted"
            assert result.evidence.context.executor_identity == identity
            assert result.evidence.context.workspace == request.expected_context.workspace
            assert result.evidence.attempt_id == "connected-first"
            assert result.evidence.observed_at == observer.observed_at

            # A still-running scripted child shares this real authenticated link.
            observer.block = True
            monkeypatch.setattr(vps_module, "CATALOG_TIMEOUT_SECONDS", 0.05)
            monkeypatch.setattr(mac_module, "CATALOG_TIMEOUT_SECONDS", 0.2)
            timed_out = await harness.endpoint.observe_catalog(
                request, attempt_id="connected-second"
            )
            assert isinstance(timed_out, CatalogUnavailable)
            assert timed_out.reason is ValidationReason.timeout
            await asyncio.wait_for(observer.cancelled.wait(), 2)
            await eventually(
                lambda: not harness.mac._catalog_tasks, "Catalog cleanup did not settle"
            )
            assert tuple(harness.mac.registry.children) == children_before
            assert tuple(harness.mac.registry._commands) == commands_before
            assert harness.native.start_count == starts_before
            assert harness.native.prompt_count == prompts_before
            assert journal_additions == []
            assert harness.endpoint.identity == identity
            assert harness.endpoint.writer is writer and harness.endpoint.ready
            assert harness.mac.identity == identity
            assert harness.native.stop_count == 0

            # The native child emits after the timeout. The event crosses the
            # registry, spool, authenticated transport and canonical VPS writer.
            assert harness.native.sink is not None and harness.native.turn is not None
            before = harness.events("tool_call_finished")
            await harness.native.sink.tool_call_finished(
                harness.native.turn,
                tool_call_id="after-catalog-timeout",
                tool_call_status=ToolCallStatus.completed,
                detail="Transport remains active.",
            )
            await eventually(
                lambda: harness.events("tool_call_finished") == before + 1,
                "Child event did not reach VPS after catalog timeout",
            )
            assert journal_additions == [EnvelopeKind.backend_event]
            assert harness.endpoint.identity == identity and harness.endpoint.ready
            print(f"catalog TLS scenario elapsed: {time.monotonic() - started:.3f}s")
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["unknown", "preparing", "invoking", "finished"])
def test_reconnect_asks_once_and_resends_only_unknown_command_with_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Real TLS reply loss and in-flight completion need both readers and native ownership."""
    from planner.executor.contracts import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        observed, release = asyncio.Event(), asyncio.Event()
        caller: asyncio.Task[Any] | None = None
        locked = None
        attempts = 0
        sends: list[str] = []
        asks: list[JsonObject] = []
        try:
            reference = harness.ask.child
            command = ChildCommand(
                "reconnect-proof", reference, ChildOperation.cancel_running_turn, None, {}
            )
            mac_send = harness.mac._send
            # Commands use the shared send lock directly; observe at the writer boundary.
            writer = harness.endpoint.writer
            assert writer is not None
            original_command = harness.mac._command
            dropped = False

            async def invoke() -> None:
                nonlocal attempts
                attempts += 1
                if phase == "invoking":
                    observed.set()
                    await release.wait()

            async def receive(message: WireMessage) -> None:
                nonlocal dropped
                if message.request_id == command.command_id:
                    sends.append(message.request_id)
                    if phase == "unknown" and not dropped:
                        dropped = True
                        observed.set()
                        return
                await original_command(message)

            async def lose_result(message: WireMessage) -> None:
                if message.kind is WireMessageKind.command_outcome and (
                    message.request_id == command.command_id and phase == "finished"
                ):
                    observed.set()
                    return
                await mac_send(message)

            request = harness.endpoint._request

            async def ask(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                if kind is WireMessageKind.reconnect_ask:
                    asks.append(payload)
                return await request(kind, payload)

            monkeypatch.setattr(harness.native, "cancel_running_turn", invoke, raising=False)
            monkeypatch.setattr(harness.mac, "_command", receive)
            monkeypatch.setattr(harness.mac, "_send", lose_result)
            monkeypatch.setattr(harness.endpoint, "_request", ask)
            if phase == "preparing":
                locked = harness.mac.registry.children[reference.child_id].lock
                await locked.acquire()
            caller = asyncio.create_task(harness.endpoint.send_command(command))
            if phase == "preparing":
                await eventually(
                    lambda: harness.mac.registry.ask((command.command_id,))[0].phase == "preparing",
                    "Command did not enter preparation",
                )
            else:
                await asyncio.wait_for(observed.wait(), 2)
            assert not caller.done()
            generation = harness.mac.identity.connection_generation
            writer.close()
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.ready
                ),
                "Single reconnect ask did not admit the new link",
            )
            assert asks == [{"command_ids": [command.command_id]}]
            if phase in ("preparing", "invoking"):
                assert not caller.done() and sends == [command.command_id]
            if locked is not None:
                locked.release()
                locked = None
            release.set()
            outcome = await asyncio.wait_for(caller, 2)
            assert outcome.disposition is CommandDisposition.succeeded
            assert attempts == 1
            assert sends == [command.command_id] * (2 if phase == "unknown" else 1)
            assert harness.mac.registry.ask((command.command_id,))[0].outcome == outcome
            assert not harness.mac.spool.pending()
            await asyncio.sleep(0.06)
            assert len(asks) == 1
            assert harness.native.start_count == harness.native.prompt_count == 1
        finally:
            release.set()
            if locked is not None:
                locked.release()
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_reconnect_ready_precedes_retained_output_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a retained FIFO suffix cannot hold connection readiness hostage."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            async with paused_outbox(harness.mac.spool):
                await harness.native.raise_ask()
                queued = harness.mac.spool.pending()
                assert len(queued) == 1
                generation = harness.mac.identity.connection_generation
                assert harness.endpoint.writer is not None
                harness.endpoint.writer.close()
                await eventually(
                    lambda: (
                        harness.mac.identity.connection_generation > generation
                        and harness.endpoint.ready
                    ),
                    "Ready waited for outbox replay",
                )
                assert harness.mac.spool.pending() == queued
                assert harness.events("permission_asked") == 0
            await harness.wait_ask_reachable()
            await eventually(
                lambda: not harness.mac.spool.pending(), "Retained output did not drain"
            )
            receipt = harness.endpoint.store.lookup_receipt(
                queued[0].sender_id, queued[0].submission_id
            )
            assert receipt is not None and receipt.result.get("applied") is True
            assert (
                await harness.endpoint.accept_envelope(queued[0], harness.mac.identity) == receipt
            )
            assert harness.events("permission_asked") == 1
        finally:
            await harness.close()

    asyncio.run(run())
