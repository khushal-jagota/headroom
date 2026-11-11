"""Compose the VPS executor owners and adapt the executor routes to their typed inputs.

Every route is its own HTTPS request, judged by the IDs it carries. No request waits
on a conversation lock, and a dropped request never cancels a save: the lease, result
recording, item batches and upload staging run in tasks this object owns, which only
shutdown cancels.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Any, cast

from starlette.types import ASGIApp

from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, PlannerError, PrincipalKind
from planner.core.db import connect
from planner.core.task_settlement import run_on_worker_thread, wait_for_settlement
from planner.execution_identity import ExecutorIdentity
from planner.executor.catalog_relay import VpsCatalogRelay
from planner.executor.configuration import (
    COMMAND_OUTCOME_SERVER_LIMIT_SECONDS,
    COMMAND_POLL_HOLD_SECONDS,
    LEASE_SERVER_LIMIT_SECONDS,
    LIVE_EVENT_SERVER_LIMIT_SECONDS,
    LIVE_READ_SERVER_LIMIT_SECONDS,
    MAX_COMMAND_OUTCOMES_PER_REPORT,
    MAX_LIVE_EVENTS_PER_BATCH,
)
from planner.executor.contracts import ExecutorHealthRead, StallSource
from planner.executor.protocol import (
    LIVE_BACKEND_EVENT_METHODS,
    CommandOutcomeReply,
    CommandOutcomeReport,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    JsonObject,
    LeaseRefused,
    LeaseReply,
    LeaseRequest,
    LiveEventBatch,
    LiveReadRequest,
    MailboxPollReply,
    WorkspaceRetirementReply,
    as_object,
    decode_event_arguments,
)
from planner.executor.vps_files import InputFile, VpsFiles
from planner.executor.vps_inbox import MacInbox
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService, UpdateCheck
from planner.executor.vps_presence_store import require_current_incarnation_in_transaction
from planner.executor.vps_runs import MacRuns
from planner.ticket_workspaces.contracts import WorkspaceState
from planner.ticket_workspaces.data import authorize_retirement, has_committed_retirement

# GET-only Panels reads an agent on the Mac may make.
_READABLE_PATHS = (
    "/api/tickets",
    "/api/managers",
    "/api/sprint",
    "/api/projects",
    "/api/worker-types",
    "/api/skills",
    "/files/sprint-items/",
    "/files/managers/",
)


class VpsExecutor:
    """The VPS half: presence, mailbox, runs, inbox, files, catalog."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        machine_id: str = "macos",
        application_revision: str = "unknown",
    ) -> None:
        self.db_path = db_path
        self.clock = clock
        self.application_revision = application_revision
        self.mailbox = SqliteCommandMailbox(db_path)
        self.presence = MacPresenceService(
            db_path, self.mailbox, application_revision=application_revision
        )
        self.runs = MacRuns(db_path, self.presence, self.mailbox)
        self.inbox = MacInbox(db_path, clock, self.runs)
        self.catalog = VpsCatalogRelay(
            self.runs, self.presence, self.mailbox, machine_id=machine_id
        )
        self.message_files = ConversationMessageFiles(db_path)
        self.files = VpsFiles(db_path, self.inbox, self.mailbox, self.message_files)
        self.presence.incarnation_replaced = self.runs.incarnation_replaced
        self.presence.returned = self.catalog.connected
        self.conversations: SqliteProcessConversationSystem
        self.app: ASGIApp
        self._owned: set[asyncio.Task[Any]] = set()

    def bind_conversations(self, conversations: SqliteProcessConversationSystem) -> None:
        self.conversations = conversations
        self.runs.conversations = conversations
        self.inbox.conversations = conversations
        self.inbox.agent_writes.conversations = conversations

    def configure(
        self,
        conversations: SqliteProcessConversationSystem,
        app: ASGIApp,
        *,
        update_check: UpdateCheck | None = None,
    ) -> None:
        self.bind_conversations(conversations)
        self.app = app
        if update_check is not None:
            self.presence.update_check = update_check

    async def start(self) -> None:
        """After the database is migrated: read presence and the mailbox's high sequence
        into memory, and resume the drains of runs of a replaced Mac process."""
        await run_on_worker_thread(self.presence.record)
        await run_on_worker_thread(self.mailbox.high_sequence)
        await self.runs.resume_replacement_drains()

    async def close(self) -> None:
        for task in tuple(self._owned):
            task.cancel()
        for task in tuple(self._owned):
            await wait_for_settlement(task)
        await self.runs.close()
        await self.catalog.close()
        await self.mailbox.close()
        await self.presence.close()

    async def _owned_work[Result](
        self, work: Coroutine[Any, Any, Result], *, answer_within: float | None = None
    ) -> Result:
        """Run work to its end even if the request that asked for it goes away. With
        answer_within, the request is answered retry later past that limit while the work
        goes on; its resend finds what it saved."""
        task = asyncio.create_task(work)
        self._owned.add(task)
        task.add_done_callback(self._owned.discard)
        try:
            async with asyncio.timeout(answer_within):
                return await asyncio.shield(task)
        except TimeoutError as error:
            raise PlannerError(ErrorCode.retry_later, "the VPS is still saving this") from error

    # --- health --------------------------------------------------------------------------

    def health(self) -> ExecutorHealthRead:
        return self.presence.health()

    def health_payload(self) -> JsonObject:
        """GET /api/executor/health's fields after "mode"."""
        return as_object(self.health())

    def stall_sources(self) -> tuple[StallSource, ...]:
        return (
            self.mailbox.stall_source(),
            self.runs.stall_source(),
            self.presence.stall_source(),
        )

    # --- routes --------------------------------------------------------------------------

    def record_contact(self) -> None:
        self.presence.record_contact()

    def require_current(
        self, *, incarnation_header: str | None, protocol_header: str | None
    ) -> ExecutorIdentity:
        return self.presence.require_current(
            incarnation_header=incarnation_header, protocol_header=protocol_header
        )

    async def lease(self, request: LeaseRequest) -> LeaseReply | LeaseRefused:
        """Route 1."""
        return await self._owned_work(
            self.presence.accept_lease(request), answer_within=LEASE_SERVER_LIMIT_SECONDS
        )

    async def retire_workspace(
        self, identity: ExecutorIdentity, state: WorkspaceState, *, replay: bool = False
    ) -> WorkspaceRetirementReply:
        return await self._owned_work(
            run_on_worker_thread(self._retire_workspace, identity, state, replay),
            answer_within=LIVE_READ_SERVER_LIMIT_SECONDS,
        )

    def _retire_workspace(
        self, identity: ExecutorIdentity, state: WorkspaceState, replay: bool
    ) -> WorkspaceRetirementReply:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            require_current_incarnation_in_transaction(connection, identity.executor_incarnation)
            authorized = (
                has_committed_retirement(connection, state)
                if replay
                else authorize_retirement(connection, state, self.clock.now_unix())
            )
            connection.commit()
            return WorkspaceRetirementReply(authorized)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def refuse_old_websocket(self) -> None:
        """Route 10."""
        await self._owned_work(self.presence.refuse_old_websocket())

    async def poll(
        self, identity: ExecutorIdentity, after: int, wait_seconds: float
    ) -> MailboxPollReply:
        """Route 2. A poll lost while held marked nothing; one lost after marking is
        answered again by the next poll, which asks from the same cursor."""
        if after < 0 or not 0 <= wait_seconds <= COMMAND_POLL_HOLD_SECONDS:
            raise PlannerError(ErrorCode.validation, "invalid poll cursor or wait")
        return await self.mailbox.poll(identity, after, wait_seconds)

    async def record_outcomes(
        self, identity: ExecutorIdentity, report: CommandOutcomeReport
    ) -> CommandOutcomeReply:
        """Route 3."""
        if len(report.outcomes) > MAX_COMMAND_OUTCOMES_PER_REPORT:
            raise PlannerError(ErrorCode.validation, "too many results in one report")
        return await self._owned_work(
            self.mailbox.record_results(identity, report),
            answer_within=COMMAND_OUTCOME_SERVER_LIMIT_SECONDS,
        )

    async def accept_items(
        self, identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        """Route 4."""
        reply = await self._owned_work(self.inbox.accept_batch(conversation_id, batch, identity))
        if reply.receipts:
            self.presence.note_conversation_activity(conversation_id)
        return reply

    async def upload(
        self,
        identity: ExecutorIdentity,
        sha256: str,
        upload: FileUploadRequest,
        content_length: int | None,
        chunks: AsyncIterator[bytes],
    ) -> None:
        """Route 5."""
        await self._owned_work(self.files.upload(identity, sha256, upload, content_length, chunks))

    async def input_file(
        self, identity: ExecutorIdentity, command_id: str, stored_file_id: str
    ) -> InputFile:
        """Route 6."""
        return await self.files.input_file(identity, command_id, stored_file_id)

    async def read(self, identity: ExecutorIdentity, read: LiveReadRequest) -> JsonObject:
        """Route 7: an allowlisted GET, as the child's own principal."""
        del identity
        if not read.path.startswith(_READABLE_PATHS):
            raise PlannerError(ErrorCode.validation, "unsupported executor read")
        import httpx

        from planner.core.contracts import principal_legacy_actor

        principal = await run_on_worker_thread(self.runs.authority, read.child)
        headers = {"X-Plan-Actor": principal_legacy_actor(principal)}
        if principal.kind is PrincipalKind.ticket:
            headers["X-Plan-Ticket-ID"] = principal.id
        try:
            async with asyncio.timeout(LIVE_READ_SERVER_LIMIT_SECONDS):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app), base_url="http://executor"
                ) as client:
                    response = await client.get(
                        read.path, params=cast(Any, read.params), headers=headers
                    )
        except TimeoutError as error:
            raise PlannerError(ErrorCode.retry_later, "the read took too long") from error
        try:
            value = response.json()
        except ValueError:
            return {"text": response.text}
        return value if isinstance(value, dict) else {"data": value}

    async def live(
        self, identity: ExecutorIdentity, conversation_id: str, batch: LiveEventBatch
    ) -> None:
        """Route 8: lossy live text, straight to the child's event sink. Live text from a
        child of another process, or of another conversation, is dropped."""
        if len(batch.events) > MAX_LIVE_EVENTS_PER_BATCH or any(
            event.method not in LIVE_BACKEND_EVENT_METHODS for event in batch.events
        ):
            raise PlannerError(ErrorCode.validation, "a live batch carries only live events")
        try:
            async with asyncio.timeout(LIVE_EVENT_SERVER_LIMIT_SECONDS):
                for event in batch.events:
                    if (
                        event.child.executor_incarnation != identity.executor_incarnation
                        or event.child.conversation_id != conversation_id
                    ):
                        continue
                    child = self.runs.children.get(event.child.child_id)
                    if child is None or child.reference != event.child or child.event_sink is None:
                        continue
                    await child.event_sink.report(event.turn, decode_event_arguments(event))
        except TimeoutError:
            return  # Live text is lossy; the ordered items carry what matters.
        if batch.events:
            self.presence.note_conversation_activity(conversation_id)
