"""Real Mac/VPS dispatch over isolated app HTTPS and WebSockets.

The scripted native adapter never calls a provider. Every event still traverses
native registry, durable spool, framed transport, VPS acceptance and conversation
core. These are process/SQLite gates; no browser is needed for this boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import TypeAdapter
from tests.support.executor_control import paused_outbox
from tests.support.executor_https import HttpsProxy, serve_executor_app
from tests.support.executor_inbox import lookup_receipt

from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    AgentMessageDelta,
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendUserInputRequest,
    ModelThinkingHappened,
    PermissionAskRaised,
    ToolCallFinished,
    ToolCallProgress,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputRequested,
    VendorSessionCursorRebound,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
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
from planner.execution_configuration import PROTOCOL_VERSION
from planner.execution_identity import AskKind, AskReference, ExecutorIdentity
from planner.executor.configuration import ExecutorPaths
from planner.executor.link import ControlMessageStream, Link
from planner.executor.mac_outbox import OutboxEntry
from planner.executor.mac_service import MacService
from planner.executor.protocol import (
    AcceptanceReceipt,
    CommandDisposition,
    ConnectionState,
    DurableAnswer,
    EnvelopeKind,
    IpcOperation,
    IpcRequest,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
    decode_command_arguments,
    encode_value,
)
from planner.executor.vps_transport import VpsExecutor

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
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
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
            await self.sink.report(
                turn_token,
                ToolCallStarted(
                    tool_call_id=f"early-{self.prompt_count}",
                    title="Before prompt returns",
                    tool_kind="other",
                    detail=None,
                ),
            )
        return BackendPromptAccepted(True)

    async def answer_permission_ask(
        self, ask_id: str, option_id: str, **kwargs: object
    ) -> AnswerFate:
        self.answers.append((ask_id, option_id))
        return AnswerFate.applied

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...], **kwargs: object
    ) -> AnswerFate:
        self.user_answers.append((request_id, answers))
        return AnswerFate.applied

    async def raise_user_input(self, request_id: str = "request_input") -> None:
        assert self.sink is not None and self.turn is not None
        await self.sink.report(
            self.turn,
            UserInputRequested(
                request=BackendUserInputRequest(
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
                )
            ),
        )

    async def stop(self) -> None:
        self.stop_count += 1

    async def raise_ask(self) -> None:
        assert self.sink is not None and self.turn is not None
        await self.sink.report(
            self.turn,
            PermissionAskRaised(
                ask=BackendPermissionAsk(
                    "ask_permission",
                    "Run the scripted action?",
                    None,
                    (
                        PermissionAskOption("allow", "Allow", "allow_once"),
                        PermissionAskOption("deny", "Deny", "reject_once"),
                    ),
                )
            ),
        )


class CutReceiptEndpoint(VpsExecutor):
    """Cut after the real canonical transaction, before its receipt reaches Mac."""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, RealClock())
        self.cut_submission: str | None = None
        self.cut_count = 0
        self.cut_user_input_application = False
        self.cut_input = False
        self.input_cut_count = 0
        self.cut_prompt_frame = False

    async def session(self, stream: ControlMessageStream) -> None:
        original_receive = stream.receive

        async def receive() -> WireMessage:
            message = await original_receive()
            if self.cut_user_input_application and message.kind is WireMessageKind.envelope:
                envelope = TypeAdapter(QueuedEnvelope).validate_python(message.payload)
                if envelope.kind is EnvelopeKind.answer_application:
                    ask = envelope.payload.get("ask")
                    if isinstance(ask, dict) and ask.get("kind") == "user_input":
                        self.cut_user_input_application = False
                        self.cut_submission = envelope.submission_id
            return message

        stream.receive = receive  # type: ignore[method-assign]
        original = stream.send

        async def send(message: WireMessage) -> None:
            if (
                self.cut_prompt_frame
                and message.kind is WireMessageKind.command
                and message.payload.get("operation") == "write_prompt"
            ):
                self.cut_prompt_frame = False
                assert self.runs.link is not None
                self.runs.link.close()
                return
            if (
                message.kind is WireMessageKind.file_transfer
                and message.payload.get("direction") == "download"
                and self.cut_input
            ):
                self.cut_input = False
                self.input_cut_count += 1
                assert self.runs.link is not None
                self.runs.link.close()
                return
            if (
                message.kind == WireMessageKind.receipt
                and self.cut_submission is not None
                and message.payload.get("submission_id") == self.cut_submission
            ):
                self.cut_submission = None
                self.cut_count += 1
                assert self.runs.link is not None
                self.runs.link.close()
                return
            await original(message)

        stream.send = send  # type: ignore[method-assign]
        await super().session(stream)


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
    mac: MacService
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
        child = next(iter(self.mac.runner._children.values()))
        assert self.native.turn is not None
        return AskReference(child.reference, self.native.turn, "ask_permission", AskKind.permission)

    @property
    def input_ask(self) -> AskReference:
        child = next(iter(self.mac.runner._children.values()))
        assert self.native.turn is not None
        return AskReference(child.reference, self.native.turn, "request_input", AskKind.user_input)

    async def wait_input_reachable(self) -> None:
        await eventually(
            lambda: (
                self.system._conversations["c_scripted"].running_turn is not None
                and bool(
                    self.system._conversations[
                        "c_scripted"
                    ].running_turn.pending_user_input_questions
                )
            ),
            "input did not reach conversation",
        )

    async def wait_ask_reachable(self) -> None:
        await eventually(
            lambda: (
                self.system._conversations["c_scripted"].running_turn is not None
                and bool(
                    self.system._conversations["c_scripted"].running_turn.pending_permission_ask_ids
                )
            ),
            "ask did not reach conversation",
        )

    def saved_answer(self, ask: AskReference) -> DurableAnswer | None:
        connection = connect(self.endpoint.db_path)
        try:
            key = "ask_id" if ask.kind is AskKind.permission else "request_id"
            row = connection.execute(
                "SELECT payload,created_at FROM conversation_events "
                "WHERE conversation_id=? AND kind=? "
                "AND json_extract(payload,?)=? ORDER BY sequence DESC LIMIT 1",
                (
                    ask.child.conversation_id,
                    "permission_answered"
                    if ask.kind is AskKind.permission
                    else "user_input_answered",
                    f"$.{key}",
                    public_ask_id(ask),
                ),
            ).fetchone()
            if row is None:
                return None
            value = json.loads(row[0])
            return DurableAnswer(
                value["answer_id"],
                ask,
                value.get("option_id"),
                row[1],
                tuple(
                    UserInputAnswer(key, tuple(answer["answers"]))
                    for key, answer in value["answers"].items()
                )
                if "answers" in value
                else None,
            )
        finally:
            connection.close()

    def answer_applied(self, ask: AskReference) -> bool:
        answer = self.saved_answer(ask)
        if answer is None:
            return False
        connection = connect(self.endpoint.db_path)
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM conversation_events WHERE conversation_id=? "
                    "AND kind='answer_applied' "
                    "AND json_extract(payload,'$.answer_id')=?",
                    (ask.child.conversation_id, answer.answer_id),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    async def choose_answer(self, ask: AskReference, option_id: str) -> DurableAnswer:
        assert (
            await self.system.answer_permission_ask(
                ask.child.conversation_id, public_ask_id(ask), option_id
            )
            is AnswerFate.saved
        )
        answer = self.saved_answer(ask)
        assert answer is not None
        return answer

    async def choose_input(
        self, ask: AskReference, answers: tuple[UserInputAnswer, ...]
    ) -> DurableAnswer:
        assert (
            await self.system.answer_user_input(
                ask.child.conversation_id, public_ask_id(ask), answers
            )
            is AnswerFate.saved
        )
        answer = self.saved_answer(ask)
        assert answer is not None
        return answer

    async def restart_vps_core(self, *, wait_ready: bool = True) -> None:
        await asyncio.wait_for(self.endpoint.close(), 2)
        self.old_systems.append(self.system)
        # A process exit releases the lifecycle lease without a native stop.
        # This in-process crash fixture must model that kernel-owned release.
        self.system._runtime_ownership.close()
        self.endpoint = CutReceiptEndpoint(self.endpoint.db_path)
        self.system = make_core(self.endpoint)
        self.endpoint.bind_conversations(self.system)
        self.transport_app.state.executor_endpoint = self.endpoint
        if wait_ready:
            await eventually(lambda: self.endpoint.runs.ready, "VPS core restart did not reconcile")

    async def close(self) -> None:
        try:
            await self._close()
        finally:
            await self.transport_stack.aclose()

    async def _close(self) -> None:
        self.mac._stop.set()
        if self.endpoint.runs.link is not None:
            self.endpoint.runs.link.close()
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


def make_core(endpoint: VpsExecutor) -> SqliteProcessConversationSystem:
    system = SqliteProcessConversationSystem(
        store=ConversationStore(endpoint.db_path),
        backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(endpoint.db_path),
    )
    # All transport fixtures are native. Local discovery is never called here.
    system.configure_machine_catalog(endpoint.catalog)
    return system


async def boot(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_factory: Callable[[ResolvedConversationStart], BackendChildFactory] | None = None,
    backend_key: ConversationBackendKey = ConversationBackendKey.codex,
    model: str = "scripted",
    reasoning_effort: str | None = None,
    emit_early_event: bool = False,
    reconnect_interval: float | None = None,
) -> ExecutorHarness:
    if reconnect_interval is not None:
        # Only fixtures with short reconnect deadlines accelerate this timer.
        monkeypatch.setattr(
            "planner.executor.mac_service.RECONNECT_INTERVAL_SECONDS", reconnect_interval
        )
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
            employee_launch_reasoning_effort=reasoning_effort,
        )
        worker_ticket_id = ticket.id
    finally:
        connection.close()
    endpoint = CutReceiptEndpoint(db_path)
    system = make_core(endpoint)
    endpoint.bind_conversations(system)
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
    mac = MacService(
        paths=ExecutorPaths(root / "mac"),
        revision="a" * 40,
        executor_id="test-executor",
        panels_server_url=proxy.origin,
        session_factory=proxy.session_factory,
    )
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    observer = ScriptedBackendSnapshots(lambda: mac.identity)
    observer.model = model
    if backend_key is ConversationBackendKey.claude:
        observer.models = (model, "claude-new")
    observer.efforts = ("low", "medium", "high", "xhigh", "max")
    mac.catalog.snapshots = observer

    def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
        del resolved

        def create(**arguments: object) -> BackendChild:
            native.sink = cast(BackendEventSink, arguments["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    mac.runner.factory = native_factory or factory
    task = asyncio.create_task(mac.run())
    harness = ExecutorHarness(
        root, endpoint, mac, system, native, task, [], transport_stack, transport_app, proxy
    )
    try:
        await eventually(
            lambda: endpoint.runs.ready and mac.connection.state == ConnectionState.ready,
            "Initial Mac/VPS handshake did not become ready",
        )
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c_scripted",
                model=model,
                backend_key=backend_key,
                reasoning_effort=reasoning_effort,
                workspace_folder=root,
                workspace_path_policy=WorkspacePathPolicy.absolute,
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
        await eventually(lambda: not mac.outbox.pending(), "Initial command outcomes did not drain")
        return harness
    except BaseException:
        await harness.close()
        raise


@pytest.mark.parametrize("outage", ["lost_frame", "already_down"])
def test_prompt_survives_short_link_drop_once_with_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outage: str
) -> None:
    from planner.conversation.message_content import MessageFile, MessageText

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        resume: asyncio.Event | None = None
        sending: asyncio.Task[PromptDeliveryFate] | None = None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            identity = harness.mac.identity
            contents = b"Attachment from the VPS after reconnect"
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = (
                harness.mac.paths.root
                / "artifacts/files/conversations/c_scripted"
                / kept.stored_file_id
            )
            native_path.parent.mkdir(parents=True, exist_ok=True)
            native_path.write_bytes(b"Stale local bytes must be fetched again")
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            if outage == "lost_frame":
                harness.endpoint.cut_prompt_frame = True
            else:
                resume = await hold_reconnection(harness, monkeypatch)
            sending = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageText("Inspect the attachment"),
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
            if resume is not None:
                await eventually(
                    lambda: bool(harness.endpoint.runs.pending_commands),
                    "Prompt was not queued while the link was down",
                )
                assert not sending.done()
                assert harness.native.prompt_count == 1
                resume.set()
            fate = await asyncio.wait_for(sending, 3)
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            assert harness.mac.identity.executor_incarnation == identity.executor_incarnation
            assert harness.mac.identity.connection_generation > identity.connection_generation
            assert not harness.endpoint.cut_prompt_frame
            assert harness.native.start_count == 1
            assert harness.native.prompt_count == 2
            assert native_path.read_bytes() == contents
            # Another reconciliation must not invoke the same prompt a second time.
            generation = harness.mac.identity.connection_generation
            assert harness.endpoint.runs.link is not None
            harness.endpoint.runs.link.close()
            await eventually(
                lambda: (
                    harness.endpoint.runs.ready
                    and harness.mac.identity.connection_generation > generation
                ),
                "Follow-up reconnect did not finish",
            )
            assert harness.native.prompt_count == 2
        finally:
            if resume is not None:
                resume.set()
            if sending is not None and not sending.done():
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


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
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
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
            await harness.native.sink.report(
                harness.native.turn,
                ToolCallStarted(
                    tool_call_id="idle", title="While idle", tool_kind="other", detail=None
                ),
            )
            await eventually(
                lambda: harness.events("tool_call_started") == 3,
                "Idle backend event waited for the retry timer",
                seconds=2,
            )
            await asyncio.sleep(0.1)
            child = next(iter(harness.mac.runner._children.values()))
            response = await asyncio.wait_for(
                harness.mac.gateway.handle(
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
            assert (
                harness.mac.connection.state is ConnectionState.ready
                and harness.endpoint.runs.ready
            )
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

        def native_factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
            assert resolved.backend_key is ConversationBackendKey.claude
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
                    lambda: not harness.mac.outbox.pending(), "result receipt did not drain"
                )

            for index in (1, 2):
                await finish_turn()
                old = clients[-1]
                old_prompt = prompts(old)[-1]
                old_reference = next(iter(harness.mac.runner._children.values())).reference
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
                assert harness.endpoint.runs.store.child_assignment(old_reference) is not None
                cleanup_release.set()
                await asyncio.wait_for(sending, 5)
                await eventually(
                    lambda: not harness.mac.outbox.pending(), "rebind outcomes did not drain"
                )
                replacement = clients[-1]
                assert len(clients) == index + 1 and old.disconnected
                assert replacement.options.resume == session
                assert replacement.options.model == "claude-new"
                assert replacement.options.effort == ("low" if index == 1 else "high")
                assert harness.endpoint.runs.store.child_assignment(old_reference) is None
                assert len(prompts(replacement)) == 1
                prompt = prompts(replacement)[0]
                assert prompt.startswith("[Authenticated Panels reply requirement]\n")
                assert prompt.count(NATIVE_WORKER_CONTEXT) == 1
                assert prompt.count(sender_text) == 1
                assert prompt.index("Chief:") < prompt.index(sender_text)

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


def test_exact_model_and_effort_cross_authenticated_native_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact model and effort cross the real lifecycle/TLS boundary without a catalog read."""
    import time

    from planner.conversation.message_content import MessageText
    from planner.runtime.conversation_start import (
        conversation_link_lock,
        send_to_ticket_conversation,
    )
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
    from planner.tickets import data
    from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale

    class CapturedNativeChild(ScriptedNativeChild):
        def __init__(self) -> None:
            super().__init__()
            self.delivered_text: list[str] = []
            self.emission_inputs: list[dict[str, object]] = []

        async def write_prompt(
            self, turn_token: TurnToken, content: MessageContent, **arguments: object
        ) -> BackendPromptAccepted:
            self.emission_inputs.append(arguments)
            self.delivered_text.append(
                "\n".join(piece.text for piece in content if isinstance(piece, MessageText))
            )
            return await super().write_prompt(turn_token, content, **arguments)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        conn = connect(harness.endpoint.db_path)
        try:
            second = CapturedNativeChild()

            def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
                del resolved

                def create(**arguments: object) -> BackendChild:
                    second.sink = cast(BackendEventSink, arguments["event_sink"])
                    return cast(BackendChild, second)

                return cast(BackendChildFactory, create)

            harness.mac.runner.factory = factory
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
            )
            from tests.support.executor_catalog import ScriptedBackendSnapshots

            observer = harness.mac.catalog.snapshots
            assert isinstance(observer, ScriptedBackendSnapshots)
            snapshots = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert snapshots.available_models and observer.calls > 0
            observer.calls = 0
            observer.block = True
            exact_text = "Exact lifecycle selection: café\nsecond line"
            async with conversation_link_lock(f"ticket:{ticket.id}"):
                delivery = await send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content(exact_text),
                    conversation_id=None,
                    created_conversation_id="c_combined_admission",
                    runs_under=ConversationStartOverrides(),
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
            selected = data.read_ticket(conn, ticket.id)
            assert selected.employee_launch_model == "scripted"
            assert selected.employee_backend == "codex"
            assert observer.calls == 0

            async def finish_turn() -> None:
                assert second.sink is not None and second.turn is not None
                await second.sink.report(
                    second.turn,
                    TurnEnded(
                        ending=ConversationTurnEnding.completed,
                        error_summary=None,
                        standard_error_tail=None,
                    ),
                )
                await eventually(
                    lambda: harness.system._conversations[conversation_id].phase.value == "idle",
                    "Workflow turn did not settle",
                )

            for index, effort in enumerate(("future-effort", None), 2):
                await finish_turn()
                text = f"Exact choice {index}"
                async with conversation_link_lock(f"ticket:{ticket.id}"):
                    changed = await send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content(text),
                        conversation_id=conversation_id,
                        runs_under=ConversationStartOverrides(
                            model="gpt-6.1-terra",
                            reasoning_effort=effort,
                            reasoning_effort_is_set=True,
                        ),
                        sender_label="Test",
                        sender=CHIEF_PRINCIPAL,
                        recipient=Principal(PrincipalKind.ticket, ticket.id),
                        now=int(time.time()),
                    )
                assert second.prompt_count == index, changed
                assert text in second.delivered_text[index - 1]
                assert observer.calls == 0
                assert second.emission_inputs[-1]["model_change"] == "gpt-6.1-terra"
                assert second.emission_inputs[-1]["reasoning_effort_change"] == effort
                assert second.emission_inputs[-1]["reasoning_effort_is_set"] is True
            assert harness.native.stop_count == 0
        finally:
            conn.close()
            await harness.close()

    asyncio.run(run())


