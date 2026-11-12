"""Real Mac/VPS dispatch over the HTTPS mailboxes, through the isolated TLS proxy.

Risk: a conversation guarantee that holds for each executor part alone but not once
they run together over the wire: a prompt, answer or stop that runs twice or never
after a dropped request, an event applied twice after a lost receipt, a file that
reaches the agent unverified, a Mac run lost across a VPS restart, or a replaced Mac
process whose replayed output is refused.
Gap: the unit tests prove each part against a stub of the other side (C's scripted VPS,
B's typed adapters without a Mac). Only this file runs the real MacService, the real
VpsExecutor and the real conversation core together, across uvicorn and TLS.
Layer: integration. One uvicorn behind the isolated test proxy
(tests/support/executor_https), real SQLite on both sides, a scripted native child
that never calls a provider (the Claude case uses the scripted SDK client). Drops are
real: HttpsProxy.cut_connections() aborts every open connection, and blackhole() holds
requests with no reply, as a link that went away does.
Setup reused: boot, ExecutorHarness, ScriptedNativeChild and CutReceiptEndpoint, which
the isolation and stall alarm tests reuse too.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import ssl
import threading
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
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
    BackendSteerAccepted,
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
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageText,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.execution_identity import AskKind, AskReference, ExecutorIdentity
from planner.executor.configuration import ExecutorPaths
from planner.executor.mac_outbox import OutboxEntry
from planner.executor.mac_service import MacService
from planner.executor.protocol import (
    AcceptanceReceipt,
    ChildOperation,
    CommandOutcome,
    DurableAnswer,
    EnvelopeKind,
    FileUploadRequest,
    IpcOperation,
    IpcRequest,
    ItemBatch,
    ItemBatchReply,
    MailboxPollReply,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
    type_adapter,
)
from planner.executor.vps_files import InputFile
from planner.executor.vps_transport import VpsExecutor

pytestmark = pytest.mark.usefixtures("executor_transport_home")

EXECUTOR_ID = "test-executor"


class ScriptedNativeChild:
    def __init__(self) -> None:
        self.sink: BackendEventSink | None = None
        self.turn: TurnToken | None = None
        self.start_count = 0
        self.prompt_count = 0
        self.stop_count = 0
        self.cancel_count = 0
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

    async def cancel_running_turn(self) -> None:
        # As a real adapter does. A cancel that fails retires the child with a second
        # stop of its own, which races the caller's stop (see part-f2-notes).
        self.cancel_count += 1

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

    async def end_turn(self) -> None:
        assert self.sink is not None and self.turn is not None
        await self.sink.report(
            self.turn,
            TurnEnded(
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            ),
        )


class CutReceiptEndpoint(VpsExecutor):
    """The real VPS half, able to lose one reply after its real commit.

    A cut aborts every connection through the proxy (HttpsProxy.cut_connections), so
    the Mac's request gets no reply, as when the link drops at that moment. Cuts land
    on POSTs and the long poll, where the drop must be seen: aiohttp silently resends an
    idempotent GET once on a reused connection.
    """

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, RealClock())
        self.proxy: HttpsProxy | None = None
        # Route 4: the reply that carries this submission's receipt.
        self.cut_submission: str | None = None
        self.cut_count = 0
        self.cut_submissions: list[str] = []
        # Every route-4 item the VPS was sent, by submission, resends included.
        self.seen_submissions: Counter[str] = Counter()
        self.cut_user_input_application = False
        # Route 4: hold that reply this long instead of cutting it (a silent link).
        self.withhold_seconds: float | None = None
        # Route 2: the poll reply that carries a write_prompt.
        self.cut_prompt_poll = False
        # Route 6: cut this many download requests before they are answered.
        self.cut_input = 0
        self.input_cut_count = 0
        # Route 6: serve a digest that does not match the bytes.
        self.corrupt_input_digest = False

    async def cut(self) -> None:
        assert self.proxy is not None
        await asyncio.to_thread(self.proxy.cut_connections)

    async def accept_items(
        self, identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        self.seen_submissions.update(item.envelope.submission_id for item in batch.items)
        if self.cut_user_input_application:
            for item in batch.items:
                envelope = item.envelope
                ask = envelope.payload.get("ask")
                if (
                    envelope.kind is EnvelopeKind.answer_application
                    and isinstance(ask, dict)
                    and ask.get("kind") == "user_input"
                ):
                    self.cut_user_input_application = False
                    self.cut_submission = envelope.submission_id
        reply = await super().accept_items(identity, conversation_id, batch)
        if self.cut_submission is not None and any(
            receipt.submission_id == self.cut_submission for receipt in reply.receipts
        ):
            self.cut_submissions.append(self.cut_submission)
            self.cut_submission = None
            self.cut_count += 1
            if self.withhold_seconds is not None:
                await asyncio.sleep(self.withhold_seconds)
            else:
                await self.cut()
        return reply

    async def poll(
        self, identity: ExecutorIdentity, after: int, wait_seconds: float
    ) -> MailboxPollReply:
        reply = await super().poll(identity, after, wait_seconds)
        if self.cut_prompt_poll and any(
            command.child_command is not None
            and command.child_command.operation is ChildOperation.write_prompt
            for command in reply.commands
        ):
            self.cut_prompt_poll = False
            await self.cut()
        return reply

    async def input_file(
        self, identity: ExecutorIdentity, command_id: str, stored_file_id: str
    ) -> InputFile:
        if self.cut_input:
            self.cut_input -= 1
            self.input_cut_count += 1
            await self.cut()
        served = await super().input_file(identity, command_id, stored_file_id)
        if self.corrupt_input_digest:
            return InputFile(served.path, "0" * 64, served.byte_count)
        return served


def public_ask_id(ask: AskReference) -> str:
    """Public views scope native callback IDs to the exact turn and ask kind."""
    return f"{ask.turn.turn_number}:{ask.kind.value}:{ask.ask_id}"


async def eventually(condition: Callable[[], bool], detail: str, seconds: float = 8) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(detail)
        await asyncio.sleep(0.01)


def fast_mac_retries(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.05) -> None:
    """Shorten the Mac loops' back-off after a failed request. The shipped back-off
    starts at a second and doubles, which only slows a test that drops requests."""
    for name in (
        "planner.executor.mac_outbox.OUTBOX_RETRY_INITIAL_SECONDS",
        "planner.executor.mac_mailbox.OUTBOX_RETRY_INITIAL_SECONDS",
        "planner.executor.mac_files.INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS",
    ):
        monkeypatch.setattr(name, seconds)
    for name in (
        "planner.executor.mac_outbox.OUTBOX_RETRY_MAX_SECONDS",
        "planner.executor.mac_mailbox._LOOP_RETRY_MAX_SECONDS",
        "planner.executor.mac_files.INPUT_DOWNLOAD_RETRY_MAX_SECONDS",
    ):
        monkeypatch.setattr(name, seconds * 4)


@contextlib.asynccontextmanager
async def paused_outbox(mac: MacService) -> AsyncIterator[None]:
    """Hold the Mac's item requests (route 4), without holding a database or lock."""
    release = asyncio.Event()
    post = mac.client.post_items

    async def held(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
        await release.wait()
        return await post(conversation_id, batch)

    mac.client.post_items = held  # type: ignore[method-assign]
    try:
        yield
    finally:
        release.set()
        mac.client.post_items = post  # type: ignore[method-assign]


@contextlib.asynccontextmanager
async def mac_unreachable(proxy: HttpsProxy) -> AsyncIterator[None]:
    """No request of the Mac's gets through (blackhole), as while the link is gone;
    afterwards they pass again."""
    await asyncio.to_thread(proxy.blackhole, True)
    try:
        yield
    finally:
        await asyncio.to_thread(proxy.blackhole, False)


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
    ticket_id: str

    def events(self, kind: str, conversation_id: str = "c_scripted") -> int:
        connection = connect(self.endpoint.db_path)
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM conversation_events WHERE conversation_id=? AND kind=?",
                    (conversation_id, kind),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def open_commands(self) -> int:
        """Mailbox rows the Mac has not finished yet."""
        connection = connect(self.endpoint.db_path)
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM executor_commands WHERE state IN ('waiting','delivered')"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def mailbox_commands(self, operation: str) -> list[tuple[str, str]]:
        """(command_id, state) of every row of one operation, oldest first."""
        connection = connect(self.endpoint.db_path)
        try:
            return [
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT command_id,state FROM executor_commands WHERE operation=? "
                    "ORDER BY command_sequence",
                    (operation,),
                )
            ]
        finally:
            connection.close()

    def settled(self) -> bool:
        """Nothing waits on either side: the Mac outbox is empty, every mailbox row is
        finished, and every journal result is reported."""
        return (
            not self.mac.outbox.pending()
            and self.open_commands() == 0
            and not self.mac.journal.unreported(1)
        )

    async def wait_settled(self, detail: str, seconds: float = 8) -> None:
        await eventually(self.settled, detail, seconds)

    @property
    def child(self) -> Any:
        return next(iter(self.mac.runner._children.values()))

    @property
    def ask(self) -> AskReference:
        assert self.native.turn is not None
        return AskReference(
            self.child.reference, self.native.turn, "ask_permission", AskKind.permission
        )

    @property
    def input_ask(self) -> AskReference:
        assert self.native.turn is not None
        return AskReference(
            self.child.reference, self.native.turn, "request_input", AskKind.user_input
        )

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

    async def end_turn(self) -> None:
        ended = self.events("turn_ended")
        await self.native.end_turn()
        await eventually(lambda: self.events("turn_ended") == ended + 1, "turn did not end")
        await eventually(
            lambda: self.system._conversations["c_scripted"].running_turn is None,
            "turn did not settle in the core",
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

    def new_mac(self, revision: str | None = None) -> MacService:
        """Another Mac process over the same executor folder (its spool and journal)."""
        old = self.mac
        service = mac_service(old.paths, self.proxy, revision or old.identity.application_revision)
        service.catalog.snapshots = old.catalog.snapshots
        service.runner.factory = old.runner.factory
        return service

    async def stop_mac(self) -> None:
        """Stop the Mac process. Its spool and journal stay for the next one."""
        self.mac._stop.set()
        await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 5)

    async def start_mac(self, service: MacService) -> None:
        self.mac = service
        self.task = asyncio.create_task(service.run())
        await eventually(
            lambda: (
                service.presence.leased()
                and self.endpoint.presence.current_identity() == service.identity
            ),
            "the new Mac process was not leased",
        )

    async def restart_vps_core(self) -> None:
        """A VPS process restart: a new endpoint and conversation core over the same
        database. The Mac keeps running and simply retries."""
        await asyncio.wait_for(self.endpoint.close(), 2)
        self.old_systems.append(self.system)
        # A process exit releases the lifecycle lease without a native stop.
        # This in-process crash fixture must model that kernel-owned release.
        self.system._runtime_ownership.close()
        self.endpoint = CutReceiptEndpoint(self.endpoint.db_path)
        self.endpoint.proxy = self.proxy
        self.system = make_core(self.endpoint)
        self.endpoint.bind_conversations(self.system)
        self.transport_app.state.executor_endpoint = self.endpoint
        await self.endpoint.start()

    async def close(self) -> None:
        try:
            await self._close()
        finally:
            await self.transport_stack.aclose()

    async def _close(self) -> None:
        self.mac._stop.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 5)
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
    system.start_admission.configure_destinations(
        lambda destination: destination == "home" or endpoint.presence.is_present()
    )
    endpoint.mailbox.start_admission = system.start_admission
    return system


def mac_service(paths: ExecutorPaths, proxy: HttpsProxy, revision: str) -> MacService:
    return MacService(
        paths=paths,
        revision=revision,
        executor_id=EXECUTOR_ID,
        panels_server_url=proxy.origin,
        ssl_context=ssl.create_default_context(cafile=str(proxy.ca_path)),
    )


def native_factory_for(
    native: ScriptedNativeChild,
) -> Callable[[ResolvedConversationStart], BackendChildFactory]:
    def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
        del resolved

        def create(**arguments: object) -> BackendChild:
            native.sink = cast(BackendEventSink, arguments["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    return factory


def create_mac_ticket(
    db_path: str,
    root: Path,
    *,
    title: str,
    backend_key: ConversationBackendKey = ConversationBackendKey.codex,
    model: str = "scripted",
    reasoning_effort: str | None = None,
) -> str:
    from planner.projects.data import create_project
    from planner.tickets import data
    from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale

    with contextlib.closing(connect(db_path)) as connection:
        project = connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
        project_id = (
            project[0]
            if project is not None
            else create_project(connection, name="Transport fixture", folder_path=root, now=1).id
        )
        return data.create_ticket(
            connection,
            title=title,
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            locale=TicketLocale.macos,
            project_id=project_id,
            employee_backend=backend_key.value,
            employee_launch_model=model,
            employee_launch_reasoning_effort=reasoning_effort,
        ).id


async def start_ticket_conversation(
    system: SqliteProcessConversationSystem,
    db_path: str,
    root: Path,
    ticket_id: str,
    conversation_id: str,
    *,
    backend_key: ConversationBackendKey = ConversationBackendKey.codex,
    model: str = "scripted",
    reasoning_effort: str | None = None,
) -> PromptDeliveryFate:
    """Start a Ticket's Mac conversation and send its first message ("Start")."""
    from planner.tickets import data

    await system.start_conversation(
        ConversationStartRequest(
            conversation_id=conversation_id,
            model=model,
            backend_key=backend_key,
            reasoning_effort=reasoning_effort,
            workspace_folder=root,
            workspace_path_policy=WorkspacePathPolicy.absolute,
            role_materials=ConversationRoleMaterials(
                "A scripted test worker.",
                (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket_id)),
            ),
        )
    )
    with contextlib.closing(connect(db_path)) as connection:
        data.write_ticket_conversation_start(
            connection,
            ticket_id,
            conversation_id=conversation_id,
            backend=backend_key.value,
            model=model,
            reasoning_effort=reasoning_effort,
            now=2,
        )
    return await asyncio.wait_for(
        system.send(
            conversation_id,
            text_message_content("Start"),
            sender_label="Test",
            sender=CHIEF_PRINCIPAL,
            recipient=Principal(PrincipalKind.ticket, ticket_id),
        ),
        5,
    )


async def boot(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_factory: Callable[[ResolvedConversationStart], BackendChildFactory] | None = None,
    backend_key: ConversationBackendKey = ConversationBackendKey.codex,
    model: str = "scripted",
    reasoning_effort: str | None = None,
    emit_early_event: bool = False,
    fast_retries: bool = True,
) -> ExecutorHarness:
    """A present Mac running conversation c_scripted, whose first turn is running."""
    if fast_retries:
        fast_mac_retries(monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(root / "native-codex-home"))
    vps_root = root / "vps"
    vps_root.mkdir()
    db_path = str(vps_root / "panels.sqlite")
    connection = connect(db_path)
    try:
        create_schema(connection)
    finally:
        connection.close()
    worker_ticket_id = create_mac_ticket(
        db_path,
        root,
        title="Reconnect worker",
        backend_key=backend_key,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    endpoint = CutReceiptEndpoint(db_path)
    system = make_core(endpoint)
    endpoint.bind_conversations(system)
    native = ScriptedNativeChild()
    native.emit_early_event = emit_early_event
    from fastapi import FastAPI

    from planner.core.response_compression import (
        CompressExceptMarkedRoutes,
        uncompressed_route_patterns,
    )
    from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
    from planner.executor.app_transport import build_executor_router

    transport_app = FastAPI()
    transport_app.state.executor_id = EXECUTOR_ID
    transport_app.state.executor_endpoint = endpoint
    executor_router = build_executor_router()
    transport_app.include_router(executor_router)
    transport_app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@example.com", "https://localhost"),
    )
    transport_app.add_middleware(
        CompressExceptMarkedRoutes,
        uncompressed_patterns=uncompressed_route_patterns(executor_router.routes),
        minimum_size=1024,
        compresslevel=4,
    )
    transport_stack = contextlib.AsyncExitStack()
    try:
        proxy = await transport_stack.enter_async_context(
            serve_executor_app(transport_app, root / "tls")
        )
        endpoint.proxy = proxy
        await endpoint.start()
        mac = mac_service(ExecutorPaths(root / "mac"), proxy, "a" * 40)
    except BaseException:
        await endpoint.close()
        await transport_stack.aclose()
        raise
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    observer = ScriptedBackendSnapshots(lambda: mac.identity)
    observer.model = model
    if backend_key is ConversationBackendKey.claude:
        observer.models = (model, "claude-new")
    observer.efforts = ("low", "medium", "high", "xhigh", "max")
    mac.catalog.snapshots = observer
    mac.runner.factory = native_factory or native_factory_for(native)
    task = asyncio.create_task(mac.run())
    harness = ExecutorHarness(
        root,
        endpoint,
        mac,
        system,
        native,
        task,
        [],
        transport_stack,
        transport_app,
        proxy,
        worker_ticket_id,
    )
    try:
        await eventually(
            lambda: mac.presence.leased() and endpoint.health().presence == "present",
            "The Mac's first lease was not accepted",
        )
        initial_delivery = await start_ticket_conversation(
            system,
            db_path,
            root,
            worker_ticket_id,
            "c_scripted",
            backend_key=backend_key,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        assert isinstance(initial_delivery, PromptDeliveryStarted), initial_delivery
        if native_factory is None:
            await eventually(
                lambda: native.start_count == 1 and native.prompt_count == 1,
                "The first prompt did not reach the scripted agent",
            )
        await harness.wait_settled("Initial command outcomes did not drain")
        return harness
    except BaseException:
        await harness.close()
        raise


def test_early_prompt_event_and_idle_publications_do_not_wait_for_retry_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real outbox and event admission progress without waiting for any retry timer:
    the shipped back-off (fast_retries=False), every step within 2 s."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch, emit_early_event=True, fast_retries=False)
        try:
            incarnation = harness.mac.identity.executor_incarnation
            await eventually(lambda: harness.events("tool_call_started") == 1, "early event")
            await harness.native.end_turn()
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
                "Event before prompt result stalled outbox replay",
                seconds=2,
            )
            # Let the sender become idle; an event alone must wake it too.
            await asyncio.sleep(0.1)
            assert harness.native.sink is not None and harness.native.turn is not None
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
            child = harness.child
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
            assert harness.mac.identity.executor_incarnation == incarnation
            assert harness.endpoint.health().presence == "present"
            assert harness.native.prompt_count == 2
        finally:
            await harness.close()

    asyncio.run(run())


def native_input_path(harness: ExecutorHarness, stored_file_id: str) -> Path:
    """Where the Mac installs a downloaded input for c_scripted."""
    return harness.mac.paths.root / "artifacts/files/conversations/c_scripted" / stored_file_id


def journal_rows(harness: ExecutorHarness, command_id: str) -> int:
    with contextlib.closing(
        connect(str(harness.mac.paths.spool / "commands.sqlite3"))
    ) as connection:
        return int(
            connection.execute(
                "SELECT count(*) FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()[0]
        )


async def send_file(harness: ExecutorHarness, stored_file_id: str, size: int) -> Any:
    return await asyncio.wait_for(
        harness.system.send(
            "c_scripted",
            (
                MessageText("Inspect the attachment"),
                MessageFile(stored_file_id, "application/octet-stream", "input.bin", size),
            ),
            sender_label="Test",
        ),
        5,
    )


def watch_downloads(harness: ExecutorHarness) -> list[BaseException | None]:
    """Record each Mac download attempt (route 6) and how it ended."""
    attempts: list[BaseException | None] = []
    download = harness.mac.client.download_command_file

    async def observed(command_id: str, stored_file_id: str, target: Path) -> str:
        try:
            digest = await download(command_id, stored_file_id, target)
        except BaseException as error:
            attempts.append(error)
            raise
        attempts.append(None)
        return digest

    harness.mac.client.download_command_file = observed  # type: ignore[method-assign]
    return attempts


@pytest.mark.parametrize("outage", ["lost_poll_reply", "already_down"])
def test_prompt_survives_short_link_drop_once_with_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outage: str
) -> None:
    """Gap: a prompt whose poll reply is lost after delivery marking, or that is sent
    while the Mac is unreachable, runs exactly once, with its bytes fetched fresh."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.end_turn()
            incarnation = harness.mac.identity.executor_incarnation
            contents = b"Attachment from the VPS after the drop"
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = native_input_path(harness, kept.stored_file_id)
            native_path.parent.mkdir(parents=True, exist_ok=True)
            native_path.write_bytes(b"Stale local bytes must be fetched again")
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            if outage == "lost_poll_reply":
                harness.endpoint.cut_prompt_poll = True
                fate = await send_file(harness, kept.stored_file_id, len(contents))
            else:
                async with mac_unreachable(harness.proxy):
                    fate = await send_file(harness, kept.stored_file_id, len(contents))
                    # Stored while the Mac cannot be reached, and not run. A poll the
                    # VPS still held when the link went may have marked it delivered to
                    # nobody; the Mac's next poll from its cursor gets it again.
                    assert harness.mailbox_commands("write_prompt")[-1][1] in (
                        "waiting",
                        "delivered",
                    )
                    await asyncio.sleep(0.2)
                    assert harness.native.prompt_count == 1
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await eventually(lambda: harness.native.prompt_count == 2, "prompt did not run")
            await harness.wait_settled("the prompt's result did not settle")
            assert not harness.endpoint.cut_prompt_poll
            prompt_id, state = harness.mailbox_commands("write_prompt")[-1]
            assert state == "finished"
            assert journal_rows(harness, prompt_id) == 1
            assert native_path.read_bytes() == contents
            # Another drop does not run the same prompt a second time.
            await harness.endpoint.cut()
            await asyncio.sleep(0.3)
            await harness.wait_settled("the link did not come back")
            assert harness.native.start_count == 1 and harness.native.prompt_count == 2
            assert harness.mac.identity.executor_incarnation == incarnation
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("receipt_failure", ["disconnect", "silent"])
def test_commit_before_ack_replay_returns_first_receipt_and_one_domain_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_failure: str,
) -> None:
    """Gap: an item batch whose reply is lost after the commit (cut, or withheld past
    the Mac's timeout) is resent and gets its first receipt; the effect happens once."""

    async def run() -> None:
        if receipt_failure == "silent":
            monkeypatch.setattr("planner.executor.mac_http.ITEM_BATCH_TIMEOUT_SECONDS", 0.3)
        harness = await boot(tmp_path, monkeypatch)
        try:
            if receipt_failure == "silent":
                harness.endpoint.withhold_seconds = 1.0
            async with paused_outbox(harness.mac):
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
            assert first_receipt.submission_id == pending[0].submission_id
            assert "payload_sha256" not in as_object(first_receipt)
            assert "payload_sha256" not in as_object(pending[0])
            await harness.wait_settled("Committed entry did not replay and acknowledge")
            # The VPS was sent the item again after its reply was lost.
            assert harness.endpoint.seen_submissions[pending[0].submission_id] >= 2
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


INPUT_ANSWERS = (UserInputAnswer("q_two", ("Red", "Blue")), UserInputAnswer("q_one", ("Yes",)))


ORDERED_INPUT_ANSWERS = (INPUT_ANSWERS[1], INPUT_ANSWERS[0])


def test_user_input_callback_application_survives_lost_receipt_without_reanswer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: the answer_application whose receipt is cut is resent and applied once; the
    agent was answered once, and a second answer is refused."""
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
            )
            pending = await _pending_user_input(runtime, "c_scripted")
            assert pending is not None
            assert len(pending["questions"]) == 2
            harness.endpoint.cut_user_input_application = True
            assert (
                await harness.system.answer_user_input(
                    "c_scripted", public_ask_id(harness.input_ask), INPUT_ANSWERS
                )
            ) is AnswerFate.saved
            await eventually(
                lambda: harness.endpoint.cut_count == 1,
                "input application acknowledgment was not cut",
            )
            await harness.wait_settled("input application replay did not drain")
            (cut,) = harness.endpoint.cut_submissions
            assert harness.endpoint.seen_submissions[cut] >= 2
            assert harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)]
            assert harness.events("user_input_answered") == 1
            assert harness.events("answer_applied") == 1
            replacement = (UserInputAnswer("q_one", ("No",)), UserInputAnswer("q_two", ("Blue",)))
            assert (
                await harness.system.answer_user_input(
                    "c_scripted", public_ask_id(harness.input_ask), replacement
                )
            ) is AnswerFate.refused
            assert harness.events("user_input_answered") == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_input_attachment_bytes_arrive_before_native_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a prompt's input bytes come over route 6 before the agent is invoked, and a
    slow download holds no other Mac work (a model-list read answers meanwhile)."""
    from planner.conversation.contracts import ConversationBackendKey

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.end_turn()
            downloading, release = asyncio.Event(), asyncio.Event()
            download = harness.mac.client.download_command_file

            async def slow(command_id: str, stored_file_id: str, target: Path) -> str:
                downloading.set()
                await release.wait()
                return await download(command_id, stored_file_id, target)

            harness.mac.client.download_command_file = slow  # type: ignore[method-assign]
            contents = bytes(range(256)) * 800
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = native_input_path(harness, kept.stored_file_id)
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            fate = await send_file(harness, kept.stored_file_id, len(contents))
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await asyncio.wait_for(downloading.wait(), 5)
            listed = await asyncio.wait_for(
                harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True), 5
            )
            assert listed.available_models
            assert harness.native.prompt_count == 1 and not native_path.exists()
            release.set()
            await eventually(lambda: harness.native.prompt_count == 2, "prompt did not run")
            await harness.wait_settled("the prompt did not settle")
            assert native_path.read_bytes() == contents
            ((prompt_id, _), *_) = reversed(harness.mailbox_commands("write_prompt"))
            assert harness.mac.runner._commands[prompt_id].command.command_id == prompt_id
            assert journal_rows(harness, prompt_id) == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_input_download_drop_retries_and_an_input_no_longer_served_fails_its_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a cut input download is retried by the Mac (not refused), and the prompt
    runs once with the right bytes; an input the VPS no longer serves (404) refuses the
    prompt, R1 ends its turn failed, and the next send still delivers."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.end_turn()
            attempts = watch_downloads(harness)
            contents = bytes(range(256)) * 3000
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            native_path = native_input_path(harness, kept.stored_file_id)
            harness.native.required_input_path = native_path
            harness.native.required_input_contents = contents
            # Two cuts: aiohttp resends a GET once by itself on a reused connection, so
            # the second cut reaches the Mac's own retry either way.
            harness.endpoint.cut_input = 2
            fate = await send_file(harness, kept.stored_file_id, len(contents))
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await eventually(lambda: harness.native.prompt_count == 2, "prompt did not run")
            assert harness.endpoint.input_cut_count == 2
            assert any(error is not None for error in attempts) and attempts[-1] is None
            assert native_path.read_bytes() == contents
            await harness.wait_settled("the delivered prompt did not settle")
            await harness.end_turn()

            # An input the VPS no longer has: route 6 answers 404, the prompt is refused.
            gone = await harness.endpoint.message_files.keep(
                "c_scripted", b"bytes that go away", media_type="application/octet-stream"
            )
            harness.endpoint.message_files.path_of("c_scripted", gone.stored_file_id).unlink()
            harness.native.required_input_path = None
            fate = await send_file(harness, gone.stored_file_id, len(b"bytes that go away"))
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await eventually(
                lambda: harness.events("turn_ended") == 3, "R1 did not end the refused turn"
            )
            await harness.wait_settled("the refused prompt did not settle")
            assert harness.native.prompt_count == 2
            ending = turn_ended_payloads(harness)[-1]
            assert ending["ending"] == "failed"
            assert "The Mac could not deliver this message" in str(ending["error_summary"])
            await eventually(
                lambda: harness.system._conversations["c_scripted"].running_turn is None,
                "the failed turn did not settle in the core",
            )

            # A separate send with a file the VPS has still delivers.
            harness.native.required_input_path = native_path
            fate = await send_file(harness, kept.stored_file_id, len(contents))
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await eventually(lambda: harness.native.prompt_count == 3, "next send did not run")
            assert native_path.read_bytes() == contents
        finally:
            await harness.close()

    asyncio.run(run())


def turn_ended_payloads(harness: ExecutorHarness) -> list[dict[str, Any]]:
    import json

    with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM conversation_events WHERE conversation_id='c_scripted' "
                "AND kind='turn_ended' ORDER BY sequence"
            )
        ]