def test_live_events_bypass_storage_and_completed_message_survives_link_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

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
            minted = []
            mint = harness.mac.outbox.add

            def observe_mint(*args: Any, **arguments: Any) -> QueuedEnvelope:
                envelope = mint(*args, **arguments)
                minted.append(envelope)
                return envelope

            monkeypatch.setattr(harness.mac.outbox, "add", observe_mint)
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                before = connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[
                    0
                ]
                with tail.subscribe("c_scripted") as watcher:
                    await sink.report(
                        replace(turn, turn_number=turn.turn_number + 1),
                        AgentMessageDelta(text_delta="Wrong turn"),
                    )
                    await sink.report(turn, AgentMessageDelta(text_delta="Live text"))
                    await sink.report(turn, ModelThinkingHappened())
                    await sink.report(turn, ToolCallProgress(tool_call_id="tool", detail="Working"))
                    assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                        text_delta="Live text"
                    )
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ModelThinkingFrame()
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ToolCallProgressFrame(
                        tool_call_id="tool", detail="Working"
                    )
                    assert not minted and not harness.mac.outbox.pending()
                    assert (
                        connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
                        == before
                    )
                    resume = await hold_reconnection(harness, monkeypatch)
                    await eventually(
                        lambda: harness.mac.connection.link is None, "Mac link stayed open"
                    )
                    await sink.report(turn, AgentMessageDelta(text_delta="Lost text"))
                    await sink.report(turn, ModelThinkingHappened())
                    await sink.report(
                        turn, ToolCallProgress(tool_call_id="tool", detail="Lost progress")
                    )
                    assert not minted and not harness.mac.outbox.pending()
                    await sink.report(
                        turn, AgentMessageCompleted(content=text_message_content("Full reply"))
                    )
                    pending = harness.mac.outbox.pending()
                    assert len(pending) == 1
                    resume.set()
                    await eventually(
                        lambda: not harness.mac.outbox.pending(), "Completed reply did not replay"
                    )
                    # Nothing shown while disconnected is replayed to live watchers.
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(watcher.next_item(), 0.05)
                    assert (
                        lookup_receipt(
                            harness.endpoint.inbox.store,
                            pending[0].sender_id,
                            pending[0].submission_id,
                        )
                        is not None
                    )
            await harness.system.kill("c_scripted")
            fate = await harness.system.send(
                "c_scripted", text_message_content("Start again"), sender_label="Test"
            )
            assert isinstance(fate, PromptDeliveryStarted)
            current_sink, current_turn = harness.native.sink, harness.native.turn
            assert current_sink is not None and current_turn is not None
            with tail.subscribe("c_scripted") as watcher:
                # Even a current turn token cannot give a retired child a destination.
                await sink.report(current_turn, AgentMessageDelta(text_delta="Retired child"))
                await current_sink.report(current_turn, AgentMessageDelta(text_delta="New child"))
                assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                    text_delta="New child"
                )
        finally:
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