def test_input_descriptor_must_validate_before_provider_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: bytes whose served digest does not match are never installed or given to
    the agent (the Mac retries); other Mac work goes on; the right digest runs it once."""
    from planner.conversation.contracts import ConversationBackendKey

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.end_turn()
            attempts = watch_downloads(harness)
            harness.endpoint.corrupt_input_digest = True
            contents = b"input bytes"
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", contents, media_type="application/octet-stream"
            )
            target = native_input_path(harness, kept.stored_file_id)
            harness.native.required_input_path = target
            harness.native.required_input_contents = contents
            fate = await send_file(harness, kept.stored_file_id, len(contents))
            assert isinstance(fate, PromptDeliveryStarted), repr(fate)
            await eventually(lambda: len(attempts) >= 2, "the Mac did not retry the download")
            assert all(error is not None for error in attempts)
            assert not target.exists()
            assert harness.native.prompt_count == 1
            listed = await asyncio.wait_for(
                harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True), 5
            )
            assert listed.available_models
            assert harness.native.prompt_count == 1 and not target.exists()
            harness.endpoint.corrupt_input_digest = False
            await eventually(lambda: harness.native.prompt_count == 2, "prompt did not run")
            await harness.wait_settled("the prompt did not settle")
            assert target.read_bytes() == contents
            assert harness.native.prompt_count == 2
        finally:
            await harness.close()

    asyncio.run(run())


def test_kill_withdraws_a_stalled_delivery_without_a_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a kill while a prompt's delivery is still committing withdraws its waiting
    row on the VPS alone, with no Mac request in between; the Mac never runs it."""
    from planner.conversation.contracts import ConversationBackendKey
    from planner.core.errors import PlannerError
    from planner.executor.protocol import ChildCommand, ChildOperation

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        polls_released, poll_waiting = asyncio.Event(), asyncio.Event()
        sending = killing = None
        try:
            await harness.end_turn()
            original = harness.child.reference
            poll = harness.mac.client.poll_commands

            async def held_poll(after: int, wait_seconds: float) -> Any:
                if not polls_released.is_set():
                    poll_waiting.set()
                    await polls_released.wait()
                return await poll(after, wait_seconds)

            harness.mac.client.poll_commands = held_poll  # type: ignore[method-assign]
            # A model-list row answers the poll the VPS is holding now; the Mac's next
            # poll is held on the Mac, so no poll waits at the VPS from here on.
            await asyncio.wait_for(
                harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True), 5
            )
            await asyncio.wait_for(poll_waiting.wait(), 5)
            put = harness.endpoint.mailbox.put_child_command

            async def stalled(command: ChildCommand) -> Any:
                if command.operation is ChildOperation.write_prompt:
                    entered.set()
                    await release.wait()
                return await put(command)

            harness.endpoint.mailbox.put_child_command = stalled  # type: ignore[method-assign]
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", b"input bytes", media_type="application/octet-stream"
            )
            sending = asyncio.create_task(send_file(harness, kept.stored_file_id, 11))
            await asyncio.wait_for(entered.wait(), 5)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            await asyncio.sleep(0.1)
            release.set()
            fate = await asyncio.wait_for(sending, 5)
            # Withdrawal proves no prompt delivery, but not native process retirement.
            with pytest.raises(PlannerError, match="previous agent did not stop"):
                await asyncio.wait_for(killing, 5)
            assert isinstance(fate, PromptDeliverySuppressed), repr(fate)
            # Withdrawn on the VPS while the Mac's poll was still held on the Mac.
            assert not polls_released.is_set()
            assert harness.mailbox_commands("write_prompt")[-1][1] == "withdrawn"
            polls_released.set()
            await eventually(
                lambda: original.child_id not in harness.mac.runner._children,
                "original child did not stop",
            )
            await harness.wait_settled("the kill did not settle")
            assert harness.native.stop_count == 1 and harness.native.prompt_count == 1
            assert harness.mailbox_commands("write_prompt")[-1][1] == "withdrawn"
        finally:
            release.set()
            polls_released.set()
            for task in (sending, killing):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def fast_leases(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.1) -> None:
    """Lease every `seconds`: the drain and presence changes wait for the next lease."""
    monkeypatch.setattr("planner.executor.mac_presence.LEASE_INTERVAL_SECONDS", seconds)