def test_permanent_refusals_receive_durable_receipts_and_release_conversation_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.executor.protocol import CliWrite, as_object

        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            child = next(iter(harness.mac.runner._children.values())).reference
            native_child = next(iter(harness.mac.runner._children.values()))
            assert native_child.ticket_id is not None
            artifact = harness.mac.paths.artifacts / native_child.ticket_id / "queued.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("bytes published before a queued payload refusal")
            async with paused_outbox(harness.mac.outbox):
                malformed = harness.mac.outbox.add(
                    OutboxEntry(
                        conversation_id=child.conversation_id,
                        kind=EnvelopeKind.child_stopped,
                        payload={"child": as_object(child), "disposition": "unsupported"},
                    )
                )
                unassigned = harness.mac.outbox.add(
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
                assigned_refusal = harness.mac.outbox.add(
                    OutboxEntry(
                        conversation_id=child.conversation_id,
                        kind=EnvelopeKind.cli_write,
                        payload=as_object(CliWrite("ticket.unsupported", {}, child, None)),
                    ),
                    ticket_id=native_child.ticket_id,
                )
                assert assigned_refusal.file_dependencies
                await harness.native.raise_ask()
                following = harness.mac.outbox.pending()[-1]
                assert following.sequence == assigned_refusal.sequence + 1

            await eventually(
                lambda: not harness.mac.outbox.pending(),
                "Permanent refusal stranded the next item in the conversation",
            )
            # Only the last position remains on the VPS. The CLI refusal is retained
            # on the Mac for retry; obsolete sequences receive a plain acknowledgment.
            refused = harness.mac.outbox.lookup(unassigned.submission_id)
            assert refused is not None and refused.receipt is not None
            assert refused.receipt.disposition is ReceiptDisposition.refused
            error = refused.receipt.result["error"]
            assert isinstance(error, dict) and error["code"] == "agent_forbidden"
            assigned = harness.mac.outbox.lookup(assigned_refusal.submission_id)
            assert assigned is not None and assigned.receipt is not None
            assert assigned.receipt.disposition is ReceiptDisposition.refused
            assert "validation" in str(assigned.receipt.result)
            published = harness.endpoint.inbox.store.file_target(
                assigned_refusal.file_dependencies[0]
            )
            assert published.read_bytes() == artifact.read_bytes()
            for envelope in (malformed, unassigned, assigned_refusal):
                stale = await harness.endpoint.inbox.accept(envelope, harness.mac.identity)
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
            accepted = lookup_receipt(
                harness.endpoint.inbox.store, following.sender_id, following.submission_id
            )
            assert accepted is not None and accepted.disposition is ReceiptDisposition.accepted
            assert harness.events("permission_asked") == 1
            await asyncio.wait_for(harness.system.kill(child.conversation_id), 5)
            assert harness.endpoint.runs.store.child_assignment(child) is None
            assert all(run.retirement_confirmed for run in harness.endpoint.runs.retained_runs())
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("receipt_failure", ["disconnect", "silent"])
def test_commit_before_ack_replay_returns_first_receipt_and_one_domain_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_failure: str,
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            if receipt_failure != "disconnect":
                original_send = harness.endpoint.runs.link._stream.send

                async def fail_receipt(message: WireMessage) -> None:
                    if (
                        message.kind is WireMessageKind.receipt
                        and message.payload.get("submission_id") == harness.endpoint.cut_submission
                    ):
                        harness.endpoint.cut_submission = None
                        harness.endpoint.cut_count += 1
                        return
                    await original_send(message)

                monkeypatch.setattr(harness.endpoint.runs.link._stream, "send", fail_receipt)
                if receipt_failure == "silent":
                    monkeypatch.setattr("planner.executor.link.REQUEST_TIMEOUT_SECONDS", 0.2)
                    writer = harness.mac.connection.link
                    assert writer is not None
                    close = writer.close

                    def restore_budget_and_close() -> None:
                        # Accelerate only the missing receipt, not the fresh
                        # generation's real SSH and reconciliation setup.
                        monkeypatch.setattr("planner.executor.link.REQUEST_TIMEOUT_SECONDS", 30.0)
                        close()

                    monkeypatch.setattr(writer, "close", restore_budget_and_close)
            async with paused_outbox(harness.mac.outbox):
                await harness.native.raise_ask()
                pending = [
                    entry
                    for entry in harness.mac.outbox.pending()
                    if entry.kind == EnvelopeKind.backend_event
                ]
                assert len(pending) == 1
                harness.endpoint.cut_submission = pending[0].submission_id
            await eventually(
                lambda: harness.endpoint.cut_count == 1, "Receipt cut was not exercised"
            )
            first_receipt = lookup_receipt(
                harness.endpoint.inbox.store, pending[0].sender_id, pending[0].submission_id
            )
            assert first_receipt is not None
            assert first_receipt.payload_sha256 == pending[0].payload_sha256
            await eventually(
                lambda: not harness.mac.outbox.pending() and harness.endpoint.runs.ready,
                "Committed entry did not replay and acknowledge",
            )
            assert harness.events("permission_asked") == 1
            assert harness.native.answers == []
            receipt = lookup_receipt(
                harness.endpoint.inbox.store, pending[0].sender_id, pending[0].submission_id
            )
            assert receipt == first_receipt

            async def duplicate_effect(*args: object, **kwargs: object) -> None:
                raise AssertionError("duplicate submission invoked backend event writer")

            monkeypatch.setattr(
                harness.endpoint.conversations, "take_backend_event", duplicate_effect
            )
            assert (
                await harness.endpoint.inbox.accept(pending[0], harness.mac.identity)
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
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
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
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    from planner.conversation.contracts import PromptDeliveryUncertain
    from planner.executor.protocol import ChildCommand, ChildOperation, CommandOutcome

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        release = asyncio.Event()
        late_delivery: asyncio.Task[PromptDeliveryFate] | None = None
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            ask = harness.ask
            old_turn = harness.native.turn
            old_mac = harness.mac
            reached = asyncio.Event()
            send_command = harness.endpoint.runs.send_command

            async def hold_command(command: ChildCommand) -> CommandOutcome:
                if command.operation is ChildOperation.steer:
                    reached.set()
                    await release.wait()
                return await send_command(command)

            monkeypatch.setattr(harness.endpoint.runs, "send_command", hold_command)
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
            assert harness.endpoint.runs.link is not None
            harness.endpoint.runs.link.close()
            await asyncio.wait_for(harness.task, 3)

            # Model service death with a fresh registry and incarnation. This
            # does not prove launchd terminates real provider process groups.
            replacement = MacService(
                paths=old_mac.paths,
                revision=old_mac.identity.application_revision,
                executor_id=old_mac.executor_id,
                panels_server_url=harness.proxy.origin,
                session_factory=harness.proxy.session_factory,
            )
            replacement.catalog.snapshots = ScriptedBackendSnapshots(lambda: replacement.identity)
            replacement.runner.factory = old_mac.runner.factory
            harness.mac = replacement
            harness.task = asyncio.create_task(replacement.run())
            await eventually(
                lambda: harness.endpoint.runs.identity == replacement.identity,
                "Replacement hello did not reach the VPS",
            )
            release.set()
            assert isinstance(await asyncio.wait_for(late_delivery, 3), PromptDeliveryUncertain)
            await eventually(
                lambda: (
                    harness.endpoint.runs.ready
                    and replacement.connection.state is ConnectionState.ready
                ),
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
            assert harness.endpoint.runs.store.has_closed_child_assignment(ask.child)
            assert not harness.answer_applied(ask)
            state = harness.system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            assert state.retained_child_reference is None
            assert harness.native.start_count == harness.native.prompt_count == 1
            delivery = await harness.system.send(
                "c_scripted", text_message_content("Fresh"), sender_label="Test"
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            assert harness.native.start_count == harness.native.prompt_count == 2
            assert harness.native.turn != old_turn
            fresh_child = next(iter(replacement.runner._children.values())).reference
            assert fresh_child.executor_incarnation != ask.child.executor_incarnation
            assert harness.events("prompt_delivery_uncertain") == 1
        finally:
            release.set()
            if late_delivery is not None:
                await asyncio.gather(late_delivery, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_vps_input_attachment_bytes_arrive_before_native_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile, MessageText
    from planner.executor.link_files import GenerationTransfers
    from planner.executor.protocol import ChildCommand

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            generation = harness.mac.identity.connection_generation
            monkeypatch.setattr("planner.executor.link.REQUEST_TIMEOUT_SECONDS", 0.15)
            emitted: list[ChildCommand] = []
            link = harness.endpoint.runs.link
            tell = link.tell

            async def record_command(
                kind: WireMessageKind,
                payload: JsonObject,
                *,
                request_id: str | None = None,
                admit: Callable[[], bool] | None = None,
            ) -> bool:
                result = await tell(kind, payload, request_id=request_id, admit=admit)
                if result and kind is WireMessageKind.command:
                    emitted.append(TypeAdapter(ChildCommand).validate_python(payload))
                return result

            monkeypatch.setattr(link, "tell", record_command)
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
            kept = await harness.endpoint.message_files.keep(
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
            assert harness.endpoint.runs.link is not None
            control = await harness.endpoint.runs.link.request(
                WireMessageKind.catalog_request, {"backend_key": "codex", "refresh": False}
            )
            assert control.kind is WireMessageKind.catalog_response
            result = await asyncio.wait_for(sending, 5)
            assert harness.native.prompt_count == 2, repr(result)
            assert asyncio.get_running_loop().time() - started > 0.15
            assert harness.mac.identity.connection_generation == generation
            assert native_path.read_bytes() == contents
            assert len(emitted) == 1
            assert harness.mac.runner._commands[emitted[0].command_id].command == emitted[0]
        finally:
            await harness.close()

    asyncio.run(run())


def test_input_transfer_drop_refuses_stale_context_then_explicit_send_delivers_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from planner.conversation.message_content import MessageFile, MessageText

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.message_files.keep(
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
            generation = harness.mac.identity.connection_generation
            harness.endpoint.cut_input = True
            fate = await asyncio.wait_for(
                harness.system.send("c_scripted", content, sender_label="Test"), 8
            )
            assert isinstance(fate, PromptDeliveryRefused), repr(fate)
            assert fate.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
            assert harness.endpoint.input_cut_count == 1
            assert harness.mac.identity.connection_generation > generation
            assert harness.native.prompt_count == 1
            # The first operation is definitively refused. This separate user send
            # can deliver after reconnect without replaying the refused command.
            submitted = await asyncio.wait_for(
                harness.system.send("c_scripted", content, sender_label="Test"), 8
            )
            assert isinstance(submitted, PromptDeliveryStarted), repr(submitted)
            assert harness.native.prompt_count == 2
            assert native_path.read_bytes() == contents
        finally:
            await harness.close()

    asyncio.run(run())


def test_kill_stalled_initial_input_fences_reconnect_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile
    from planner.executor.protocol import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        entered, cleanup = asyncio.Event(), asyncio.Event()
        sending = None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "first turn did not end")
            original = next(iter(harness.mac.runner._children.values())).reference
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", b"input bytes", media_type="application/octet-stream"
            )
            prepare = harness.mac.runner._prepare_inputs
            command_id = ""

            async def stalled(command: ChildCommand) -> None:
                nonlocal command_id
                await prepare(command)
                if command.operation is ChildOperation.write_prompt:
                    command_id = command.command_id
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await cleanup.wait()

            monkeypatch.setattr(harness.mac.runner, "_prepare_inputs", stalled)
            sending = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageFile(
                            kept.stored_file_id, "application/octet-stream", "input.bin", 11
                        ),
                    ),
                    sender_label="Test",
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            assert harness.mac.runner.ask((command_id,))[0].phase == "preparing"
            # Reconnect observes the same preparation; it does not replay the provider.
            generation = harness.mac.identity.connection_generation
            assert harness.endpoint.runs.link is not None
            harness.endpoint.runs.link.close()
            await eventually(
                lambda: (
                    harness.endpoint.runs.ready
                    and harness.mac.identity.connection_generation > generation
                ),
                "link did not return",
            )
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            fate = await asyncio.wait_for(sending, 3)
            assert isinstance(fate, PromptDeliverySuppressed)
            assert harness.native.prompt_count == 1
            cleanup.set()
            await asyncio.wait_for(killing, 5)
            await eventually(
                lambda: original.child_id not in harness.mac.runner._children,
                "original child did not stop",
            )
            assert harness.native.stop_count == 1 and harness.native.prompt_count == 1
            assert not harness.mac.runner._commands[command_id].invoking
        finally:
            cleanup.set()
            if sending is not None and not sending.done():
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


INPUT_ANSWERS = (UserInputAnswer("q_two", ("Red", "Blue")), UserInputAnswer("q_one", ("Yes",)))
ORDERED_INPUT_ANSWERS = (INPUT_ANSWERS[1], INPUT_ANSWERS[0])


def test_user_input_callback_application_survives_lost_receipt_without_reanswer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.api import ConversationRuntime, _pending_user_input
    from planner.conversation.backend_usage import BackendUsageService
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.snapshot import BackendSnapshotService

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
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
            )
            pending = await _pending_user_input(runtime, "c_scripted")
            assert pending is not None
            assert len(pending["questions"]) == 2
            generation = harness.mac.identity.connection_generation
            harness.endpoint.cut_user_input_application = True
            assert (
                await harness.system.answer_user_input(
                    "c_scripted",
                    public_ask_id(harness.input_ask),
                    INPUT_ANSWERS,
                )
            ) is AnswerFate.saved
            await eventually(
                lambda: harness.endpoint.cut_count == 1,
                "input application acknowledgment was not cut",
            )
            await eventually(
                lambda: (
                    harness.mac.identity.connection_generation > generation
                    and harness.endpoint.runs.ready
                ),
                "input application did not reconcile",
            )
            await eventually(
                lambda: not harness.mac.outbox.pending(), "input application replay did not drain"
            )
            assert harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)]
            assert harness.events("user_input_answered") == 1
            replacement = (UserInputAnswer("q_one", ("No",)), UserInputAnswer("q_two", ("Blue",)))
            assert (
                await harness.system.answer_user_input(
                    "c_scripted",
                    public_ask_id(harness.input_ask),
                    replacement,
                )
            ) is AnswerFate.refused
            assert harness.events("user_input_answered") == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_refuses_previous_protocol_before_replay_without_fencing_current_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from planner.execution_identity import ExecutorIdentity
    from planner.executor.link import ExecutorAppClient
    from planner.executor.protocol import ExecutorHealth

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            identity = harness.endpoint.runs.identity
            assert identity is not None
            connection = connect(harness.endpoint.db_path)
            before = connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            ws_context = client.websocket("/api/executor/connect")
            stream = await ws_context.__aenter__()
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
            assert await asyncio.wait_for(stream.receive_bytes(), 1) == b""
            await ws_context.__aexit__(None, None, None)
            await client.close()
            assert harness.endpoint.runs.identity == identity and harness.endpoint.runs.ready
            assert (
                connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
                == before
            )
            connection.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("invalid_ack", ["mismatched", "traversal"])
def test_artifact_descriptor_must_validate_before_publication_and_backend_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_ack: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            reject = True
            rejected = asyncio.Event()
            refusals: list[AcceptanceReceipt] = []
            generation, writer = harness.mac.identity, harness.endpoint.runs.link
            send = harness.endpoint.runs.link._stream.send

            async def corrupt_ack(message: WireMessage) -> None:
                if reject and message.kind is WireMessageKind.file_transfer:
                    payload = dict(message.payload)
                    if invalid_ack == "mismatched":
                        payload["direction"] = "download"
                    elif invalid_ack == "traversal":
                        payload["transfer_id"] = "../escape"
                    message = WireMessage(
                        message.kind,
                        message.request_id,
                        payload,
                    )
                await send(message)
                if (
                    message.kind is WireMessageKind.receipt
                    and message.payload.get("disposition") == ReceiptDisposition.refused
                ):
                    refusals.append(TypeAdapter(AcceptanceReceipt).validate_python(message.payload))
                    rejected.set()

            monkeypatch.setattr(harness.endpoint.runs.link._stream, "send", corrupt_ack)
            ticket_id = next(iter(harness.mac.runner._children.values())).ticket_id
            assert ticket_id is not None
            artifact = harness.mac.paths.artifacts / ticket_id / "typed-ack.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            contents = bytes(range(256)) * 2000
            artifact.write_bytes(contents)
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                ToolCallFinished(
                    tool_call_id="typed_ack",
                    tool_call_status=ToolCallStatus.completed,
                    detail="The file needs an authorized upload slot.",
                ),
            )
            await asyncio.wait_for(rejected.wait(), 8)
            target = tmp_path / "vps/files/tickets" / ticket_id / "typed-ack.bin"
            assert not target.exists()
            assert harness.events("tool_call_finished") == 0
            assert len(refusals) == 1
            assert (
                lookup_receipt(
                    harness.endpoint.inbox.store, refusals[0].sender_id, refusals[0].submission_id
                )
                == refusals[0]
            )
            await eventually(lambda: not harness.mac.outbox.pending(), "Refused item did not drain")
            assert all(not service.uploads for service in harness.endpoint.transfers.values())
            assert artifact.read_bytes() == contents
            assert harness.mac.identity == generation and harness.endpoint.runs.link is writer
            assert harness.endpoint.runs.link is not None
            control = await harness.endpoint.runs.link.request(
                WireMessageKind.catalog_request, {"backend_key": "codex", "refresh": False}
            )
            assert control.kind is WireMessageKind.catalog_response
            reject = False
            await harness.native.sink.report(
                harness.native.turn,
                ToolCallFinished(
                    tool_call_id="corrected_descriptor",
                    tool_call_status=ToolCallStatus.completed,
                    detail="The next item uses a valid descriptor.",
                ),
            )
            await eventually(
                lambda: harness.events("tool_call_finished") == 1,
                "Corrected artifact acknowledgment did not allow the dependent event",
            )
            assert target.read_bytes() == contents
            await eventually(
                lambda: not harness.mac.outbox.pending(), "Artifact receipt did not drain"
            )
        finally:
            await harness.close()

    asyncio.run(run())


def test_input_descriptor_must_validate_before_provider_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.message_content import MessageFile
    from planner.executor.protocol import ChildCommand, CommandDisposition, CommandOutcome

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            send = harness.endpoint.runs.link._stream.send

            async def corrupt(message: WireMessage) -> None:
                if (
                    message.kind is WireMessageKind.file_transfer
                    and message.payload.get("direction") == "download"
                ):
                    payload = dict(message.payload)
                    payload["direction"] = "upload"
                    message = WireMessage(
                        message.kind,
                        message.request_id,
                        payload,
                    )
                await send(message)

            monkeypatch.setattr(harness.endpoint.runs.link._stream, "send", corrupt)
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", b"input bytes", media_type="application/octet-stream"
            )
            outcomes: list[CommandOutcome] = []
            send_command = harness.endpoint.runs.send_command

            async def observe(command: ChildCommand) -> CommandOutcome:
                outcome = await send_command(command)
                outcomes.append(outcome)
                return outcome

            monkeypatch.setattr(harness.endpoint.runs, "send_command", observe)
            result = await asyncio.wait_for(
                harness.system.send(
                    "c_scripted",
                    (
                        MessageFile(
                            kept.stored_file_id, "application/octet-stream", "input.bin", 11
                        ),
                    ),
                    sender_label="Owner",
                ),
                5,
            )
            assert isinstance(result, PromptDeliveryRefused)
            assert len(outcomes) == 1 and outcomes[0].disposition is CommandDisposition.refused
            assert harness.native.prompt_count == 1
            target = (
                harness.mac.paths.root
                / "artifacts/files/conversations/c_scripted"
                / kept.stored_file_id
            )
            assert not target.exists()
            assert harness.endpoint.runs.link is not None
            control = await harness.endpoint.runs.link.request(
                WireMessageKind.catalog_request, {"backend_key": "codex", "refresh": False}
            )
            assert control.kind is WireMessageKind.catalog_response
        finally:
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
    assert harness.endpoint.runs.link is not None
    harness.endpoint.runs.link.close()
    await eventually(lambda: not harness.endpoint.runs.ready, "link did not disconnect")
    return resume