async def post_owner_answer(harness: ExecutorHarness, ask: AskReference) -> dict[str, str]:
    import httpx
    from fastapi import FastAPI

    from planner.conversation.api import _runtime, router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    runtime = SimpleNamespace(system=harness.system, database_path=harness.endpoint.db_path)
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


def stop_rows(harness: ExecutorHarness) -> list[tuple[str, str, str | None]]:
    """(command_id, state, outcome_json) of the mailbox's stop rows."""
    with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
        return [
            (str(row[0]), str(row[1]), row[2])
            for row in connection.execute(
                "SELECT command_id,state,outcome_json FROM executor_commands "
                "WHERE operation='stop' ORDER BY command_sequence"
            )
        ]


def ticket_recap(harness: ExecutorHarness, ticket_id: str) -> str:
    with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
        return str(
            connection.execute("SELECT recap FROM tickets WHERE id=?", (ticket_id,)).fetchone()[0]
        )


def test_planned_runtime_swap_and_reversion_replay_original_results_after_vps_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a planned runtime swap hands the outbox to the next runtime under the same
    incarnation. Across a VPS restart and a reverted candidate, the saved write, reply
    and update stop each apply once. The unconfirmed candidate admits nothing and keeps
    custody; the fallback releases it once admitted."""
    from planner.executor.mac_updates import handoff_record
    from planner.executor.protocol import CliWrite, as_object
    from planner.executor.runtime_bootstrap import atomic_record, read_record
    from planner.executor.update_configuration import (
        MAC_RUNTIME_HANDOFF_ENV,
        MAC_RUNTIME_READY_ENV,
    )

    monkeypatch.setattr("planner.executor.mac_updates.MAC_UPDATE_POLL_SECONDS", 0.05)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            old = harness.mac
            child = harness.child
            assert harness.native.sink is not None and harness.native.turn is not None
            artifact = old.paths.artifacts / child.ticket_id / "saved-before-update.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"durable artifact before update")
            async with paused_outbox(old):
                saved_write = old.outbox.add(
                    OutboxEntry(
                        child.reference.conversation_id,
                        EnvelopeKind.cli_write,
                        as_object(
                            CliWrite(
                                "ticket.edit",
                                {
                                    "ticket_id": child.ticket_id,
                                    "body": {"recap": "Saved before update"},
                                },
                                child.reference,
                                harness.native.turn,
                            )
                        ),
                        "saved_before_update",
                    ),
                    ticket_id=child.ticket_id,
                )
                assert saved_write.file_dependencies
                await harness.native.sink.report(
                    harness.native.turn,
                    AgentMessageCompleted(
                        content=text_message_content("Saved reply before update")
                    ),
                )
                assert await old.runner.prepare_update("now")
                original_pending = old.outbox.pending()
                assert [item.kind for item in original_pending] == [
                    EnvelopeKind.cli_write,
                    EnvelopeKind.backend_event,
                    EnvelopeKind.child_stopped,
                ]
                identity = old.identity
                handoff = old.paths.root / "updates/handoff.json"
                atomic_record(handoff, handoff_record(identity, replay_pending=True))
                await harness.stop_mac()
            await harness.restart_vps_core()
            accepted: dict[str, list[AcceptanceReceipt]] = {}
            accept = harness.endpoint.inbox.accept

            async def observe_accept(
                envelope: QueuedEnvelope, peer: ExecutorIdentity, **kwargs: Any
            ) -> AcceptanceReceipt:
                receipt = await accept(envelope, peer, **kwargs)
                accepted.setdefault(envelope.submission_id, []).append(receipt)
                return receipt

            monkeypatch.setattr(harness.endpoint.inbox, "accept", observe_accept)
            monkeypatch.setenv(MAC_RUNTIME_HANDOFF_ENV, str(handoff))
            # The candidate runs on trial: the supervisor has not confirmed it.
            monkeypatch.setenv(MAC_RUNTIME_READY_ENV, str(old.paths.root / "ready.json"))

            candidate = harness.new_mac("b" * 40)
            async with paused_outbox(candidate):
                await harness.start_mac(candidate)
                # The candidate inherits the incarnation, so the VPS replaces nothing.
                assert candidate.identity.executor_incarnation == identity.executor_incarnation
                assert candidate.identity.connection_generation == identity.connection_generation
                assert candidate.runner.admission_stopped
                assert candidate.runner.planned_update_replay
                assert candidate.outbox.pending() == original_pending
                assert harness.endpoint.runs.store.child_assignment(child.reference) is not None
                # Model a failure and reversion after its lease but before saved receipts.
                await harness.stop_mac()
            assert accepted == {}
            assert (read_record(handoff) or {}).get("replay_pending") is True
            monkeypatch.delenv(MAC_RUNTIME_READY_ENV)
            fallback = harness.new_mac("a" * 40)
            await harness.start_mac(fallback)
            await eventually(
                lambda: not fallback.outbox.pending(), "Saved update output did not replay"
            )
            await eventually(
                lambda: harness.endpoint.runs.store.has_closed_child_assignment(child.reference),
                "The update stop's proof did not retire the run after the saved output",
            )
            assert fallback.identity.executor_incarnation == identity.executor_incarnation
            await eventually(
                lambda: (read_record(handoff) or {}).get("replay_pending") is False,
                "The Mac did not release custody after saved output drained",
            )
            await eventually(
                lambda: not fallback.runner.admission_stopped, "Admission did not reopen"
            )
            assert ticket_recap(harness, child.ticket_id) == "Saved before update"
            # Each original item receives one acceptance, never a refusal.
            for original in original_pending:
                receipts = accepted[original.submission_id]
                assert len(receipts) == 1
                assert receipts[0].disposition is ReceiptDisposition.accepted
            uploaded = harness.endpoint.inbox.store.file_target(saved_write.file_dependencies[0])
            assert uploaded.read_bytes() == artifact.read_bytes()
            # A duplicate of the original submission cannot change the Ticket again.
            receipt = await accept(saved_write, fallback.identity)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert harness.native.start_count == harness.native.prompt_count == 1
            assert harness.native.stop_count == 1
            state = harness.system._conversations["c_scripted"]
            await eventually(lambda: state.child is None, "The core did not retire the child")
            assert not state.child_is_quarantined
            # The adopted turn was live: the replayed reply applied to it.
            reply = original_pending[1].submission_id
            assert accepted[reply][0].result == {"applied": True}
        finally:
            await harness.close()

    asyncio.run(run())


def test_update_stop_settles_live_vps_before_a_failed_attempt_resumes_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: the update target arrives in a lease reply; the stop it causes must settle
    the live core through its proof, even with no VPS restart, and a failed attempt
    must reopen Mac admission for a fresh child."""
    from dataclasses import replace

    from tests.unit.test_mac_updates import MacProvider, attempt, mac_service, write

    from planner.executor.protocol import CliWrite, as_object

    fast_leases(monkeypatch)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            system = harness.system
            provider = MacProvider()
            published = provider.publish()
            mac_updates = mac_service(
                tmp_path / "server", provider, harness.mac.identity.application_revision
            )
            mac_updates.prepare(published)
            harness.transport_app.state.mac_updates = mac_updates
            harness.endpoint.presence.update_check = mac_updates.check
            child = harness.child
            old_turn = harness.native.turn
            assert old_turn is not None
            artifact = harness.mac.paths.artifacts / child.ticket_id / "before-stop.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"saved before the authoritative stop")
            async with paused_outbox(harness.mac):
                saved = harness.mac.outbox.add(
                    OutboxEntry(
                        child.reference.conversation_id,
                        EnvelopeKind.cli_write,
                        as_object(
                            CliWrite(
                                "ticket.edit",
                                {
                                    "ticket_id": child.ticket_id,
                                    "body": {"recap": "Saved before the failed update"},
                                },
                                child.reference,
                                old_turn,
                            )
                        ),
                    ),
                    ticket_id=child.ticket_id,
                )
                assert saved.file_dependencies
                preparing = attempt(published, "preparing")
                write(mac_updates, preparing)
                await eventually(
                    lambda: (
                        (status := mac_updates.status()) is not None
                        and status.phase == "ready_to_restart"
                    ),
                    "The present Mac did not report its physical stop",
                )
                assert harness.native.stop_count == 1
                stopped = next(
                    row
                    for row in harness.mac.outbox.pending()
                    if row.kind is EnvelopeKind.child_stopped
                )
                assert stopped.sequence > saved.sequence
                assert system._conversations["c_scripted"].running_turn is not None
                assert harness.endpoint.runs.store.child_assignment(child.reference) is not None
                # The VPS part fails before installation, so the old release stays.
                mac_updates.attempts.change(
                    preparing.attempt_id,
                    frozenset({"preparing"}),
                    lambda value: replace(value, state="failed", detail="fixture failure"),
                )
            await eventually(lambda: not harness.mac.outbox.pending(), "The outbox did not drain")
            await eventually(
                lambda: (
                    system._conversations["c_scripted"].running_turn is None
                    and system._conversations["c_scripted"].child is None
                ),
                "The Mac's stop proof did not settle the unchanged VPS core",
            )
            await eventually(
                lambda: not harness.mac.runner.admission_stopped,
                "A failed attempt did not release Mac admission",
            )
            assert harness.system is system
            state = system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            assert state.retained_child_reference is None
            endings = [
                event.payload
                for event in await system._store.read_events_after("c_scripted", 0)
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert len(endings) == 1
            assert endings[0].ending is ConversationTurnEnding.interrupted
            assert harness.endpoint.runs.store.child_assignment(child.reference) is None
            assert harness.endpoint.runs.store.has_closed_child_assignment(child.reference)
            assert ticket_recap(harness, child.ticket_id) == "Saved before the failed update"
            uploaded = harness.endpoint.inbox.store.file_target(saved.file_dependencies[0])
            assert uploaded.read_bytes() == artifact.read_bytes()
            delivery = await asyncio.wait_for(
                system.send(
                    "c_scripted",
                    text_message_content("Continue after the failed update"),
                    sender_label="Test",
                    sender=CHIEF_PRINCIPAL,
                    recipient=Principal(PrincipalKind.ticket, child.ticket_id),
                ),
                3,
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            await eventually(
                lambda: harness.native.start_count == harness.native.prompt_count == 2,
                "The fresh child did not start",
            )
            current = harness.child.reference
            assert current != child.reference
            running = state.running_turn
            assert running is not None and running.token != old_turn
            # The original proof can be resent after a lost reply, but neither it nor a
            # substituted payload under its identity can stop this child.
            identity = harness.mac.identity
            original = await harness.endpoint.inbox.accept(stopped, identity)
            assert original.disposition is ReceiptDisposition.accepted
            outcome = type_adapter(CommandOutcome).validate_python(stopped.payload)
            substituted = replace(stopped, payload=as_object(replace(outcome, child=current)))
            assert (
                await harness.endpoint.inbox.accept(substituted, identity)
            ).disposition is ReceiptDisposition.accepted
            await asyncio.sleep(0)
            assert state.running_turn is running
            assert harness.endpoint.runs.store.child_assignment(current) is not None
            assert harness.native.stop_count == 1
            assert not state.child_is_quarantined
        finally:
            await harness.close()

    asyncio.run(run())


def test_mac_replacement_drains_then_interrupts_old_run_and_recovers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a new Mac process must not cut its predecessor's run short: the run stays
    open until the new process's lease shows that conversation drained, so the replayed
    output applies; then the turn ends interrupted and one recovery message starts a
    fresh child under the new incarnation. A steer the old process had fetched comes
    back uncertain, never as delivered and never resent."""
    from planner.conversation.contracts import PromptDeliveryUncertain

    fast_leases(monkeypatch)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        steer_reached = asyncio.Event()
        steering: asyncio.Task[Any] | None = None
        try:
            # The Ticket's worker step is out, as the readiness loop leaves it.
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                connection.execute(
                    "UPDATE tickets SET worker_step_claim='out',worker_step_claim_revision=7 "
                    "WHERE id=?",
                    (harness.ticket_id,),
                )
                connection.commit()
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            ask = harness.ask
            old_turn = harness.native.turn
            old_mac = harness.mac

            async def held_steer(
                turn: TurnToken, content: MessageContent, **kwargs: object
            ) -> BackendSteerAccepted:
                steer_reached.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            monkeypatch.setattr(harness.native, "steer", held_steer, raising=False)
            delivered: list[str] = []
            write_prompt = harness.native.write_prompt

            async def recorded_prompt(
                turn: TurnToken, content: MessageContent, **kwargs: object
            ) -> BackendPromptAccepted:
                delivered.append(
                    "\n".join(piece.text for piece in content if isinstance(piece, MessageText))
                )
                return await write_prompt(turn, content, **kwargs)

            monkeypatch.setattr(harness.native, "write_prompt", recorded_prompt)
            steering = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    text_message_content("Unknown"),
                    sender_label="Test",
                    mode=PromptDeliveryMode.steer,
                )
            )
            await asyncio.wait_for(steer_reached.wait(), 5)
            async with paused_outbox(old_mac):
                # Output the old process made before it died, still in its outbox.
                assert harness.native.sink is not None and old_turn is not None
                await harness.native.sink.report(
                    old_turn,
                    ToolCallStarted(
                        tool_call_id="before-crash", title="Before", tool_kind="other", detail=None
                    ),
                )
                await harness.stop_mac()
            # Model service death with a fresh registry and incarnation. This does not
            # prove launchd terminates real provider process groups.
            replacement = harness.new_mac()
            await harness.start_mac(replacement)
            assert replacement.identity.executor_incarnation != ask.child.executor_incarnation
            fate = await asyncio.wait_for(steering, 15)
            assert isinstance(fate, PromptDeliveryUncertain), fate
            await eventually(
                lambda: harness.endpoint.runs.store.has_closed_child_assignment(ask.child),
                "The drained run was not closed",
            )
            # The replayed event applied under the still-open assignment.
            assert harness.events("tool_call_started") == 1
            await eventually(
                lambda: harness.events("turn_ended") == 1, "The old turn was not interrupted"
            )
            endings = [
                event.payload
                for event in await harness.system._store.read_events_after("c_scripted", 0)
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert [ending.ending for ending in endings] == [ConversationTurnEnding.interrupted]
            assert not harness.answer_applied(ask)
            # One recovery message starts a fresh child under the new incarnation.
            await eventually(
                lambda: harness.native.start_count == harness.native.prompt_count == 2,
                "The recovery message did not start a fresh child",
            )
            fresh_child = harness.child.reference
            assert fresh_child.executor_incarnation == replacement.identity.executor_incarnation
            assert harness.native.turn != old_turn
            state = harness.system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            await harness.wait_settled("The recovery did not settle")
            assert harness.events("prompt_delivery_uncertain") == 1
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                # The recovery restored the worker step's claim, as a new revision.
                claim, revision = connection.execute(
                    "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id=?",
                    (harness.ticket_id,),
                ).fetchone()
                assert claim == "out" and revision > 7
                assert [
                    row[0]
                    for row in connection.execute(
                        "SELECT recovery_state FROM deploy_interruptions "
                        "WHERE source_conversation_id='c_scripted'"
                    )
                ] == ["resumed"]
            await asyncio.sleep(0.3)
            assert harness.native.start_count == harness.native.prompt_count == 2
            # The fresh child got the recovery prompt, once.
            assert len(delivered) == 1
            assert (
                delivered[0].count(
                    "Inspect the interrupted work and continue the current authorized step."
                )
                == 1
            )
        finally:
            if steering is not None and not steering.done():
                steering.cancel()
                await asyncio.gather(steering, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_a_message_the_replaced_mac_process_never_fetched_reaches_the_fresh_agent_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: a message is sent while the Mac process is gone, and its long poll, still
    held, marks the prompt delivered into a dead socket. A new process then leases. The
    message must reach the fresh agent once, and nothing be recorded as interrupted.
    Gap: the unit tests prove the cursor rule and the rewrite apart; this proves the real
    successor's journal cursor, the drain and the core together."""
    fast_leases(monkeypatch)
    mark = "a message sent while the Mac was gone"

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            delivered: list[str] = []
            write_prompt = harness.native.write_prompt

            async def recorded_prompt(
                turn: TurnToken, content: MessageContent, **kwargs: object
            ) -> BackendPromptAccepted:
                delivered.append(
                    "\n".join(piece.text for piece in content if isinstance(piece, MessageText))
                )
                return await write_prompt(turn, content, **kwargs)

            monkeypatch.setattr(harness.native, "write_prompt", recorded_prompt)
            await harness.end_turn()
            await harness.wait_settled("the first turn did not settle")
            await harness.stop_mac()  # Its held poll stays on the VPS.
            await harness.system.send(
                "c_scripted", text_message_content(mark), sender_label="Owner"
            )
            await eventually(
                lambda: (
                    [state for _, state in harness.mailbox_commands("write_prompt")][-1]
                    == "delivered"
                ),
                "the held poll did not take the prompt",
            )
            replacement = harness.new_mac()
            await harness.start_mac(replacement)
            await eventually(lambda: len(delivered) == 1, "the fresh agent did not get it")
            await harness.wait_settled("the rewritten prompt did not settle")
            assert mark in delivered[0]
            assert harness.child.reference.executor_incarnation == (
                replacement.identity.executor_incarnation
            )
            assert [state for _, state in harness.mailbox_commands("write_prompt")][-2:] == [
                "withdrawn",
                "finished",
            ]
            assert harness.events("turn_ended") == 1  # Only the first turn's.
            # The interruption that held the message through the rewrite is closed.
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                assert [
                    row[0]
                    for row in connection.execute("SELECT recovery_state FROM deploy_interruptions")
                ] == ["closed"]
            await asyncio.sleep(0.3)
            assert len(delivered) == 1
        finally:
            await harness.close()

    asyncio.run(run())


def test_stop_waits_for_its_proof_item_after_its_result_and_a_dropped_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a stop's route-3 result is not proof. The kill waits for the child_stopped
    item, which follows the child's output in its outbox, across a dropped link."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        stopping: asyncio.Task[None] | None = None
        try:
            await harness.end_turn()
            await harness.wait_settled("initial events did not drain")
            async with paused_outbox(harness.mac):
                stopping = asyncio.create_task(harness.system.kill("c_scripted"))
                await eventually(
                    lambda: any(outcome is not None for _, _, outcome in stop_rows(harness)),
                    "The stop's result did not reach the VPS",
                )
                assert not stopping.done()
                assert harness.native.stop_count == 1
                await harness.endpoint.cut()
                await asyncio.sleep(0.3)
                assert not stopping.done()
                assert harness.native.stop_count == 1
            await asyncio.wait_for(stopping, 5)
            await harness.wait_settled("stop proof did not drain")
            assert not await harness.system.is_running("c_scripted")
            assert harness.native.stop_count == 1
            ((_, state, _),) = stop_rows(harness)
            assert state == "finished"
        finally:
            if stopping is not None and not stopping.done():
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_shutdown_waits_for_admitted_outcome_application_and_final_event_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a kill must not finish before the answer application and the agent's last
    event are accepted, in order, ahead of the stop proof."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        release_application, release_event = threading.Event(), asyncio.Event()
        application_waiting, event_waiting, stopping = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
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

            async def observe_native_stop(command: Any) -> CommandOutcome:
                stopping.set()
                return await stop_exact(command)

            def late_application(
                entry: OutboxEntry, *, ticket_id: str | None = None
            ) -> QueuedEnvelope:
                if entry.kind is EnvelopeKind.answer_application:
                    loop.call_soon_threadsafe(application_waiting.set)
                    assert release_application.wait(10), "application publication not released"
                return publish(entry, ticket_id=ticket_id)

            async def final_native_event() -> None:
                assert harness.native.sink is not None
                await harness.native.sink.report(
                    None, VendorSessionCursorRebound(vendor_session_cursor="cleanup-cursor")
                )
                await native_stop()

            async def withheld_event(
                envelope: QueuedEnvelope, identity: ExecutorIdentity, **kwargs: Any
            ) -> AcceptanceReceipt:
                if envelope.kind is EnvelopeKind.backend_event and envelope.payload.get(
                    "arguments"
                ) == {"vendor_session_cursor": "cleanup-cursor"}:
                    event_waiting.set()
                    await release_event.wait()
                receipt = await accept(envelope, identity, **kwargs)
                if receipt.disposition is ReceiptDisposition.accepted:
                    accepted.append(envelope)
                return receipt

            async def cancel_native() -> None:
                pass

            monkeypatch.setattr(harness.mac.runner, "_stop_exact_child", observe_native_stop)
            monkeypatch.setattr(harness.mac.outbox, "add", late_application)
            monkeypatch.setattr(harness.native, "cancel_running_turn", cancel_native, raising=False)
            monkeypatch.setattr(harness.native, "stop", final_native_event)
            monkeypatch.setattr(harness.endpoint.inbox, "accept", withheld_event)
            assert await post_owner_answer(harness, harness.ask) == {"fate": "saved"}
            await asyncio.wait_for(application_waiting.wait(), 5)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            await asyncio.wait_for(stopping.wait(), 5)
            await asyncio.sleep(0.2)
            assert not event_waiting.is_set() and not killing.done()
            assert harness.native.stop_count == 0
            release_application.set()
            await asyncio.wait_for(event_waiting.wait(), 5)
            await eventually(
                lambda: any(outcome is not None for _, _, outcome in stop_rows(harness)),
                "The stop's result did not reach the VPS",
            )
            assert harness.endpoint.runs.store.child_assignment(reference) is not None
            assert not killing.done()
            release_event.set()
            await asyncio.wait_for(killing, 5)
            await harness.wait_settled("cleanup outbox did not drain")
            assert harness.endpoint.runs.store.child_assignment(reference) is None
            assert harness.events("permission_answered") == 1
            assert harness.native.answers == [("ask_permission", "allow")]
            state = harness.system._conversations["c_scripted"]
            assert state.child is None and not state.child_is_quarantined
            # The kill's stop withdrew the cancel queued behind the answer; a refused
            # cancel retires its child with one more stop. Each stop is proven.
            stops = stop_rows(harness)
            assert all(state == "finished" for _, state, _ in stops)
            proofs = {
                "stopped_" + hashlib.sha256(command_id.encode()).hexdigest()
                for command_id, _, _ in stops
            }
            stop_sequence = min(
                row.sequence
                for row in accepted
                if row.kind is EnvelopeKind.child_stopped and row.submission_id in proofs
            )
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                assert (
                    connection.execute(
                        "SELECT stop_state FROM mac_runs WHERE conversation_id=?",
                        (reference.conversation_id,),
                    ).fetchone()[0]
                    == "closed"
                )
            final_event = next(
                row
                for row in accepted
                if row.kind is EnvelopeKind.backend_event
                and row.payload.get("arguments") == {"vendor_session_cursor": "cleanup-cursor"}
            )
            application = next(
                row for row in accepted if row.kind is EnvelopeKind.answer_application
            )
            assert application.sequence < final_event.sequence < stop_sequence
            for row in (application, final_event):
                # The later stop has advanced the single inbox position.
                assert (
                    lookup_receipt(harness.endpoint.inbox.store, row.sender_id, row.submission_id)
                    is None
                )
        finally:
            release_application.set()
            release_event.set()
            if killing is not None and not killing.done():
                killing.cancel()
                await asyncio.gather(killing, return_exceptions=True)
            await harness.close()

    asyncio.run(run())


def test_old_websocket_is_refused_needs_update_while_update_route_serves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a protocol-21 Mac dials the old WebSocket. It must be refused and shown as
    "Mac needs update", its update route must still answer it with only the ID header,
    and the current Mac's work must go on: nothing it saved changes, and its next lease
    shows it present again."""
    import aiohttp

    from planner.environments.mac_updates import MacUpdateService
    from planner.environments.update_attempts import FileUpdateAttemptStore
    from planner.execution_identity import MAC_NEEDS_UPDATE
    from planner.executor.protocol import decode_bounded
    from planner.executor.update_contracts import MacUpdateCheck

    fast_leases(monkeypatch)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            harness.transport_app.state.mac_updates = MacUpdateService(
                tmp_path / "mac-updates",
                FileUpdateAttemptStore(tmp_path / "update-attempt.json"),
                None,
                "a" * 40,
                lambda: None,
            )
            origin = harness.proxy.origin
            only_id = {"X-Panels-Executor-ID": EXECUTOR_ID}

            def positions() -> list[tuple[Any, ...]]:
                with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                    return [
                        tuple(row)
                        for row in connection.execute(
                            "SELECT * FROM mac_inbox_positions ORDER BY sender_id,conversation_id"
                        )
                    ]

            before = positions()
            async with harness.proxy.session_factory() as session:
                try:
                    async with session.ws_connect(
                        "wss" + origin[5:] + "/api/executor/connect", headers=only_id
                    ):
                        raise AssertionError("the old WebSocket was accepted")
                except aiohttp.WSServerHandshakeError as refused:
                    assert refused.status == 403
                health = harness.endpoint.health()
                assert health.presence == "refused"
                assert health.refusal is not None
                assert health.refusal.reason == MAC_NEEDS_UPDATE
                assert health.refusal.peer_identity is None
                async with session.get(origin + "/api/executor/update", headers=only_id) as answer:
                    assert answer.status == 200
                    check = decode_bounded(await answer.read(), MacUpdateCheck)
                assert check.target is None
            assert positions() == before
            # The current Mac's next accepted lease clears the refusal.
            await eventually(
                lambda: harness.endpoint.health().presence == "present",
                "The current Mac's lease did not clear the old Mac's refusal",
            )
            assert harness.endpoint.health().identity == harness.mac.identity
            await harness.end_turn()
            delivery = await harness.system.send(
                "c_scripted", text_message_content("Still working"), sender_label="Test"
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            await eventually(lambda: harness.native.prompt_count == 2, "The prompt did not run")
            assert harness.native.start_count == 1
            await harness.wait_settled("The current Mac's work did not settle")
        finally:
            await harness.close()

    asyncio.run(run())


async def release_held_poll(harness: ExecutorHarness) -> None:
    """While the Mac cannot be reached, end the long poll the VPS still holds from
    before, so later commands stay waiting: a put wakes it, and its reply is lost."""
    from planner.executor.protocol import ModelListRequest

    placed = await harness.endpoint.mailbox.put_model_list_request(
        ModelListRequest(ConversationBackendKey.codex.value, False)
    )
    await eventually(
        lambda: (placed.command_id, "delivered") in harness.mailbox_commands("read_model_list"),
        "the held poll was not answered",
    )


async def raise_kind(harness: ExecutorHarness, kind: AskKind) -> AskReference:
    if kind is AskKind.permission:
        await harness.native.raise_ask()
        await harness.wait_ask_reachable()
        return harness.ask
    await harness.native.raise_user_input()
    await harness.wait_input_reachable()
    return harness.input_ask


def test_claude_configuration_refusal_rebinds_only_after_exact_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a Claude child keeps the model and effort it started with, and a prompt is
    accepted at its commit, so the VPS refuses a change itself (NeedsRebind) and writes
    no prompt row for the old child. The core then rebinds: the replacement starts only
    after the exact old child's cleanup, with the old assignment kept until then, and
    the prompt is framed once for the real Claude adapter."""
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
            # The scripted client also lists a one-block streamed message as a prompt.
            return [
                "\n".join(block["text"] for block in message["message"]["content"])
                for message in client.streamed_messages
            ]

        def old_child_prompt_rows(child_id: str) -> int:
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                return int(
                    connection.execute(
                        "SELECT count(*) FROM executor_commands "
                        "WHERE operation='write_prompt' AND child_id=?",
                        (child_id,),
                    ).fetchone()[0]
                )

        sending = None
        conn = connect(harness.endpoint.db_path)
        try:
            await eventually(lambda: bool(clients) and bool(prompts(clients[0])), "first prompt")
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
                await harness.wait_settled("result receipt did not drain")

            for index in (1, 2):
                await finish_turn()
                old = clients[-1]
                old_prompt = prompts(old)[-1]
                old_reference = harness.child.reference
                prompt_rows = old_child_prompt_rows(old_reference.child_id)
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
                # The stop waits for its proof, so the rebind waits for the cleanup.
                await asyncio.sleep(0.1)
                assert not sending.done()
                assert len(clients) == index
                assert len(prompts(old)) == 1 and prompts(old)[-1] == old_prompt
                assert not old.disconnected
                assert harness.endpoint.runs.store.child_assignment(old_reference) is not None
                cleanup_release.set()
                await asyncio.wait_for(sending, 5)
                # The prompt is accepted at its commit; the Mac then runs it.

                def replaced(count: int = index + 1) -> bool:
                    return len(clients) == count and len(prompts(clients[-1])) == 1

                await eventually(replaced, "the replacement did not receive its prompt")
                await harness.wait_settled("rebind outcomes did not drain")
                replacement = clients[-1]
                assert old.disconnected
                assert replacement.options.resume == session
                assert replacement.options.model == "claude-new"
                assert replacement.options.effort == ("low" if index == 1 else "high")
                assert harness.endpoint.runs.store.child_assignment(old_reference) is None
                # The refused prompt was never put for the old child.
                assert old_child_prompt_rows(old_reference.child_id) == prompt_rows
                prompt = prompts(replacement)[0]
                assert prompt.startswith(NATIVE_WORKER_CONTEXT)
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
    """Gap: exact model and effort cross the lifecycle, the mailbox and TLS to the native
    write without any catalog read."""
    import time

    from tests.support.executor_catalog import ScriptedBackendSnapshots

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
            conversation_id = delivery.conversation_id
            assert conversation_id == "c_combined_admission"
            assert isinstance(delivery.fate, PromptDeliveryStarted), delivery.fate
            await eventually(
                lambda: second.start_count == second.prompt_count == 1, "first dispatch"
            )
            assert len(second.delivered_text) == 1
            assert exact_text in second.delivered_text[0]
            selected = data.read_ticket(conn, ticket.id)
            assert selected.employee_launch_model == "scripted"
            assert selected.employee_backend == "codex"
            assert observer.calls == 0

            async def finish_turn() -> None:
                assert second.sink is not None
                await second.end_turn()
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
                assert isinstance(changed.fate, PromptDeliveryStarted), changed.fate

                def dispatched(count: int = index) -> bool:
                    return second.prompt_count == count

                await eventually(dispatched, "the changed prompt was not dispatched")
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
    """Gap: live text goes over route 8 straight to watchers, never into either store;
    a live request lost with the link is dropped, while the completed message travels
    the outbox and replays once; a retired child's live text reaches no one."""
    from planner.conversation.events import (
        AgentMessageDeltaFrame,
        ModelThinkingFrame,
        ToolCallProgressFrame,
    )
    from planner.conversation.live_tail import ConversationLiveTail

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            tail = ConversationLiveTail()
            monkeypatch.setattr(harness.system, "_live_tail", tail)
            sink, turn = harness.native.sink, harness.native.turn
            assert sink is not None and turn is not None
            minted: list[QueuedEnvelope] = []
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
                    async with mac_unreachable(harness.proxy):
                        # One live request in flight while the link is gone: it is
                        # dropped when the link fails, never resent.
                        await sink.report(turn, AgentMessageDelta(text_delta="Lost text"))
                        await asyncio.sleep(0.2)
                        assert not minted and not harness.mac.outbox.pending()
                        await sink.report(
                            turn, AgentMessageCompleted(content=text_message_content("Full reply"))
                        )
                        pending = harness.mac.outbox.pending()
                        assert len(pending) == 1
                    await eventually(
                        lambda: not harness.mac.outbox.pending(), "Completed reply did not replay"
                    )
                    # Nothing shown while disconnected is replayed to live watchers.
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(watcher.next_item(), 0.3)
                    replayed = lookup_receipt(
                        harness.endpoint.inbox.store,
                        pending[0].sender_id,
                        pending[0].submission_id,
                    )
                    assert replayed is not None and replayed.result == {"applied": True}
            await harness.system.kill("c_scripted")
            fate = await harness.system.send(
                "c_scripted", text_message_content("Start again"), sender_label="Test"
            )
            assert isinstance(fate, PromptDeliveryStarted)
            await eventually(lambda: harness.native.prompt_count == 2, "restart prompt")
            current_sink, current_turn = harness.native.sink, harness.native.turn
            assert current_sink is not None and current_turn is not None
            assert current_sink is not sink
            with tail.subscribe("c_scripted") as watcher:
                # Even a current turn token cannot give a retired child a destination.
                await sink.report(current_turn, AgentMessageDelta(text_delta="Retired child"))
                await asyncio.sleep(0.2)
                await current_sink.report(current_turn, AgentMessageDelta(text_delta="New child"))
                assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                    text_delta="New child"
                )
        finally:
            await harness.close()

    asyncio.run(run())


def test_permanent_refusals_receive_durable_receipts_and_release_conversation_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gap: items the VPS refuses for good (a malformed stop, a write by an unassigned
    child, a write the VPS refuses after its files arrived) each get a durable refused
    receipt over route 4, so the next item of the conversation is applied."""
    from planner.executor.protocol import CliWrite, as_object

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            child = harness.child.reference
            native_child = harness.child
            assert native_child.ticket_id is not None
            artifact = harness.mac.paths.artifacts / native_child.ticket_id / "queued.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("bytes published before a queued payload refusal")
            acknowledged: dict[str, ReceiptDisposition] = {}
            acknowledge = harness.mac.outbox.acknowledge

            def recorded(receipt: AcceptanceReceipt) -> None:
                acknowledged[receipt.submission_id] = receipt.disposition
                acknowledge(receipt)

            monkeypatch.setattr(harness.mac.outbox, "acknowledge", recorded)
            async with paused_outbox(harness.mac):
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
            # The Mac does not keep a stop's receipt; it was handed over once, refused.
            assert acknowledged[malformed.submission_id] is ReceiptDisposition.refused
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
            # Only the last position stays on the VPS; earlier sequences get the plain
            # acknowledgment, never a second effect.
            for envelope in (malformed, unassigned, assigned_refusal):
                stale = await harness.endpoint.inbox.accept(envelope, harness.mac.identity)
                assert stale.disposition is ReceiptDisposition.accepted and stale.result == {}
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                rows = connection.execute(
                    "SELECT payload FROM conversation_events WHERE conversation_id=? "
                    "AND kind='tool_call_finished'",
                    (child.conversation_id,),
                ).fetchall()
                assert [row for row in rows if "outbox_refusal_" in row[0]] == []
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


@pytest.mark.parametrize("invalid_descriptor", ["mismatched", "traversal"])
def test_artifact_descriptor_must_validate_before_publication_and_backend_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_descriptor: str
) -> None:
    """Gap: an upload whose descriptor (route 5's FileUploadRequest header) names another
    Ticket or escapes the Ticket's folder is refused for good before any byte is
    published; its item carries the refusal and is refused with no backend row, and the
    next item with a valid descriptor publishes. Other routes keep answering."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            reject = True
            upload_file = harness.mac.client.upload_file
            refused_uploads: list[FileUploadRequest] = []

            async def corrupt_descriptor(upload: FileUploadRequest, source: Path) -> None:
                if reject:
                    manifest = (
                        replace(upload.manifest, owner_id="t_another_ticket")
                        if invalid_descriptor == "mismatched"
                        else replace(upload.manifest, relative_path="../escape")
                    )
                    upload = replace(upload, manifest=manifest)
                    refused_uploads.append(upload)
                await upload_file(upload, source)

            monkeypatch.setattr(harness.mac.client, "upload_file", corrupt_descriptor)
            ticket_id = harness.child.ticket_id
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
                    detail="The file needs an authorized upload.",
                ),
            )
            (item,) = harness.mac.outbox.pending()
            await eventually(lambda: not harness.mac.outbox.pending(), "Refused item did not drain")
            assert len(refused_uploads) == 1
            receipt = lookup_receipt(
                harness.endpoint.inbox.store, item.sender_id, item.submission_id
            )
            assert receipt is not None and receipt.disposition is ReceiptDisposition.refused
            target = tmp_path / "vps/files/tickets" / ticket_id / "typed-ack.bin"
            assert not target.exists()
            assert not (tmp_path / "vps/files/tickets" / "escape").exists()
            assert not list((tmp_path / "vps").glob("executor-uploads/*"))
            assert harness.events("tool_call_finished") == 0
            assert artifact.read_bytes() == contents
            listed = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert listed.available_models
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
                "Corrected descriptor did not allow the dependent event",
            )
            assert target.read_bytes() == contents
            await harness.wait_settled("Artifact receipt did not drain")
        finally:
            await harness.close()

    asyncio.run(run())