async def post_owner_answer(harness: ExecutorHarness, ask: AskReference) -> dict[str, str]:
    import httpx
    from fastapi import FastAPI

    from planner.conversation.api import _runtime, router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    runtime = SimpleNamespace(
        system=harness.system,
        database_path=harness.endpoint.db_path,
    )
    app.dependency_overrides[_runtime] = lambda: runtime
    body: dict[str, Any] = {}
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
    return cast(dict[str, str], response.json())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_saved_answer_after_vps_restart_remains_unapplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
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
            assert await post_owner_answer(harness, ask) == {"fate": "saved"}
            answer = harness.saved_answer(ask)
            assert answer is not None
            assert harness.endpoint.runs.pending_commands == {}
            assert harness.native.answers == [] and harness.native.user_answers == []
            await harness.restart_vps_core(wait_ready=False)
            assert not harness.endpoint.runs.children
            assert await post_owner_answer(harness, ask) == {"fate": "refused"}
            assert harness.saved_answer(ask) == answer
            assert harness.endpoint.runs.pending_commands == {}
            resume.set()
            await eventually(lambda: harness.native.stop_count == 1, "old child was not stopped")
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: not state.child_is_quarantined, "stop proof did not settle")
            assert harness.native.answers == [] and harness.native.user_answers == []
            assert not harness.answer_applied(ask)
            assert harness.saved_answer(ask) == answer
            assert await post_owner_answer(harness, ask) == {"fate": "refused"}
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
            request = Link.tell

            async def pause_ready(
                self: Link, message_kind: WireMessageKind, payload: JsonObject, **kwargs: Any
            ) -> bool:
                if message_kind is WireMessageKind.reconciliation_ready:
                    reached.set()
                    await ready_release.wait()
                return await request(self, message_kind, payload, **kwargs)

            monkeypatch.setattr(Link, "tell", pause_ready)
            resume.set()
            await asyncio.wait_for(reached.wait(), 5)
            assert not harness.endpoint.runs.ready
            assert await post_owner_answer(harness, ask) == {"fate": "saved"}
            assert harness.endpoint.runs.pending_commands == {}
            ready_release.set()
            event = "answer_applied"
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
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
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
            assert await post_owner_answer(harness, ask) == {"fate": "saved"}
            answer = harness.saved_answer(ask)

            async def stalled_cancel(*args: object) -> None:
                cancel_entered.set()
                await native_cancel.wait()

            monkeypatch.setattr(harness.system, "_cancel_child_turn", stalled_cancel)
            old_system = harness.system
            interrupt = asyncio.create_task(old_system.interrupt("c_scripted"))
            await asyncio.wait_for(cancel_entered.wait(), 2)
            assert not harness.answer_applied(ask)
            state = old_system._conversations["c_scripted"]
            assert state.running_turn is not None and state.running_turn.callbacks_revoked
            # A callback announced after revocation cannot create a newly answerable ask.
            await harness.native.raise_user_input("request_late")
            # Simulate a crash without permitting the pending native cancellation to finish.
            state = old_system._conversations["c_scripted"]
            for settlement in tuple(state.settlement_tasks):
                settlement.cancel()
            await asyncio.gather(*tuple(state.settlement_tasks), return_exceptions=True)
            await asyncio.gather(interrupt, return_exceptions=True)
            await harness.restart_vps_core(wait_ready=False)
            assert await post_owner_answer(harness, ask) == {"fate": "refused"}
            assert harness.saved_answer(ask) == answer
            resume.set()
            await eventually(lambda: harness.endpoint.runs.ready, "revoked turn did not reconcile")
            await eventually(
                lambda: not harness.mac.outbox.pending(), "late callback did not drain"
            )
            assert harness.native.answers == [] and harness.native.user_answers == []
            assert harness.events("answer_applied") == 0
            assert not await harness.system.has_pending_user_input("c_scripted")
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: not state.child_is_quarantined, "restart stop did not settle")
            assert harness.native.stop_count == 1
            assert harness.endpoint.runs.pending_commands == {}
            assert not harness.answer_applied(ask)
            assert not await harness.system.is_running("c_scripted")
        finally:
            native_cancel.set()
            if interrupt is not None and not interrupt.done():
                interrupt.cancel()
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())