def test_authenticated_catalog_exchange_timeout_preserves_active_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gap: a model-list read the Mac does not answer in time keeps the live child and
    the last saved list; a new Mac process refreshes every list; a partial list keeps
    the last complete one; and a reader with only SQLite returns the saved list."""
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    from planner.executor import catalog_relay
    from planner.executor.catalog_relay import VpsCatalogRelay
    from planner.executor.vps_mailbox import SqliteCommandMailbox
    from planner.executor.vps_presence import MacPresenceService
    from planner.executor.vps_runs import MacRuns

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
            starts = harness.native.start_count
            prompts = harness.native.prompt_count
            child = harness.child.reference
            source.block = True
            with monkeypatch.context() as timeout_patch:
                timeout_patch.setattr(catalog_relay, "MODEL_LIST_LIMIT_SECONDS", 0.2)
                retained = await harness.endpoint.catalog.snapshot(
                    ConversationBackendKey.codex, refresh=True
                )
            assert retained == first
            assert harness.endpoint.health().presence == "present"
            assert harness.native.start_count == starts
            assert harness.native.prompt_count == prompts
            assert harness.native.stop_count == 0
            assert harness.endpoint.runs.store.child_assignment(child) is not None
            # The read the Mac never answered was withdrawn, not left waiting.
            read_states = {state for _, state in harness.mailbox_commands("read_model_list")}
            assert "waiting" not in read_states
            source.block = False
            source.model = "reconnected-model"
            await harness.stop_mac()
            await harness.start_mac(harness.new_mac())
            await eventually(
                lambda: all(
                    (saved := harness.endpoint.catalog.retained(backend)) is not None
                    and saved.available_models[0].model_id == "reconnected-model"
                    for backend in ConversationBackendKey
                ),
                "A new Mac process did not refresh the machine lists",
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
            # A new VPS reader has only SQLite, no in-memory copy or Mac.
            db_path = harness.endpoint.db_path
            mailbox = SqliteCommandMailbox(db_path)
            presence = MacPresenceService(db_path, mailbox)
            runs = MacRuns(db_path, presence, mailbox)
            another_mac = VpsCatalogRelay(runs, presence, mailbox, machine_id="other-mac")
            assert another_mac.retained(ConversationBackendKey.codex) is None
            offline = VpsCatalogRelay(runs, presence, mailbox)
            saved = offline.retained(ConversationBackendKey.codex)
            assert saved is not None
            assert saved.available_models == first.available_models
            assert saved.default_model_id == first.default_model_id
            await mailbox.close()
            await presence.close()
        finally:
            await harness.close()

    asyncio.run(run())


def test_vps_core_recreation_keeps_mac_run_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a VPS restart keeps the Mac run (D's live adoption over the real mailbox):
    no stop reaches the Mac, the ask raised before the restart is answered after it and
    reaches the agent once, an event after the restart applies to the same turn, and
    the next send goes to the same child."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            await harness.native.raise_ask()
            await harness.wait_ask_reachable()
            ask = harness.ask
            child = harness.child.reference
            await harness.restart_vps_core()
            state = harness.system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            assert await harness.system.is_running("c_scripted")
            assert await harness.system.has_pending_permission_ask("c_scripted")
            assert (
                await harness.system.answer_permission_ask(
                    "c_scripted", public_ask_id(ask), "allow"
                )
                is AnswerFate.saved
            )
            await eventually(lambda: harness.answer_applied(ask), "answer was not applied")
            assert harness.native.answers == [("ask_permission", "allow")]
            assert harness.native.sink is not None and harness.native.turn is not None
            await harness.native.sink.report(
                harness.native.turn,
                ToolCallStarted(
                    tool_call_id="after", title="After restart", tool_kind="other", detail=None
                ),
            )
            await eventually(
                lambda: harness.events("tool_call_started") == 1, "event after restart"
            )
            await harness.end_turn()
            delivery = await harness.system.send(
                "c_scripted", text_message_content("Fresh"), sender_label="Test"
            )
            assert isinstance(delivery, PromptDeliveryStarted)
            await eventually(lambda: harness.native.prompt_count == 2, "next prompt")
            await harness.wait_settled("the run did not settle")
            assert harness.native.start_count == 1 and harness.native.stop_count == 0
            assert harness.child.reference == child
            assert harness.native.answers == [("ask_permission", "allow")]
            assert harness.mailbox_commands("stop") == []
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_durable_answer_saved_while_the_mac_is_unreachable_is_delivered_after_vps_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    """Gap: an answer is a mailbox row, so one saved while the Mac cannot be reached
    survives a VPS restart and reaches the agent exactly once when the Mac returns; a
    second answer to the same ask is refused."""

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            ask = await raise_kind(harness, kind)
            async with mac_unreachable(harness.proxy):
                await release_held_poll(harness)
                assert await post_owner_answer(harness, ask) == {"fate": "saved"}
                answer = harness.saved_answer(ask)
                assert answer is not None
                operation = (
                    "answer_permission_ask" if kind is AskKind.permission else "answer_user_input"
                )
                assert harness.mailbox_commands(operation) == [(answer.answer_id, "waiting")]
                await harness.restart_vps_core()
                assert harness.native.answers == [] and harness.native.user_answers == []
            await eventually(lambda: harness.answer_applied(ask), "answer was not applied")
            await harness.wait_settled("the answer did not settle")
            if kind is AskKind.permission:
                assert harness.native.answers == [("ask_permission", "allow")]
                assert harness.native.user_answers == []
            else:
                assert harness.native.user_answers == [("request_input", ORDERED_INPUT_ANSWERS)]
                assert harness.native.answers == []
            assert harness.mailbox_commands(operation) == [(answer.answer_id, "finished")]
            assert harness.saved_answer(ask) == answer
            assert await post_owner_answer(harness, ask) == {"fate": "refused"}
            await asyncio.sleep(0.2)
            # The refused answer wrote no row, and the applied one is not run again.
            assert len(harness.native.answers) + len(harness.native.user_answers) == 1
            assert harness.events("answer_applied") == 1
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_local_cancel_revokes_saved_answer_before_native_wait_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    """Gap: an answer saved while the Mac is unreachable, then revoked by a local cancel
    whose attempt committed but whose mailbox row never did (the VPS crashed first),
    never reaches the agent after the restart, and an ask the agent raised after the
    revocation never becomes answerable.

    Adapted: a VPS restart no longer stops the Mac child. The run is adopted live with
    its turn's callbacks revoked (the cancel attempt is in the facts), the saved answer's
    row expires at fetch because its request is no longer open, and the turn keeps
    running on the Mac until the owner interrupts again, which now reaches the agent
    through the mailbox and ends the turn with nothing left open.
    """

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        native_cancels: list[str] = []
        cancel_entered = asyncio.Event()
        interrupt = None

        async def native_cancel() -> None:
            native_cancels.append("cancel")

        monkeypatch.setattr(harness.native, "cancel_running_turn", native_cancel, raising=False)
        try:
            ask = await raise_kind(harness, kind)
            operation = (
                "answer_permission_ask" if kind is AskKind.permission else "answer_user_input"
            )
            async with mac_unreachable(harness.proxy):
                await release_held_poll(harness)
                assert await post_owner_answer(harness, ask) == {"fate": "saved"}
                answer = harness.saved_answer(ask)
                assert answer is not None
                assert harness.mailbox_commands(operation) == [(answer.answer_id, "waiting")]
                old_system = harness.system
                state = old_system._conversations["c_scripted"]
                proxy_child = state.child
                assert proxy_child is not None

                async def stalled_cancel() -> None:
                    # The cancel attempt has committed; its mailbox row never does.
                    cancel_entered.set()
                    await asyncio.Event().wait()

                monkeypatch.setattr(proxy_child, "cancel_running_turn", stalled_cancel)
                interrupt = asyncio.create_task(old_system.interrupt("c_scripted"))
                await asyncio.wait_for(cancel_entered.wait(), 2)
                assert not harness.answer_applied(ask)
                assert state.running_turn is not None and state.running_turn.callbacks_revoked
                # A callback announced after revocation cannot create a newly answerable ask.
                await harness.native.raise_user_input("request_late")
                # Simulate a crash without permitting the pending cancellation to finish.
                for settlement in tuple(state.settlement_tasks):
                    settlement.cancel()
                await asyncio.gather(*tuple(state.settlement_tasks), return_exceptions=True)
                interrupt.cancel()
                await asyncio.gather(interrupt, return_exceptions=True)
                await harness.restart_vps_core()
                assert await post_owner_answer(harness, ask) == {"fate": "refused"}
                assert harness.saved_answer(ask) == answer
            await harness.wait_settled("the late callback and the answer row did not settle")
            assert harness.mailbox_commands(operation) == [(answer.answer_id, "expired")]
            assert harness.native.answers == [] and harness.native.user_answers == []
            assert harness.events("answer_applied") == 0
            assert not await harness.system.has_pending_user_input("c_scripted")
            assert not await harness.system.has_pending_permission_ask("c_scripted")
            state = harness.system._conversations["c_scripted"]
            assert not state.child_is_quarantined
            assert state.running_turn is not None and state.running_turn.callbacks_revoked
            assert native_cancels == [] and harness.native.stop_count == 0
            # Interrupting again reaches the agent through the mailbox.
            await asyncio.wait_for(harness.system.interrupt("c_scripted"), 5)
            await eventually(lambda: native_cancels == ["cancel"], "the cancel did not arrive")
            await harness.wait_settled("the cancel did not settle")
            assert not await harness.system.is_running("c_scripted")
            assert harness.native.answers == [] and harness.native.user_answers == []
            assert not harness.answer_applied(ask)
            assert harness.native.stop_count == 0
        finally:
            if interrupt is not None and not interrupt.done():
                interrupt.cancel()
            await harness.close()

    asyncio.run(run())