def test_stop_waits_for_queued_receipt_after_direct_result_and_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.protocol import ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, reconnect_interval=0.02)
        stopping: asyncio.Task[None] | None = None
        direct_result = asyncio.Event()
        assert harness.mac.connection.link is not None
        writer = harness.mac.connection.link._stream
        send = writer.send

        async def observe(message: WireMessage) -> None:
            await send(message)
            if (
                message.kind is WireMessageKind.command_outcome
                and message.request_id is not None
            ):
                pending = harness.endpoint.runs.pending_commands.get(message.request_id)
                if pending is not None and pending.command.operation is ChildOperation.stop:
                    direct_result.set()

        monkeypatch.setattr(writer, "send", observe)
        try:
            # Prove the idle child's StopRun receipt across the replacement connection.
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await eventually(lambda: harness.events("turn_ended") == 1, "initial turn did not end")
            await eventually(
                lambda: harness.system._conversations["c_scripted"].running_turn is None,
                "initial runtime turn did not settle",
            )
            assert not await harness.system.is_running("c_scripted")
            await eventually(
                lambda: not harness.mac.outbox.pending(), "initial events did not drain"
            )
            async with paused_outbox(harness.mac.outbox):
                stopping = asyncio.create_task(harness.system.kill("c_scripted"))
                await asyncio.wait_for(direct_result.wait(), 5)
                assert not stopping.done()
                generation = harness.mac.identity.connection_generation
                assert harness.endpoint.runs.link is not None
                harness.endpoint.runs.link.close()
                await eventually(
                    lambda: (
                        harness.mac.identity.connection_generation > generation
                        and harness.endpoint.runs.identity == harness.mac.identity
                        and harness.endpoint.runs.ready
                    ),
                    "stop did not reconcile on the replacement connection",
                )
                assert not stopping.done()
                assert harness.native.stop_count == 1
            await asyncio.wait_for(stopping, 5)
            await eventually(lambda: not harness.mac.outbox.pending(), "stop receipt did not drain")
            assert not await harness.system.is_running("c_scripted")
            assert harness.native.stop_count == 1
        finally:
            if stopping is not None and not stopping.done():
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_shutdown_waits_for_admitted_outcome_application_and_final_event_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.protocol import ChildOperation, CommandOutcome

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        release_application, release_event = threading.Event(), asyncio.Event()
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
            stop_exact = harness.mac.runner._stop_exact_child
            publish = harness.mac.outbox.add
            loop = asyncio.get_running_loop()
            native_stop = harness.native.stop
            accept = harness.endpoint.inbox.accept
            resolve = harness.endpoint.runs._resolve_command

            async def observe_native_stop(command: Any) -> CommandOutcome:
                stopping.set()
                return await stop_exact(command)

            def late_application(
                entry: OutboxEntry, *, ticket_id: str | None = None
            ) -> QueuedEnvelope:
                if entry.kind is EnvelopeKind.answer_application:
                    loop.call_soon_threadsafe(application_waiting.set)
                    assert release_application.wait(10), "callback publication was not released"
                return publish(entry, ticket_id=ticket_id)

            async def final_native_event() -> None:
                assert harness.native.sink is not None
                await harness.native.sink.report(
                    None, VendorSessionCursorRebound(vendor_session_cursor="cleanup-cursor")
                )
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

            def observed_hint(outcome: CommandOutcome, *, stop_proven: bool = False) -> None:
                pending = harness.endpoint.runs.pending_commands.get(outcome.command_id)
                if pending is not None and pending.command.operation is ChildOperation.stop:
                    stop_hint.set()
                resolve(outcome, stop_proven=stop_proven)

            async def cancel_native() -> None:
                pass

            monkeypatch.setattr(harness.mac.runner, "_stop_exact_child", observe_native_stop)
            monkeypatch.setattr(harness.mac.outbox, "add", late_application)
            monkeypatch.setattr(harness.native, "cancel_running_turn", cancel_native, raising=False)
            monkeypatch.setattr(harness.native, "stop", final_native_event)
            monkeypatch.setattr(harness.endpoint.inbox, "accept", withheld_event)
            monkeypatch.setattr(harness.endpoint.runs, "_resolve_command", observed_hint)
            assert await post_owner_answer(harness, harness.ask) == {"fate": "saved"}
            await asyncio.wait_for(application_waiting.wait(), 5)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            await asyncio.wait_for(stopping.wait(), 5)
            assert not event_waiting.is_set() and not stop_hint.is_set() and not killing.done()
            assert not stop_hint.is_set() and not killing.done()
            release_application.set()
            await asyncio.wait_for(event_waiting.wait(), 5)
            await asyncio.wait_for(stop_hint.wait(), 5)
            stops = [
                (pending.command, pending.future)
                for pending in harness.endpoint.runs.pending_commands.values()
                if pending.command.operation is ChildOperation.stop
            ]
            assert stops and all(not future.done() for _, future in stops)
            assert harness.endpoint.runs.store.child_assignment(reference) is not None
            assert not killing.done()
            release_event.set()
            await asyncio.wait_for(killing, 5)
            await eventually(lambda: not harness.mac.outbox.pending(), "cleanup FIFO did not drain")
            assert harness.endpoint.runs.store.child_assignment(reference) is None
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
                    receipt = lookup_receipt(
                        harness.endpoint.inbox.store, row.sender_id, row.submission_id
                    )
                    assert receipt is None  # The later stop has advanced the single inbox position.
                assert (
                    connection.execute(
                        "SELECT stop_state FROM mac_runs WHERE conversation_id=?",
                        (reference.conversation_id,),
                    ).fetchone()[0]
                    == "closed"
                )
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
    """A timed-out machine read preserves the live child and the offline saved list."""
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    from planner.executor import catalog_relay

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            source = harness.mac.catalog.snapshots
            assert isinstance(source, ScriptedBackendSnapshots)
            for backend in ConversationBackendKey:
                listed = await harness.endpoint.catalog.snapshot(backend, refresh=True)
                assert listed.available_models[0].model_id == source.model
            first = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert first.available_models[0].model_id == source.model
            assert first.default_model_id == source.model
            assert first.default_reasoning_effort == "medium"
            writer = harness.endpoint.runs.link
            starts = harness.native.start_count
            prompts = harness.native.prompt_count
            source.block = True
            with monkeypatch.context() as timeout_patch:
                timeout_patch.setattr(catalog_relay, "CATALOG_TIMEOUT_SECONDS", 0.05)
                retained = await harness.endpoint.catalog.snapshot(
                    ConversationBackendKey.codex, refresh=True
                )
            assert retained == first
            assert harness.endpoint.runs.link is writer and harness.endpoint.runs.ready
            assert harness.native.start_count == starts
            assert harness.native.prompt_count == prompts
            source.block = False
            source.model = "reconnected-model"
            assert writer is not None
            writer.close()
            await eventually(
                lambda: (
                    harness.endpoint.runs.ready
                    and harness.endpoint.runs.link is not writer
                    and all(
                        (saved := harness.endpoint.catalog.retained(backend)) is not None
                        and saved.available_models[0].model_id == "reconnected-model"
                        for backend in ConversationBackendKey
                    )
                ),
                "Reconnect did not refresh the machine lists",
            )
            first = await harness.endpoint.catalog.snapshot(ConversationBackendKey.codex)
            # A new Mac process may have only an incomplete first read. The VPS
            # still owns the last complete list across that process boundary.
            source.complete = False
            source.model = "partial-model"
            partial = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert partial.available_models == first.available_models
            assert partial.default_model_id == first.default_model_id
            # A new VPS reader has only SQLite, no in-memory copy or connected Mac.
            from planner.executor.catalog_relay import VpsCatalogRelay
            from planner.executor.vps_runs import MacRuns

            another_mac = VpsCatalogRelay(MacRuns(harness.endpoint.db_path), machine_id="other-mac")
            assert not (await another_mac.snapshot(ConversationBackendKey.codex)).available_models
            offline = VpsCatalogRelay(MacRuns(harness.endpoint.db_path))
            saved = await offline.snapshot(ConversationBackendKey.codex)
            assert saved.available_models == first.available_models
            assert saved.default_model_id == first.default_model_id
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["unknown", "preparing", "invoking", "finished"])
def test_reconnect_asks_once_and_resends_only_unknown_command_with_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Real TLS reply loss and in-flight completion need both readers and native ownership."""
    from planner.executor.protocol import ChildCommand, ChildOperation

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
                "reconnect-proof",
                reference,
                ChildOperation.cancel_running_turn,
                None,
                decode_command_arguments(ChildOperation.cancel_running_turn, {}),
            )
            assert harness.mac.connection.link is not None
            mac_send = harness.mac.connection.link._stream.send
            # Commands use the shared send lock directly; observe at the writer boundary.
            writer = harness.endpoint.runs.link
            assert writer is not None
            original_command = harness.mac._command
            dropped = False

            async def invoke() -> None:
                nonlocal attempts
                attempts += 1
                if phase == "invoking":
                    observed.set()
                    await release.wait()

            target_command_id = command.command_id

            async def receive(command: ChildCommand, link: Link) -> None:
                nonlocal dropped
                if command.command_id == target_command_id:
                    sends.append(command.command_id)
                    if phase == "unknown" and not dropped:
                        dropped = True
                        observed.set()
                        return
                await original_command(command, link)

            async def lose_result(message: WireMessage) -> None:
                if message.kind is WireMessageKind.command_outcome and (
                    message.request_id == command.command_id and phase == "finished"
                ):
                    observed.set()
                    return
                await mac_send(message)

            request = Link.request

            async def ask(
                self: Link, kind: WireMessageKind, payload: JsonObject, **kwargs: Any
            ) -> WireMessage:
                if kind is WireMessageKind.reconnect_ask:
                    asks.append(payload)
                return await request(self, kind, payload, **kwargs)

            monkeypatch.setattr(harness.native, "cancel_running_turn", invoke, raising=False)
            monkeypatch.setattr(harness.mac, "_command", receive)
            monkeypatch.setattr(harness.mac.connection.link._stream, "send", lose_result)
            monkeypatch.setattr(Link, "request", ask)
            if phase == "preparing":
                locked = harness.mac.runner._children[reference.child_id].lock
                await locked.acquire()
            caller = asyncio.create_task(harness.endpoint.runs.send_command(command))
            if phase == "preparing":
                await eventually(
                    lambda: harness.mac.runner.ask((command.command_id,))[0].phase == "preparing",
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
                    and harness.endpoint.runs.ready
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
            assert harness.mac.runner.ask((command.command_id,))[0].outcome == outcome
            assert not harness.mac.outbox.pending()
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
        resume = None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            async with paused_outbox(harness.mac.outbox):
                await harness.native.raise_ask()
                queued = harness.mac.outbox.pending()
                assert len(queued) == 1
                generation = harness.mac.identity.connection_generation
                resume = await hold_reconnection(harness, monkeypatch)
                await eventually(
                    lambda: harness.mac.connection.lost_at_ms is not None,
                    "Mac did not record the current connection loss",
                )
                loss = harness.mac.connection.lost_at_ms
                from planner.executor import mac_service
                from planner.executor.link import dial_link

                dial = dial_link
                failed_attempts = 0

                @contextlib.asynccontextmanager
                async def interrupted_dial(*args: Any, **kwargs: Any) -> AsyncIterator[Link]:
                    nonlocal failed_attempts
                    assert harness.mac.connection.health().lost_at_ms == loss
                    if failed_attempts == 0:
                        failed_attempts += 1
                        raise OSError("scripted connection refusal during the outage")
                    async with dial(*args, **kwargs) as link:
                        assert harness.mac.connection.health().lost_at_ms == loss
                        yield link

                monkeypatch.setattr(mac_service, "dial_link", interrupted_dial)
                resume.set()
                await eventually(
                    lambda: (
                        harness.mac.identity.connection_generation > generation
                        and harness.endpoint.runs.ready
                    ),
                    "Ready waited for outbox replay",
                )
                assert failed_attempts == 1
                assert harness.mac.outbox.pending() == queued
                assert harness.events("permission_asked") == 0
            await harness.wait_ask_reachable()
            await eventually(
                lambda: not harness.mac.outbox.pending(), "Retained output did not drain"
            )
            receipt = lookup_receipt(
                harness.endpoint.inbox.store, queued[0].sender_id, queued[0].submission_id
            )
            assert receipt is not None and receipt.result.get("applied") is True
            assert await harness.endpoint.inbox.accept(queued[0], harness.mac.identity) == receipt
            assert harness.mac.connection.lost_at_ms is None
            assert harness.events("permission_asked") == 1
        finally:
            if resume is not None:
                resume.set()
            await harness.close()

    asyncio.run(run())
