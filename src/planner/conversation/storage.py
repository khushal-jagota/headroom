"""SQLite ownership for conversations and the rows they record.

Calls open their own connection inside a worker thread and close it before returning.
Conversation delivery never receives or owns an application transaction.

Appending a row is one immediate transaction: take the write lock, read where the
conversation's record has got to, insert the next row, move the conversation's marker
forward, commit. Two writers cannot both decide they own the same sequence number,
because the second one waits for the first one's lock.

The commit announces itself on the change signal only when the rows it wrote are ones a
screen outside the conversation reads. See ``_commit_appended_rows``.

Rows are written once. Nothing here updates or deletes a row it has written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Collection, Coroutine, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import IO, Any, cast
from uuid import uuid4

from planner.conversation.backends.contracts import BackendSpawnFailed, TurnToken
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    DeploymentRecoveryReference,
    PromptDeliveryMode,
    PromptDispatchDecision,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.deploy_interruptions import data as interruption_data
from planner.conversation.deploy_interruptions.files import read_manifests
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventKind,
    ConversationEventPayload,
    ExplicitReplyMissingEventPayload,
    MessageToOwnerEventPayload,
    ModelChangedEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    conversation_event_kinds_need_the_change_signal,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.lifecycle import (
    ConversationTransactionParticipant,
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
    LifecycleTransition,
)
from planner.conversation.lifecycle_storage import (
    acquire_runtime_ownership,
    apply_facts,
    participate,
    read_facts,
    validate_runtime_ownership,
)
from planner.conversation.publication.coordinator import (
    SignalAdmission,
    SignalPublicationCoordinator,
)
from planner.conversation.publication.mutations import SignalRuntimeMutation
from planner.conversation.views.writer import append_projection
from planner.core.commit_capture import register_capture_task, run_in_capture_thread
from planner.core.config import CONVERSATION_WRITE_LOCK_RETRY_SECONDS
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import commit_without_change_signal, connect
from planner.executor.contracts import ChildReference
from planner.executor.store import ExecutorStore
from planner.manager_wakes import data as manager_wakes_data
from planner.notifications.attention import (
    ConversationAttentionSnapshot,
    advance_conversation_attention,
    capture_conversation_attention,
    conversation_attention_snapshot,
    conversation_attention_snapshot_is_current,
)
from planner.skill_versions import settle_worker_step_skill_bindings

DEFAULT_BUSY_TIMEOUT_MILLISECONDS = 5000


class ConversationRecordMissing(RuntimeError):
    """A conversation's own row is not there, so its record has nowhere to go.

    Rows are numbered per conversation and the conversation's row is what holds the
    number, so appending without it is not something to paper over.
    """


class ConversationRecordNamesNoModel(BackendSpawnFailed):
    """A conversation's row names no model, so it cannot say what to start again on.

    Every conversation is created with a model named, so this is a row from before that
    was so. Nobody knows what it ran on — the backend chose and never wrote it down — so
    it is not repaired: putting a model in its mouth now would make the record say
    something untrue.

    What it stops is starting a child, and only that. Reading such a row is fine and has
    to be: the record is the account of what happened, those rows are part of what
    happened, and a screen that shows a conversation must be able to show them. So this
    is raised where a start is asked for rather than where a row is read, and it is a
    spawn failure because that is exactly what it is — a conversation that cannot be
    started refuses the message rather than breaking the page that lists it.
    """


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """A conversation as it is stored: what it was started with, and where it is now.

    ``model`` and ``reasoning_effort`` are the current values, which a delivery carrying a
    change moves. ``vendor_session_cursor`` is the backend's own session identity — it is
    internal, it is rebindable, and it is what a lazy resume starts from.
    ``composer_catalog`` is what the backend last said the composer can offer for this
    agent, kept here so it is still there when no child is.
    """

    conversation_id: str
    backend_key: ConversationBackendKey
    # Null only on a row written before a model was required. Nothing new can be, and the
    # rows that are cannot start a child — but they are still part of the record.
    model: str | None
    reasoning_effort: str | None
    workspace_folder: Path
    role_text: str | None
    identity_environment_variables: tuple[tuple[str, str], ...]
    access: ConversationAccess
    vendor_session_cursor: str | None
    composer_catalog: tuple[ComposerCatalogEntry, ...]
    latest_sequence: int
    latest_agent_activity_at: int | None
    latest_agent_activity_sequence: int
    automatically_compacted_through_sequence: int
    automatic_compaction_attempted_through_sequence: int
    owner_read_through_sequence: int
    created_at: int
    workspace_path_policy: WorkspacePathPolicy = WorkspacePathPolicy.legacy_home_mapping

    def resolved_start(self) -> ResolvedConversationStart:
        """The start this conversation was created from, rebuilt from its row.

        A child spawned long after the conversation was created — a lazy resume, or a
        respawn after the janitor stopped an idle one — is started from this, so the row
        has to be able to answer for the start request that is long gone.
        """
        role_materials = (
            None
            if self.role_text is None
            else ConversationRoleMaterials(
                role_text=self.role_text,
                identity_environment_variables=self.identity_environment_variables,
            )
        )
        if self.model is None:
            raise ConversationRecordNamesNoModel(self.conversation_id)
        return ResolvedConversationStart(
            conversation_id=self.conversation_id,
            backend_key=self.backend_key,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            role_materials=role_materials,
            workspace_folder=self.workspace_folder,
            workspace_path_policy=self.workspace_path_policy,
            access=self.access,
        )


@dataclass(frozen=True, slots=True)
class ConversationAttentionFacts:
    """Durable conversation facts used by work-list attention projection."""

    unread_message_to_owner: bool
    last_turn_failed: bool


@dataclass(frozen=True, slots=True)
class StoredConversationEvent:
    """One row of a conversation's record, as it was written."""

    conversation_id: str
    sequence: int
    kind: ConversationEventKind
    payload: ConversationEventPayload
    created_at: int


class ConversationStore:
    """Short-lived append transactions, with one explicit shared-transaction seam."""

    def __init__(
        self,
        db_path: str,
        *,
        integer_now: Callable[[], int] = lambda: int(time.time()),
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MILLISECONDS,
        transaction_participant: ConversationTransactionParticipant | None = None,
        signal_coordinator: SignalPublicationCoordinator | None = None,
        bind_signal_admission: Callable[[sqlite3.Connection, object], AbstractContextManager[None]]
        | None = None,
        runtime_reconciliation_admission: object | None = None,
        transaction_control_guard: Callable[[sqlite3.Connection], AbstractContextManager[None]]
        | None = None,
    ) -> None:
        if signal_coordinator is not None and (
            bind_signal_admission is None
            or transaction_control_guard is None
            or runtime_reconciliation_admission is None
        ):
            raise RuntimeError(
                "Signal publication requires the retained generic transaction adapter"
            )
        self.signal_coordinator = signal_coordinator
        self._bind_signal_admission = bind_signal_admission
        self._transaction_control_guard = transaction_control_guard
        self._runtime_reconciliation_admission = runtime_reconciliation_admission
        self._signal_attempt: ContextVar[tuple[SignalRuntimeMutation | None, str] | None] = (
            ContextVar("signal_attempt", default=None)
        )
        self._retained_signal_recording: ContextVar[bool] = ContextVar(
            "retained_signal_recording", default=False
        )
        self._db_path = db_path
        self._transaction_participant = transaction_participant
        self._integer_now = integer_now
        self._busy_timeout_ms = busy_timeout_ms
        self._write_operations: set[asyncio.Task[object]] = set()
        self._recording_failures: dict[asyncio.Task[Any], tuple[str, Exception, asyncio.Event]] = {}

    def claim_runtime(self, *, existing_ownership: IO[bytes] | None = None) -> IO[bytes]:
        """Exclusively reconcile lost capabilities before this runtime admits work."""
        ownership = (
            existing_ownership
            if existing_ownership is not None
            else acquire_runtime_ownership(self._db_path)
        )
        validate_runtime_ownership(self._db_path, ownership)
        try:
            conn = self._connect()
        except BaseException:
            if existing_ownership is None:
                ownership.close()
            raise
        try:
            with (
                nullcontext()
                if self.signal_coordinator is None
                else self._bind_reconciliation(conn)
            ):
                self._reconcile_runtime(conn)
        except BaseException:
            if existing_ownership is None:
                ownership.close()
            raise
        finally:
            conn.close()
        return ownership

    def _bind_reconciliation(self, conn: sqlite3.Connection) -> AbstractContextManager[None]:
        assert self._bind_signal_admission is not None
        assert self._runtime_reconciliation_admission is not None
        return self._bind_signal_admission(conn, self._runtime_reconciliation_admission)

    def _reconcile_runtime(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        from planner.executor.store import ExecutorStore

        ExecutorStore.retain_legacy_request_ids_in_transaction(conn)
        interruption_data.reconcile(conn, read_manifests(Path(self._db_path)), self._integer_now())
        ids = conn.execute(
            "SELECT DISTINCT conversation_id FROM conversation_lifecycle_facts "
            "WHERE status IN ('intent','running','pending','answering')"
        ).fetchall()
        for row in ids:
            conversation_id = str(row[0])
            from planner.conversation.lifecycle_storage import native_execution_is_unresolved

            if native_execution_is_unresolved(conn, conversation_id):
                continue
            facts = tuple(
                replace(
                    fact,
                    status=(
                        LifecycleStatus.unknown
                        if fact.kind is LifecycleKind.attempt
                        else LifecycleStatus.unavailable
                    ),
                )
                for fact in read_facts(conn, conversation_id)
                if fact.status
                in (
                    LifecycleStatus.intent,
                    LifecycleStatus.running,
                    LifecycleStatus.pending,
                    LifecycleStatus.answering,
                )
            )
            apply_facts(conn, conversation_id, facts)
            interruption_data.settle_delivery(conn, conversation_id, facts, now=self._integer_now())
            participate(
                conn,
                self._transaction_participant,
                LifecycleTransition(conversation_id, read_facts(conn, conversation_id)),
                guard=self._transaction_control_guard,
            )
        commit_without_change_signal(conn)

    async def bind_interruption_delivery(
        self,
        conversation_id: str,
        turn_number: int,
        references: tuple[DeploymentRecoveryReference, ...] | None = None,
        recovery_sender_message_ids: dict[str, str] | None = None,
    ) -> str | None:
        return await self._write(
            self._bind_interruption_delivery_sync,
            conversation_id,
            turn_number,
            references,
            recovery_sender_message_ids,
        )

    def _bind_interruption_delivery_sync(
        self,
        conversation_id: str,
        turn_number: int,
        references: tuple[DeploymentRecoveryReference, ...] | None = None,
        recovery_sender_message_ids: dict[str, str] | None = None,
    ) -> str | None:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            self._apply_signal_mutation(conn, admission)
            notice = interruption_data.bind_delivery(
                conn, conversation_id, turn_number, references, recovery_sender_message_ids
            )
            self._record_signal_result(conn, notice)
            self._commit_rows(conn, ())
            return notice

    async def record_unknown_recovery_deliveries(self) -> None:
        """Use the existing transcript uncertainty row for a lost recovery receipt."""
        from planner.conversation.message_content import text_message_content

        groups = await asyncio.to_thread(self._unknown_recovery_deliveries_sync)
        for conversation_id, identity, references in groups:
            if await self.sender_message_outcome(conversation_id, identity) is not None:
                continue
            await self.append_event(
                conversation_id,
                PromptDeliveryUncertainEventPayload(
                    content=text_message_content(
                        "Inspect the interrupted work and continue the current authorized step."
                    ),
                    sender_label="Panels deployment recovery",
                    mode=PromptDeliveryMode.queue,
                    sender_message_id=identity,
                    recovery_references=references,
                ),
            )

    def _unknown_recovery_deliveries_sync(
        self,
    ) -> tuple[tuple[str, str, tuple[DeploymentRecoveryReference, ...]], ...]:
        conn = self._connect()
        try:
            groups: dict[tuple[str, int, str], list[DeploymentRecoveryReference]] = {}
            for row in conn.execute(
                "SELECT x.conversation_id,x.turn_number,x.interruption_id,t.stage,"
                "x.recovery_sender_message_id "
                "FROM deploy_interruption_deliveries x JOIN deploy_interruptions d "
                "USING(interruption_id) LEFT JOIN tickets t ON d.owner_kind='ticket' "
                "AND t.id=d.owner_id JOIN conversations c ON c.conversation_id="
                "x.conversation_id WHERE x.status='unknown' "
                "AND x.recovery_sender_message_id IS NOT NULL ORDER BY x.interruption_id"
            ):
                groups.setdefault((str(row[0]), int(row[1]), str(row[4])), []).append(
                    DeploymentRecoveryReference(str(row[2]), row[3])
                )
            return tuple((key[0], key[2], tuple(value)) for key, value in groups.items())
        finally:
            conn.close()

    async def select_deployment_recoveries(
        self,
    ) -> dict[str, tuple[DeploymentRecoveryReference, ...]]:
        return await asyncio.to_thread(self._select_deployment_recoveries_sync)

    def _select_deployment_recoveries_sync(
        self,
    ) -> dict[str, tuple[DeploymentRecoveryReference, ...]]:
        conn = self._connect()
        try:
            return interruption_data.select_recoveries(conn)
        finally:
            conn.close()

    async def fail_recovery_claims(self, conversation_id: str, turn_number: int) -> None:
        await self._write(self._fail_recovery_claims_sync, conversation_id, turn_number)

    def _fail_recovery_claims_sync(self, conversation_id: str, turn_number: int) -> None:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            self._apply_signal_mutation(conn, admission)
            claim_revisions = self._recovery_ticket_claim_revisions(conn, conversation_id)
            interruption_data.fail_recovery_claims(
                conn, conversation_id, turn_number, now=self._integer_now()
            )
            self._record_signal_result(conn, None)
            self._commit_rows(
                conn,
                (),
                always_announce=(
                    claim_revisions != self._recovery_ticket_claim_revisions(conn, conversation_id)
                ),
            )

    async def reserve_turn(self, conversation_id: str) -> int:
        return await self._write(self._reserve_turn_sync, conversation_id)

    def _reserve_turn_sync(self, conversation_id: str) -> int:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(turn_number),0) FROM conversation_lifecycle_facts "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            native = conn.execute(
                "SELECT json_extract(turn_json,'$.turn_number') FROM executor_core_turns "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            occurrence = conn.execute(
                "SELECT COALESCE(MAX(turn_number),0) FROM conversation_turn_occurrences "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            turn_number = (
                max(int(row[0]), 0 if native is None else int(native[0]), int(occurrence[0])) + 1
            )
            epoch = conn.execute(
                "SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1"
            ).fetchone()
            assert epoch is not None
            conn.execute(
                "INSERT INTO conversation_turn_occurrences VALUES (?,?,?,?)",
                (conversation_id, turn_number, uuid4().hex, str(epoch[0])),
            )
            apply_facts(
                conn,
                conversation_id,
                (
                    LifecycleFact(
                        LifecycleKind.turn,
                        str(turn_number),
                        turn_number,
                        LifecycleOperation.turn,
                        LifecycleStatus.intent,
                    ),
                    LifecycleFact(
                        LifecycleKind.attempt,
                        f"prompt:{turn_number}",
                        turn_number,
                        LifecycleOperation.prompt,
                        LifecycleStatus.intent,
                    ),
                ),
            )
            participate(
                conn,
                self._transaction_participant,
                LifecycleTransition(conversation_id, read_facts(conn, conversation_id)),
                guard=self._transaction_control_guard,
            )
            self._record_signal_result(conn, turn_number)
            self._commit_rows(conn, ())
            return turn_number

    async def lifecycle_facts(self, conversation_id: str) -> tuple[LifecycleFact, ...]:
        def read() -> tuple[LifecycleFact, ...]:
            conn = self._connect()
            try:
                return read_facts(conn, conversation_id)
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def transition_lifecycle(
        self,
        conversation_id: str,
        facts: tuple[LifecycleFact, ...],
        *,
        executor_turn: tuple[str, str, str] | None = None,
        retired_child: ChildReference | None = None,
    ) -> None:
        await self._write(
            self._transition_lifecycle_sync, conversation_id, facts, executor_turn, retired_child
        )

    def _transition_lifecycle_sync(
        self,
        conversation_id: str,
        facts: tuple[LifecycleFact, ...],
        executor_turn: tuple[str, str, str] | None = None,
        retired_child: ChildReference | None = None,
    ) -> None:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            self._apply_signal_mutation(conn, admission)
            claim_revisions = self._recovery_ticket_claim_revisions(conn, conversation_id)
            apply_facts(conn, conversation_id, facts)
            if executor_turn is not None:
                self.save_executor_turn_in_transaction(conn, conversation_id, executor_turn)
            if retired_child is not None:
                ExecutorStore.close_child_in_transaction(
                    conn, retired_child, self._integer_now() * 1000
                )
            interruption_data.settle_delivery(conn, conversation_id, facts, now=self._integer_now())
            participate(
                conn,
                self._transaction_participant,
                LifecycleTransition(conversation_id, read_facts(conn, conversation_id)),
                guard=self._transaction_control_guard,
            )
            self._record_signal_result(conn, None)
            self._commit_rows(
                conn,
                (),
                always_announce=(
                    claim_revisions != self._recovery_ticket_claim_revisions(conn, conversation_id)
                ),
            )

    def recording_failures(self, conversation_id: str) -> tuple[Exception, ...]:
        """Actual recording errors retained for explicit persistence reconciliation."""
        return tuple(
            error
            for target, error, _ in self._recording_failures.values()
            if target == conversation_id
        )

    def resume_recording(self, conversation_id: str) -> int:
        """Resume failed storage calls, without repeating a backend exchange."""
        pending = [
            resumed
            for target, _, resumed in self._recording_failures.values()
            if target == conversation_id
        ]
        for resumed in pending:
            resumed.set()
        return len(pending)

    async def settle_recording[T](
        self, conversation_id: str, operation: Callable[[], Coroutine[Any, Any, T]]
    ) -> T:
        """A non-lock failure pauses this operation until explicit reconciliation.

        SQLite contention is handled by _write, at the transaction boundary. All other
        errors are exposed here and retained with the exact storage call. The caller
        keeps its accepted turn and ordered event until this operation actually succeeds.
        """
        task = asyncio.current_task()
        assert task is not None
        while True:
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                resumed = asyncio.Event()
                self._recording_failures[task] = (conversation_id, error, resumed)
                logging.getLogger("planner.conversation").error(
                    "conversation %s recording paused: %r",
                    conversation_id,
                    error,
                    exc_info=True,
                )
                try:
                    await resumed.wait()
                finally:
                    self._recording_failures.pop(task, None)

    async def _write[T](self, operation: Callable[..., T], *args: object) -> T:
        """Retain one SQLite operation through caller cancellation and lock contention.

        A synchronous attempt closes its connection (and rolls back any transaction)
        before it raises. Only SQLite's BUSY family proves transient writer contention.
        Each next attempt starts after the previous thread finishes, never beside it.
        """

        def invoke() -> T:
            coordinator = self.signal_coordinator
            candidate = self._signal_attempt.get()
            if coordinator is not None and candidate is not None:
                conn = self._connect()
                try:
                    evidence = coordinator.resolve_operation(conn, candidate[1])
                finally:
                    conn.close()
                if evidence is not None:
                    return cast(T, self._decode_signal_result(evidence[1]))
            return operation(*args)

        async def finish() -> T:
            while True:
                try:
                    return await run_in_capture_thread(invoke)
                except sqlite3.OperationalError as error:
                    code = getattr(error, "sqlite_errorcode", None)
                    if code is None or code & 0xFF != sqlite3.SQLITE_BUSY:
                        raise
                    await asyncio.sleep(CONVERSATION_WRITE_LOCK_RETRY_SECONDS)

        token = None
        if self.signal_coordinator is not None and self._signal_attempt.get() is None:
            token = self._signal_attempt.set((None, uuid4().hex))
        candidate = self._signal_attempt.get()
        try:
            task = asyncio.create_task(finish())
        finally:
            if token is not None:
                self._signal_attempt.reset(token)
        register_capture_task(task)
        self._write_operations.add(task)
        task.add_done_callback(self._write_operations.discard)
        try:
            result = await asyncio.shield(task)
            if self.signal_coordinator is not None and candidate is not None:
                self.signal_coordinator.accept_operation_result(candidate[1])
            return result
        except asyncio.CancelledError:
            if self.signal_coordinator is None:
                raise
            # Retain the caller's conversation lock until its worker resolves.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise

    async def finish_writes(self) -> None:
        """Keep ownership until every cancellation-shielded SQLite write closes."""
        while self._write_operations:
            tasks = tuple(self._write_operations)
            for task in tasks:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # The thread still owns its transaction. Do not release the runtime
                    # lease while a successor could reconcile ahead of that commit.
                    continue
                except Exception:
                    # The operation already reported its error to its owner.
                    continue
            await asyncio.sleep(0)

    async def create_conversation(self, resolved: ResolvedConversationStart) -> ConversationRecord:
        """Write a conversation's row. Raises ``ConversationAlreadyStarted`` for a repeat."""
        return await self._write(self._create_conversation_sync, resolved)

    async def read_conversation(self, conversation_id: str) -> ConversationRecord | None:
        return await asyncio.to_thread(self._read_conversation_sync, conversation_id)

    async def append_event(
        self,
        conversation_id: str,
        payload: ConversationEventPayload,
        *,
        agent_activity: bool = False,
        automatic_compaction_confirmed: bool = False,
        owner_read_through_sequence: int | None = None,
        lifecycle: tuple[LifecycleFact, ...] = (),
        executor_turn: tuple[str, str, str] | None = None,
    ) -> StoredConversationEvent:
        """Write the next row of this conversation's record and return it as written."""
        return await self._write(
            self._append_event_sync,
            conversation_id,
            payload,
            agent_activity,
            automatic_compaction_confirmed,
            owner_read_through_sequence,
            lifecycle,
            executor_turn,
        )

    async def append_initial_shutdown_disposition(
        self,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        lifecycle: tuple[LifecycleFact, ...],
        executor_turn: tuple[str, str, str] | None,
    ) -> tuple[StoredConversationEvent, ...]:
        """Commit the complete initial cancellation audit before settling its caller."""
        return await self._write(
            self._append_initial_shutdown_disposition_sync,
            conversation_id,
            payloads,
            lifecycle,
            executor_turn,
        )

    def _append_initial_shutdown_disposition_sync(
        self,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        lifecycle: tuple[LifecycleFact, ...],
        executor_turn: tuple[str, str, str] | None,
    ) -> tuple[StoredConversationEvent, ...]:
        with self._publication_connection() as (conn, admission):
            try:
                self._begin_validated_attention_write(conn, conversation_id)
                self._apply_signal_mutation(conn, admission)
                if executor_turn is not None:
                    self.save_executor_turn_in_transaction(conn, conversation_id, executor_turn)
                written = self.append_in_transaction(
                    conn, conversation_id, payloads, lifecycle=lifecycle
                )
                self._record_signal_result(conn, written)
                self._commit_rows(conn, payloads)
            except BaseException as error:
                self._invalidate_signal_failure(error)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return written

    async def admit_sender_messages(
        self,
        conversation_id: str,
        messages: tuple[PromptDiscardedEventPayload, ...],
        *,
        lifecycle_if_all_suppressed: tuple[LifecycleFact, ...] = (),
    ) -> tuple[PromptDispatchDecision, ...]:
        """Serialize current notice eligibility with exact discard and lifecycle records."""
        return await self._write(
            self._admit_sender_messages_sync,
            conversation_id,
            messages,
            lifecycle_if_all_suppressed,
        )

    def _admit_sender_messages_sync(
        self,
        conversation_id: str,
        messages: tuple[PromptDiscardedEventPayload, ...],
        lifecycle_if_all_suppressed: tuple[LifecycleFact, ...],
    ) -> tuple[PromptDispatchDecision, ...]:
        with self._publication_connection() as (conn, admission):
            try:
                snapshot = self._begin_validated_attention_write(conn, conversation_id)
                self._apply_signal_mutation(conn, admission)
                claim_revisions = self._recovery_ticket_claim_revisions(conn, conversation_id)
                decisions: list[PromptDispatchDecision] = []
                discarded: list[PromptDiscardedEventPayload] = []
                for message in messages:
                    notice, reason = manager_wakes_data.admit_notice_batch(
                        conn,
                        conversation_id=conversation_id,
                        sender_message_id=message.sender_message_id,
                        now=self._integer_now(),
                    )
                    if reason is None and message.recovery_references:
                        reason = interruption_data.admit_recovery(
                            conn, conversation_id, message.recovery_references, self._integer_now()
                        )
                    if reason is not None:
                        previous = conn.execute(
                            "SELECT payload FROM conversation_events WHERE conversation_id=? "
                            "AND kind='prompt_discarded' "
                            "AND json_extract(payload,'$.sender_message_id')=? "
                            "ORDER BY sequence LIMIT 1",
                            (conversation_id, message.sender_message_id),
                        ).fetchone()
                        if previous is None:
                            discarded.append(replace(message, suppression_reason=reason))
                        else:
                            reason = (
                                json.loads(str(previous["payload"])).get("suppression_reason")
                                or reason
                            )
                    decisions.append(
                        PromptDispatchDecision(message.sender_message_id, reason, notice)
                    )
                lifecycle = (
                    lifecycle_if_all_suppressed
                    if decisions
                    and all(value.suppression_reason is not None for value in decisions)
                    else ()
                )
                written: tuple[StoredConversationEvent, ...] = ()
                if discarded or lifecycle:
                    written = self._insert_rows(conn, conversation_id, tuple(discarded), lifecycle)
                    if discarded:
                        assert snapshot is not None
                        attention = advance_conversation_attention(
                            snapshot,
                            tuple(discarded),
                            owner_read_through_sequence=snapshot.owner_read_through_sequence,
                            occurred_at=self._integer_now(),
                        )
                        capture_conversation_attention(
                            conn, conversation_id, self._integer_now(), attention
                        )
                    self._participate_in_append(conn, conversation_id, written)
                result = tuple(decisions)
                self._record_signal_result(conn, result)
                self._commit_rows(
                    conn,
                    tuple(discarded),
                    always_announce=(
                        claim_revisions
                        != self._recovery_ticket_claim_revisions(conn, conversation_id)
                    ),
                )
                return result
            except BaseException as error:
                self._invalidate_signal_failure(error)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    async def mark_held_sender_messages_leaving_queue(
        self, conversation_id: str, sender_message_ids: tuple[str, ...]
    ) -> int:
        """Durably mark Panels wake messages before a held delivery can reach a backend."""
        wake_ids = tuple(dict.fromkeys(sender_message_ids))
        if not wake_ids:
            return 0
        return await self._write(
            self._mark_held_sender_messages_leaving_queue_sync,
            conversation_id,
            wake_ids,
        )

    async def all_sender_messages_are_manager_notices(
        self, conversation_id: str, sender_message_ids: tuple[str | None, ...]
    ) -> bool:
        """Confirm that every delivered message names a durable internal notice batch."""
        if not sender_message_ids or any(value is None for value in sender_message_ids):
            return False
        unique_ids = tuple(
            dict.fromkeys(value for value in sender_message_ids if value is not None)
        )
        return await asyncio.to_thread(
            self._all_sender_messages_are_manager_notices_sync,
            conversation_id,
            unique_ids,
        )

    def _all_sender_messages_are_manager_notices_sync(
        self, conversation_id: str, sender_message_ids: tuple[str, ...]
    ) -> bool:
        placeholders = ",".join("?" for _ in sender_message_ids)
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT count(*) FROM manager_wake_batches "
                f"WHERE conversation_id=? AND sender_message_id IN ({placeholders})",
                (conversation_id, *sender_message_ids),
            ).fetchone()
        finally:
            conn.close()
        return row is not None and int(row[0]) == len(sender_message_ids)

    def _mark_held_sender_messages_leaving_queue_sync(
        self, conversation_id: str, sender_message_ids: tuple[str, ...]
    ) -> int:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            changed = 0
            for sender_message_id in sender_message_ids:
                changed += conn.execute(
                    "UPDATE manager_wake_batches SET status='dispatching',updated_at=? "
                    "WHERE conversation_id=? AND sender_message_id=? "
                    "AND status IN ('offering','accepted')",
                    (self._integer_now(), conversation_id, sender_message_id),
                ).rowcount
            self._record_signal_result(conn, changed)
            conn.commit()
            return changed

    async def append_message_to_owner(
        self, conversation_id: str, payload: MessageToOwnerEventPayload
    ) -> StoredConversationEvent:
        """Write one owner-bound message without a backend call."""
        return await self.append_event(conversation_id, payload)

    async def append_turn_ending(
        self,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        *,
        agent_activity: bool,
        automatic_compaction_confirmed: bool,
        automatic_compaction_result: AutomaticCompactionResult | None,
        create_failure_notice: bool = False,
        lifecycle: tuple[LifecycleFact, ...] = (),
        executor_turn: tuple[str, str, str] | None = None,
    ) -> tuple[StoredConversationEvent, ...]:
        """Atomically append silence markers followed by their turn ending."""
        return await self._write(
            self._append_turn_ending_sync,
            conversation_id,
            payloads,
            agent_activity,
            automatic_compaction_confirmed,
            automatic_compaction_result,
            create_failure_notice,
            lifecycle,
            executor_turn,
        )

    async def advance_owner_read_through_sequence(
        self, conversation_id: str, through_sequence: int
    ) -> ConversationRecord | None:
        """Move the owner's position forward, bounded by the current record."""
        return await self._write(
            self._advance_owner_read_through_sequence_sync,
            conversation_id,
            through_sequence,
        )

    async def conversations_due_for_automatic_compaction(
        self, *, due_at_or_before: int, activity_after: int
    ) -> tuple[str, ...]:
        """Return unprotected activity inside the bounded automatic compaction window."""
        return await asyncio.to_thread(
            self._conversations_due_for_automatic_compaction_sync,
            due_at_or_before,
            activity_after,
        )

    async def append_delivered_prompt(
        self,
        conversation_id: str,
        *,
        prompt: PromptEventPayload,
        model_change: ModelChangedEventPayload | None,
        extra_prompts: tuple[PromptEventPayload, ...] = (),
        owner_read_through_sequence: int | None = None,
        lifecycle: tuple[LifecycleFact, ...] = (),
        composed_content_delivered: bool = True,
        executor_turn: tuple[str, str, str] | None = None,
    ) -> tuple[StoredConversationEvent, ...]:
        """Write everything one delivery leaves behind, as one thing that either all
        happened or none of it did.

        Several messages that went to the agent as one prompt are several rows here, one
        each, written in the order they were sent. One row could not carry them: a row
        names one sender message id, and that id is how each sender recognises its own
        message when the record hands it back.

        A delivery that carried a change leaves three marks: the change is recorded, the
        conversation is moved onto the new values, and the prompt is recorded. They are one
        transaction because they are one fact. Written separately, a failure part-way
        through leaves a notebook nobody can read straight: a change recorded for a prompt
        that is not there, or a conversation moved onto a model its record never mentions.

        The change is written before the prompt, because it is what the prompt ran under.
        A held owner prompt can supply its earlier admission position. This prevents its
        later delivery from crediting rows that arrived after the owner left.
        Returns the rows in the order they were written.
        """
        from planner.conversation.contracts import PromptDeliveryStarted
        from planner.executor.native_messages import native_message_admission

        with native_message_admission(
            PromptDeliveryStarted(), composed_content_delivered=composed_content_delivered
        ):
            return await self._write(
                self._append_delivered_prompt_sync,
                conversation_id,
                prompt,
                model_change,
                extra_prompts,
                owner_read_through_sequence,
                lifecycle,
                composed_content_delivered,
                executor_turn,
            )

    async def read_events_after(
        self, conversation_id: str, after_sequence: int
    ) -> tuple[StoredConversationEvent, ...]:
        """Every row of this conversation's record past a position, in order."""
        return await asyncio.to_thread(
            self._read_events_after_sync, conversation_id, after_sequence
        )

    async def diagnostics_expired(self, conversation_id: str) -> bool:
        """Whether omission metadata records expired diagnostics in this conversation."""
        return await asyncio.to_thread(self._expiry_sync, conversation_id, None)

    async def sequence_expired(self, conversation_id: str, sequence: int) -> bool:
        """Whether this exact original event position was recorded as expired."""
        return await asyncio.to_thread(self._expiry_sync, conversation_id, sequence)

    def _expiry_sync(self, conversation_id: str, sequence: int | None) -> bool:
        from planner.conversation.retention.read import diagnostics_expired, sequence_expired

        conn = self._connect()
        try:
            return (
                diagnostics_expired(conn, conversation_id)
                if sequence is None
                else sequence_expired(conn, conversation_id, sequence)
            )
        finally:
            conn.close()

    async def read_latest_events(
        self, conversation_id: str, *, before_sequence: int | None, limit: int
    ) -> tuple[StoredConversationEvent, ...]:
        """The last rows of this conversation's record, in order, ending before a position.

        The backward half of ``read_events_after``. A reader who arrives at a long
        conversation wants its end, and then the page before that, which is what
        ``before_sequence`` walks back through.
        """
        return await asyncio.to_thread(
            self._read_latest_events_sync, conversation_id, before_sequence, limit
        )

    async def read_event(
        self, conversation_id: str, sequence: int
    ) -> StoredConversationEvent | None:
        """One row of this conversation's record, by the position it was written at."""
        return await asyncio.to_thread(self._read_event_sync, conversation_id, sequence)

    async def sender_message_outcome(
        self, conversation_id: str, sender_message_id: str
    ) -> StoredConversationEvent | None:
        """Return the one durable outcome for a sender message identity."""
        return await asyncio.to_thread(
            self._sender_message_outcome_sync, conversation_id, sender_message_id
        )

    async def latest_turn_ended_sequences(
        self, conversation_ids: Collection[str]
    ) -> dict[str, int]:
        """Where each of these conversations last had a turn end, by sequence.

        One question about many conversations, because the surface that asks it is
        drawing a list and asks about every row at once.

        A conversation that has never had a turn end is absent from the answer rather
        than present as a zero: there is no such row, and saying so is not the same as
        naming a position. A caller reading the answer per conversation supplies its own
        nothing.
        """
        return await asyncio.to_thread(self._latest_turn_ended_sequences_sync, conversation_ids)

    async def owner_read_through_sequences(
        self, conversation_ids: Collection[str]
    ) -> dict[str, int]:
        """Return the owner's durable position for each existing conversation."""
        return await asyncio.to_thread(self._owner_read_through_sequences_sync, conversation_ids)

    async def attention_facts(
        self, conversation_ids: Collection[str]
    ) -> dict[str, ConversationAttentionFacts]:
        """Return owner-message and last-turn facts for a list of conversations."""
        return await asyncio.to_thread(self._attention_facts_sync, conversation_ids)

    async def conversation_ids_holding_unread_owner_message(self) -> frozenset[str]:
        """Every conversation whose newest message to the owner is past his read mark.

        This asks the whole record rather than a list, because the caller is looking for
        conversations it does not already hold. A caller that has the list wants
        ``attention_facts``, which answers more about each one.
        """
        return await asyncio.to_thread(self._conversation_ids_holding_unread_owner_message_sync)

    async def has_delivered_prompt(self, conversation_id: str) -> bool:
        """Whether any prompt has ever reached this conversation's backend.

        This is what "the first prompt" means for the role text the core composes onto it.
        It is a question about deliveries, not about the record's length: a held message
        that was refused leaves a row behind without any prompt having been delivered.
        """
        return await asyncio.to_thread(self._has_delivered_prompt_sync, conversation_id)

    async def update_vendor_session_cursor(
        self, conversation_id: str, vendor_session_cursor: str
    ) -> None:
        await self._write(
            self._update_vendor_session_cursor_sync, conversation_id, vendor_session_cursor
        )

    async def replace_composer_catalog(
        self, conversation_id: str, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        """Put the whole composer catalog where the old one was.

        A backend reports the catalog it has now, not what moved in it. The prior catalog
        is out of date rather than partly right, so this does not merge entries.
        """
        await self._write(self._replace_composer_catalog_sync, conversation_id, composer_catalog)

    # --- inside the worker thread ---

    def _create_conversation_sync(self, resolved: ResolvedConversationStart) -> ConversationRecord:
        role_materials = resolved.role_materials
        record = ConversationRecord(
            conversation_id=resolved.conversation_id,
            backend_key=resolved.backend_key,
            model=resolved.model,
            reasoning_effort=resolved.reasoning_effort,
            workspace_folder=resolved.workspace_folder,
            workspace_path_policy=resolved.workspace_path_policy,
            role_text=None if role_materials is None else role_materials.role_text,
            identity_environment_variables=(
                () if role_materials is None else role_materials.identity_environment_variables
            ),
            access=resolved.access,
            vendor_session_cursor=None,
            composer_catalog=(),
            latest_sequence=0,
            latest_agent_activity_at=None,
            latest_agent_activity_sequence=0,
            automatically_compacted_through_sequence=0,
            automatic_compaction_attempted_through_sequence=0,
            owner_read_through_sequence=0,
            created_at=self._integer_now(),
        )
        with self._publication_connection() as (conn, admission):
            try:
                if self.signal_coordinator is not None:
                    conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO conversations (conversation_id, backend_key, model, "
                    "reasoning_effort, workspace_folder, workspace_path_policy, role_text, "
                    "identity_environment_variables, access, vendor_session_cursor, "
                    "composer_catalog, latest_sequence, latest_agent_activity_at, "
                    "latest_agent_activity_sequence, automatically_compacted_through_sequence, "
                    "automatic_compaction_attempted_through_sequence, "
                    "owner_read_through_sequence, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.conversation_id,
                        str(record.backend_key),
                        record.model,
                        record.reasoning_effort,
                        str(record.workspace_folder),
                        str(record.workspace_path_policy),
                        record.role_text,
                        _identity_environment_variables_to_json(
                            record.identity_environment_variables
                        ),
                        str(record.access),
                        record.vendor_session_cursor,
                        _composer_catalog_to_json(record.composer_catalog),
                        record.latest_sequence,
                        record.latest_agent_activity_at,
                        record.latest_agent_activity_sequence,
                        record.automatically_compacted_through_sequence,
                        record.automatic_compaction_attempted_through_sequence,
                        record.owner_read_through_sequence,
                        record.created_at,
                    ),
                )
                if self.signal_coordinator is not None:
                    self._record_signal_result(conn, record)
                    conn.commit()
            except sqlite3.IntegrityError as already_there:
                raise ConversationAlreadyStarted(record.conversation_id) from already_there
        return record

    def _read_conversation_sync(self, conversation_id: str) -> ConversationRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id, backend_key, model, reasoning_effort, "
                "workspace_folder, workspace_path_policy, role_text, "
                "identity_environment_variables, access, "
                "vendor_session_cursor, composer_catalog, latest_sequence, "
                "latest_agent_activity_at, latest_agent_activity_sequence, "
                "automatically_compacted_through_sequence, "
                "automatic_compaction_attempted_through_sequence, "
                "owner_read_through_sequence, "
                "created_at "
                "FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _conversation_record(row)

    def _advance_owner_read_through_sequence_sync(
        self, conversation_id: str, through_sequence: int
    ) -> ConversationRecord | None:
        if through_sequence < 0:
            raise ValueError("through_sequence must be non-negative")
        with self._publication_connection() as (conn, admission):
            snapshot = self._begin_validated_attention_write(conn, conversation_id)
            try:
                conn.execute(
                    "UPDATE conversations SET owner_read_through_sequence = "
                    "MAX(owner_read_through_sequence, MIN(?, latest_sequence)) "
                    "WHERE conversation_id = ?",
                    (through_sequence, conversation_id),
                )
                if snapshot is not None:
                    occurred_at = self._integer_now()
                    owner_read_through_sequence = max(
                        snapshot.owner_read_through_sequence,
                        min(through_sequence, snapshot.latest_sequence),
                    )
                    result = advance_conversation_attention(
                        snapshot,
                        (),
                        owner_read_through_sequence=owner_read_through_sequence,
                        occurred_at=occurred_at,
                    )
                    capture_conversation_attention(
                        conn,
                        conversation_id,
                        occurred_at,
                        result,
                    )
                row = conn.execute(
                    "SELECT * FROM conversations WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                record = None if row is None else _conversation_record(row)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            else:
                self._record_signal_result(conn, record)
                self._commit_rows(conn, (), always_announce=True)
        return record

    def _append_event_sync(
        self,
        conversation_id: str,
        payload: ConversationEventPayload,
        agent_activity: bool,
        automatic_compaction_confirmed: bool,
        owner_read_through_sequence: int | None,
        lifecycle: tuple[LifecycleFact, ...] = (),
        executor_turn: tuple[str, str, str] | None = None,
    ) -> StoredConversationEvent:
        with self._publication_connection() as (conn, admission):
            try:
                snapshot = self._begin_validated_attention_write(conn, conversation_id)
                self._apply_signal_mutation(conn, admission)
                written = self._insert_rows(conn, conversation_id, (payload,), lifecycle)
                stored = written[0]
                if agent_activity:
                    conn.execute(
                        "UPDATE conversations SET latest_agent_activity_at = ?, "
                        "latest_agent_activity_sequence = ? WHERE conversation_id = ?",
                        (stored.created_at, stored.sequence, conversation_id),
                    )
                if automatic_compaction_confirmed:
                    conn.execute(
                        "UPDATE conversations SET automatically_compacted_through_sequence = "
                        "latest_agent_activity_sequence, "
                        "automatic_compaction_attempted_through_sequence = "
                        "latest_agent_activity_sequence WHERE conversation_id = ?",
                        (conversation_id,),
                    )
                if owner_read_through_sequence is not None:
                    conn.execute(
                        "UPDATE conversations SET owner_read_through_sequence = "
                        "MAX(owner_read_through_sequence, ?) WHERE conversation_id = ?",
                        (owner_read_through_sequence, conversation_id),
                    )
                assert snapshot is not None
                read_through = snapshot.owner_read_through_sequence
                if owner_read_through_sequence is not None:
                    read_through = max(read_through, owner_read_through_sequence)
                result = advance_conversation_attention(
                    snapshot,
                    (payload,),
                    owner_read_through_sequence=read_through,
                    occurred_at=stored.created_at,
                )
                capture_conversation_attention(conn, conversation_id, stored.created_at, result)
                if executor_turn is not None:
                    self.save_executor_turn_in_transaction(conn, conversation_id, executor_turn)
                self._participate_in_append(conn, conversation_id, written)
                self._record_signal_result(conn, written[0])
                self._commit_rows(conn, (payload,))
            except BaseException as error:
                self._invalidate_signal_failure(error)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return written[0]

    def _conversations_due_for_automatic_compaction_sync(
        self, due_at_or_before: int, activity_after: int
    ) -> tuple[str, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT conversation_id FROM conversations "
                "WHERE latest_agent_activity_at IS NOT NULL "
                "AND latest_agent_activity_at <= ? "
                "AND latest_agent_activity_at > ? "
                "AND latest_agent_activity_sequence > "
                "automatic_compaction_attempted_through_sequence "
                "ORDER BY latest_agent_activity_at, conversation_id",
                (due_at_or_before, activity_after),
            ).fetchall()
        finally:
            conn.close()
        return tuple(str(row["conversation_id"]) for row in rows)

    def _append_delivered_prompt_sync(
        self,
        conversation_id: str,
        prompt: PromptEventPayload,
        model_change: ModelChangedEventPayload | None,
        extra_prompts: tuple[PromptEventPayload, ...] = (),
        owner_read_through_sequence: int | None = None,
        lifecycle: tuple[LifecycleFact, ...] = (),
        composed_content_delivered: bool = True,
        executor_turn: tuple[str, str, str] | None = None,
    ) -> tuple[StoredConversationEvent, ...]:
        prompts: tuple[ConversationEventPayload, ...] = (prompt, *extra_prompts)
        payloads: tuple[ConversationEventPayload, ...] = (
            prompts if model_change is None else (model_change, *prompts)
        )
        with self._publication_connection() as (conn, admission):
            try:
                snapshot = self._begin_validated_attention_write(conn, conversation_id)
                self._apply_signal_mutation(conn, admission)
                written = self._insert_rows(conn, conversation_id, payloads, lifecycle)
                interruption_data.settle_delivery(
                    conn,
                    conversation_id,
                    lifecycle,
                    now=self._integer_now(),
                    composed_content_delivered=composed_content_delivered,
                )
                if model_change is not None:
                    conn.execute(
                        "UPDATE conversations SET model = ?, reasoning_effort = ? "
                        "WHERE conversation_id = ?",
                        (model_change.model, model_change.reasoning_effort, conversation_id),
                    )
                owner_reply_sequences = [
                    event.sequence
                    for event in written
                    if isinstance(event.payload, PromptEventPayload)
                    and event.payload.sender is not None
                    and event.payload.sender.kind is PrincipalKind.owner
                ]
                if owner_reply_sequences:
                    read_through = (
                        max(owner_reply_sequences)
                        if owner_read_through_sequence is None
                        else owner_read_through_sequence
                    )
                    conn.execute(
                        "UPDATE conversations SET owner_read_through_sequence = "
                        "MAX(owner_read_through_sequence, ?) WHERE conversation_id = ?",
                        (read_through, conversation_id),
                    )
                assert snapshot is not None
                resulting_read_through = snapshot.owner_read_through_sequence
                if owner_reply_sequences:
                    resulting_read_through = max(resulting_read_through, read_through)
                result = advance_conversation_attention(
                    snapshot,
                    payloads,
                    owner_read_through_sequence=resulting_read_through,
                    occurred_at=written[-1].created_at,
                )
                capture_conversation_attention(
                    conn, conversation_id, written[-1].created_at, result
                )
                if executor_turn is not None:
                    self.save_executor_turn_in_transaction(conn, conversation_id, executor_turn)
                self._participate_in_append(conn, conversation_id, written)
                self._record_signal_result(conn, written)
                self._commit_rows(conn, payloads)
            except BaseException as error:
                self._invalidate_signal_failure(error)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return written

    def _append_turn_ending_sync(
        self,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        agent_activity: bool,
        automatic_compaction_confirmed: bool,
        automatic_compaction_result: AutomaticCompactionResult | None,
        create_failure_notice: bool,
        lifecycle: tuple[LifecycleFact, ...] = (),
        executor_turn: tuple[str, str, str] | None = None,
    ) -> tuple[StoredConversationEvent, ...]:
        with self._publication_connection() as (conn, admission):
            try:
                snapshot = self._begin_validated_attention_write(conn, conversation_id)
                self._apply_signal_mutation(conn, admission)
                written = self._insert_rows(conn, conversation_id, payloads, lifecycle)
                ended = written[-1]
                if create_failure_notice:
                    ending_payload = ended.payload
                    error_summary = getattr(ending_payload, "error_summary", None)
                    self._create_ticket_failure_notice(
                        conn,
                        conversation_id=conversation_id,
                        event_sequence=ended.sequence,
                        error_summary=str(error_summary),
                        now=ended.created_at,
                    )
                if agent_activity:
                    conn.execute(
                        "UPDATE conversations SET latest_agent_activity_at = ?, "
                        "latest_agent_activity_sequence = ? WHERE conversation_id = ?",
                        (ended.created_at, ended.sequence, conversation_id),
                    )
                if automatic_compaction_confirmed:
                    conn.execute(
                        "UPDATE conversations SET automatically_compacted_through_sequence = "
                        "latest_agent_activity_sequence, "
                        "automatic_compaction_attempted_through_sequence = "
                        "latest_agent_activity_sequence WHERE conversation_id = ?",
                        (conversation_id,),
                    )
                elif automatic_compaction_result is AutomaticCompactionResult.not_compacted:
                    conn.execute(
                        "UPDATE conversations SET "
                        "automatic_compaction_attempted_through_sequence = "
                        "latest_agent_activity_sequence WHERE conversation_id = ?",
                        (conversation_id,),
                    )
                assert snapshot is not None
                result = advance_conversation_attention(
                    snapshot,
                    payloads,
                    owner_read_through_sequence=snapshot.owner_read_through_sequence,
                    occurred_at=ended.created_at,
                )
                capture_conversation_attention(conn, conversation_id, ended.created_at, result)
                if executor_turn is not None:
                    self.save_executor_turn_in_transaction(conn, conversation_id, executor_turn)
                self._participate_in_append(conn, conversation_id, written)
                self._record_signal_result(conn, written)
                self._commit_rows(conn, payloads)
            except BaseException as error:
                self._invalidate_signal_failure(error)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return written

    @staticmethod
    def _create_ticket_failure_notice(
        conn: sqlite3.Connection,
        *,
        conversation_id: str,
        event_sequence: int,
        error_summary: str,
        now: int,
    ) -> None:
        """Snapshot the responsible employee for one currently linked Ticket."""
        row = conn.execute(
            "SELECT id,title,ceiling_holder FROM tickets WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return
        stored_holder = json.loads(str(row["ceiling_holder"]))
        holder = Principal(PrincipalKind(str(stored_holder["kind"])), str(stored_holder["id"]))
        if holder.kind is PrincipalKind.owner:
            return
        manager_wakes_data.create_turn_failure_wake(
            conn,
            target=holder,
            ticket_id=str(row["id"]),
            ticket_title=str(row["title"]),
            conversation_id=conversation_id,
            event_sequence=event_sequence,
            error_summary=error_summary,
            now=now,
        )

    def _participate_in_append(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        written: tuple[StoredConversationEvent, ...],
    ) -> None:
        """Expose the final candidate after all source effects, before the owner commits."""
        participate(
            conn,
            self._transaction_participant,
            LifecycleTransition(
                conversation_id,
                read_facts(conn, conversation_id),
                tuple(event.sequence for event in written),
            ),
            guard=self._transaction_control_guard,
        )

    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        lifecycle: tuple[LifecycleFact, ...] = (),
    ) -> tuple[StoredConversationEvent, ...]:
        """Add rows to the end of a conversation's record. A transaction must be open.

        Where the record has got to is read once and moved once, so a run of rows written
        together is numbered consecutively with no gap for anyone else to write into: the
        transaction is immediate, so a second writer is waiting for this one's lock.
        """
        # Another destination can commit actual reply credit while this source
        # prepared its ending. Canonical credit wins over a stale RAM snapshot.
        if any(isinstance(payload, ExplicitReplyMissingEventPayload) for payload in payloads):
            runtime = conn.execute(
                "SELECT runtime_json FROM executor_core_turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if runtime is not None:
                recipients = json.loads(runtime[0])["explicit_reply_recipients"]
                payloads = tuple(
                    payload
                    for payload in payloads
                    if not isinstance(payload, ExplicitReplyMissingEventPayload)
                    or {"kind": payload.prompt_sender.kind.value, "id": payload.prompt_sender.id}
                    not in recipients
                )
        created_at = self._integer_now()
        row = conn.execute(
            "SELECT latest_sequence FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise ConversationRecordMissing(conversation_id)
        latest_sequence = int(row["latest_sequence"])
        written = []
        for offset, payload in enumerate(payloads, start=1):
            kind = conversation_event_payload_kind(payload)
            conn.execute(
                "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    latest_sequence + offset,
                    str(kind),
                    conversation_event_payload_to_canonical_json(payload),
                    created_at,
                ),
            )
            from planner.executor.native_messages import record_native_message_event_in_transaction

            record_native_message_event_in_transaction(conn, conversation_id, payload)
            from planner.executor.native_messages import consume_native_held_prompt_in_transaction

            consume_native_held_prompt_in_transaction(conn, conversation_id, payload)
            written.append(
                StoredConversationEvent(
                    conversation_id=conversation_id,
                    sequence=latest_sequence + offset,
                    kind=kind,
                    payload=payload,
                    created_at=created_at,
                )
            )
            if isinstance(payload, PromptEventPayload) and payload.sender_message_id is not None:
                settle_worker_step_skill_bindings(conn, payload.sender_message_id, delivered=True)
            elif (
                isinstance(
                    payload,
                    (
                        PromptDeliveryRefusedEventPayload,
                        PromptDeliveryUncertainEventPayload,
                        PromptDiscardedEventPayload,
                    ),
                )
                and payload.sender_message_id is not None
            ):
                settle_worker_step_skill_bindings(conn, payload.sender_message_id, delivered=False)
        conn.execute(
            "UPDATE conversations SET latest_sequence = ? WHERE conversation_id = ?",
            (latest_sequence + len(payloads), conversation_id),
        )
        apply_facts(conn, conversation_id, lifecycle)
        interruption_data.settle_delivery(
            conn,
            conversation_id,
            tuple(
                fact
                for fact in lifecycle
                if fact.status
                in (LifecycleStatus.refused, LifecycleStatus.unknown, LifecycleStatus.suppressed)
            ),
            now=self._integer_now(),
        )
        append_projection(conn, written)
        return tuple(written)

    def _read_events_after_sync(
        self, conversation_id: str, after_sequence: int
    ) -> tuple[StoredConversationEvent, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT conversation_id, sequence, kind, payload, created_at "
                "FROM conversation_events WHERE conversation_id = ? AND sequence > ? "
                "ORDER BY sequence",
                (conversation_id, after_sequence),
            ).fetchall()
        finally:
            conn.close()
        return tuple(_stored_event(row) for row in rows)

    def _read_latest_events_sync(
        self, conversation_id: str, before_sequence: int | None, limit: int
    ) -> tuple[StoredConversationEvent, ...]:
        conn = self._connect()
        parameters: list[object] = [conversation_id]
        before_clause = ""
        if before_sequence is not None:
            before_clause = " AND sequence < ?"
            parameters.append(before_sequence)
        parameters.append(limit)
        try:
            rows = conn.execute(
                "SELECT * FROM (SELECT conversation_id, sequence, kind, payload, created_at "
                "FROM conversation_events WHERE conversation_id = ?"
                + before_clause
                + " ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
                tuple(parameters),
            ).fetchall()
        finally:
            conn.close()
        return tuple(_stored_event(row) for row in rows)

    def _read_event_sync(
        self, conversation_id: str, sequence: int
    ) -> StoredConversationEvent | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id, sequence, kind, payload, created_at "
                "FROM conversation_events WHERE conversation_id = ? AND sequence = ?",
                (conversation_id, sequence),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _stored_event(row)

    def _sender_message_outcome_sync(
        self, conversation_id: str, sender_message_id: str
    ) -> StoredConversationEvent | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id,sequence,kind,payload,created_at FROM conversation_events "
                "WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=? "
                "AND kind IN "
                "('prompt','prompt_delivery_refused','prompt_delivery_uncertain',"
                "'prompt_discarded','message_to_owner') ORDER BY sequence DESC LIMIT 1",
                (conversation_id, sender_message_id),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _stored_event(row)

    def _latest_turn_ended_sequences_sync(
        self, conversation_ids: Collection[str]
    ) -> dict[str, int]:
        unique = tuple(dict.fromkeys(conversation_ids))
        if not unique:
            return {}
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT conversation_id, MAX(sequence) AS latest_turn_ended_sequence "
                "FROM conversation_events WHERE kind = ? AND conversation_id IN "
                f"({','.join('?' * len(unique))}) GROUP BY conversation_id",
                (str(ConversationEventKind.turn_ended), *unique),
            ).fetchall()
        finally:
            conn.close()
        return {str(row["conversation_id"]): int(row["latest_turn_ended_sequence"]) for row in rows}

    def _owner_read_through_sequences_sync(
        self, conversation_ids: Collection[str]
    ) -> dict[str, int]:
        ids = tuple(dict.fromkeys(conversation_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT conversation_id, owner_read_through_sequence FROM conversations "
                f"WHERE conversation_id IN ({placeholders})",
                ids,
            ).fetchall()
        finally:
            conn.close()
        return {
            str(row["conversation_id"]): int(row["owner_read_through_sequence"]) for row in rows
        }

    def _attention_facts_sync(
        self, conversation_ids: Collection[str]
    ) -> dict[str, ConversationAttentionFacts]:
        ids = tuple(dict.fromkeys(conversation_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT c.conversation_id, c.owner_read_through_sequence, "
                "e.latest_owner_message, "
                "(SELECT CASE WHEN te.kind = 'turn_ended' "
                " THEN json_extract(te.payload, '$.ending') ELSE NULL END "
                " FROM conversation_events te "
                " WHERE te.conversation_id = c.conversation_id "
                " AND te.kind IN ('prompt','turn_ended') "
                " AND te.sequence > COALESCE((SELECT a.through_sequence "
                " FROM conversation_error_acknowledgements a "
                " WHERE a.conversation_id = c.conversation_id), 0) "
                " ORDER BY te.sequence DESC LIMIT 1) AS last_turn_ending "
                "FROM conversations c LEFT JOIN ("
                "SELECT conversation_id, MAX(sequence) AS latest_owner_message "
                "FROM conversation_events WHERE kind = 'message_to_owner' "
                "GROUP BY conversation_id"
                ") e ON e.conversation_id = c.conversation_id "
                f"WHERE c.conversation_id IN ({placeholders})",
                ids,
            ).fetchall()
        finally:
            conn.close()
        return {
            str(row["conversation_id"]): ConversationAttentionFacts(
                unread_message_to_owner=(
                    row["latest_owner_message"] is not None
                    and int(row["latest_owner_message"]) > int(row["owner_read_through_sequence"])
                ),
                last_turn_failed=str(row["last_turn_ending"] or "") == "failed",
            )
            for row in rows
        }

    def _conversation_ids_holding_unread_owner_message_sync(self) -> frozenset[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT c.conversation_id FROM conversations c "
                "JOIN conversation_events e ON e.conversation_id = c.conversation_id "
                "WHERE e.kind = 'message_to_owner' "
                "GROUP BY c.conversation_id "
                "HAVING MAX(e.sequence) > c.owner_read_through_sequence"
            ).fetchall()
        finally:
            conn.close()
        return frozenset(str(row["conversation_id"]) for row in rows)

    def _has_delivered_prompt_sync(self, conversation_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM conversation_events WHERE conversation_id = ? AND kind = ? LIMIT 1",
                (conversation_id, str(ConversationEventKind.prompt)),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def _update_vendor_session_cursor_sync(
        self, conversation_id: str, vendor_session_cursor: str
    ) -> None:
        with self._publication_connection() as (conn, admission):
            if self.signal_coordinator is not None:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE conversations SET vendor_session_cursor = ? WHERE conversation_id = ?",
                (vendor_session_cursor, conversation_id),
            )
            if self.signal_coordinator is not None:
                self._record_signal_result(conn, None)
                conn.commit()

    def _replace_composer_catalog_sync(
        self, conversation_id: str, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        serialized_catalog = _composer_catalog_to_json(composer_catalog)
        with self._publication_connection() as (conn, admission):
            if self.signal_coordinator is not None:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE conversations SET composer_catalog = ? "
                "WHERE conversation_id = ? AND composer_catalog IS NOT ?",
                (serialized_catalog, conversation_id, serialized_catalog),
            )

            if self.signal_coordinator is not None:
                self._record_signal_result(conn, None)
                conn.commit()

    def _record_signal_result(self, conn: sqlite3.Connection, result: object) -> None:
        if self.signal_coordinator is not None:
            self.signal_coordinator.record_result(conn, self._encode_signal_result(result))

    @staticmethod
    def _encode_signal_result(result: object) -> object:
        if isinstance(result, PromptDispatchDecision):
            return {"dispatch_decision": asdict(result)}
        if isinstance(result, StoredConversationEvent):
            return {
                "event": {
                    "conversation_id": result.conversation_id,
                    "sequence": result.sequence,
                    "kind": result.kind.value,
                    "payload": conversation_event_payload_to_canonical_json(result.payload),
                    "created_at": result.created_at,
                }
            }
        if isinstance(result, tuple):
            return {"events": [ConversationStore._encode_signal_result(item) for item in result]}
        if isinstance(result, ConversationRecord):
            row = asdict(result)
            row["workspace_folder"] = str(result.workspace_folder)
            row["composer_catalog"] = _composer_catalog_to_json(result.composer_catalog)
            row["identity_environment_variables"] = _identity_environment_variables_to_json(
                result.identity_environment_variables
            )
            return {"record": row}
        return result

    @staticmethod
    def _decode_signal_result(result: object) -> object:
        if not isinstance(result, dict):
            return result
        if "dispatch_decision" in result:
            return PromptDispatchDecision(**result["dispatch_decision"])
        if "event" in result:
            row = result["event"]
            kind = ConversationEventKind(row["kind"])
            return StoredConversationEvent(
                row["conversation_id"],
                row["sequence"],
                kind,
                conversation_event_payload_from_canonical_json(kind, row["payload"]),
                row["created_at"],
            )
        if "events" in result:
            return tuple(ConversationStore._decode_signal_result(item) for item in result["events"])
        if "record" in result:
            return _conversation_record(result["record"])
        raise ValueError("unknown private Signal operation result")

    @contextmanager
    def signal_mutation(self, mutation: SignalRuntimeMutation | None) -> Iterator[None]:
        """Retain one private operation identity across explicit persistence retries."""
        if self.signal_coordinator is None:
            if mutation is not None:
                raise RuntimeError("Signal mutation has no producer")
            yield
            return
        token = self._signal_attempt.set((mutation, uuid4().hex))
        retained = self._retained_signal_recording.set(True)
        try:
            yield
        finally:
            self._signal_attempt.reset(token)
            self._retained_signal_recording.reset(retained)

    @contextmanager
    def _publication_connection(
        self,
    ) -> Iterator[tuple[sqlite3.Connection, SignalAdmission | None]]:
        conn = self._connect()
        closed = False
        try:
            candidate = self._signal_attempt.get()
            admission = None
            binding: AbstractContextManager[None] = nullcontext()
            if candidate is not None:
                coordinator = self.signal_coordinator
                binder = self._bind_signal_admission
                assert coordinator is not None and binder is not None
                mutation, operation_id = candidate
                admission = coordinator.prepare_mutation(
                    conn,
                    mutation,
                    operation_id=operation_id,
                    retain_failure=self._retained_signal_recording.get(),
                )
                binding = binder(conn, admission)
            with binding:
                try:
                    yield conn, admission
                except BaseException as error:
                    self._invalidate_signal_failure(error)
                    raise
                finally:
                    closed = True
                    conn.close()
        finally:
            if not closed:
                conn.close()

    def _invalidate_signal_failure(self, error: BaseException) -> None:
        if self.signal_coordinator is None or not self._retained_signal_recording.get():
            return
        code = getattr(error, "sqlite_errorcode", None)
        if (
            isinstance(error, sqlite3.OperationalError)
            and code is not None
            and code & 0xFF == sqlite3.SQLITE_BUSY
        ):
            return
        candidate = self._signal_attempt.get()
        if candidate is not None:
            self.signal_coordinator.retain_operation_failure(candidate[1])
        self.signal_coordinator.invalidate("publication_failed")

    def _apply_signal_mutation(
        self, conn: sqlite3.Connection, admission: SignalAdmission | None
    ) -> None:
        if admission is not None and admission.mutation is not None:
            assert self.signal_coordinator is not None
            self.signal_coordinator.apply_mutation(conn, admission)

    @staticmethod
    def _recovery_ticket_claim_revisions(
        conn: sqlite3.Connection,
        conversation_id: str,
    ) -> tuple[tuple[str, int], ...]:
        """Quiet conversation writes announce when recovery changes a Ticket claim."""
        return tuple(
            (str(row[0]), int(row[1]))
            for row in conn.execute(
                "SELECT id,worker_step_claim_revision FROM tickets WHERE id IN "
                "(SELECT owner_id FROM deploy_interruptions WHERE source_conversation_id=? "
                "AND owner_kind='ticket') ORDER BY id",
                (conversation_id,),
            )
        )

    def _commit_rows(
        self,
        conn: sqlite3.Connection,
        payloads: tuple[ConversationEventPayload, ...],
        *,
        always_announce: bool = False,
    ) -> None:
        _commit_appended_rows(
            conn,
            payloads,
            always_announce=always_announce,
            strict_outcome=self.signal_coordinator is not None,
        )

    @staticmethod
    def save_executor_turn_in_transaction(
        conn: sqlite3.Connection, conversation_id: str, metadata: tuple[str, str, str]
    ) -> None:
        previous = conn.execute(
            "SELECT child_json,turn_json,runtime_json FROM executor_core_turns "
            "WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if previous is not None:
            committed = json.loads(previous[2])
            archive = committed.get("legacy_request_public_ids")
            if archive:
                current = json.loads(metadata[2])
                current["legacy_request_public_ids"] = archive
                metadata = (metadata[0], metadata[1], json.dumps(current))
        if (
            previous is not None
            and json.loads(previous[0]) == json.loads(metadata[0])
            and json.loads(previous[1]) == json.loads(metadata[1])
        ):
            current = json.loads(metadata[2])
            committed = json.loads(previous[2])
            if committed.get("callbacks_revoked"):
                current["callbacks_revoked"] = True
            recipients = current["explicit_reply_recipients"]
            for recipient in committed["explicit_reply_recipients"]:
                if recipient not in recipients:
                    recipients.append(recipient)
            metadata = (metadata[0], metadata[1], json.dumps(current))
        conn.execute(
            "INSERT INTO "
            "executor_core_turns(conversation_id,child_json,turn_json,runtime_json) "
            "VALUES (?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET "
            "child_json=excluded.child_json,turn_json=excluded.turn_json,runtime_json=excluded.runtime_json",
            (conversation_id, *metadata),
        )
        if json.loads(metadata[2]).get("ended"):
            token = json.loads(metadata[1])
            for row in conn.execute(
                "SELECT ask_key,reference_json FROM executor_asks WHERE conversation_id=?",
                (conversation_id,),
            ).fetchall():
                reference = json.loads(row[1])
                if reference["turn"] == token and reference["child"] == json.loads(metadata[0]):
                    conn.execute(
                        "UPDATE executor_asks SET reachability='callback_gone' "
                        "WHERE ask_key=? AND reachability IN ('reachable','uncertain')",
                        (row[0],),
                    )

    @staticmethod
    def credit_executor_reply_in_transaction(
        conn: sqlite3.Connection, turn: Any, recipient: Principal
    ) -> None:
        row = conn.execute(
            "SELECT turn_json,runtime_json FROM executor_core_turns WHERE conversation_id=?",
            (turn.conversation_id,),
        ).fetchone()
        if row is None:
            return
        token = json.loads(str(row[0]))
        if (
            token.get("turn_number") != turn.turn_number
            or token.get("conversation_id") != turn.conversation_id
        ):
            return
        runtime = json.loads(str(row[1]))
        principal = {"kind": recipient.kind.value, "id": recipient.id}
        recipients = runtime["explicit_reply_recipients"]
        if principal not in recipients:
            recipients.append(principal)
            conn.execute(
                "UPDATE executor_core_turns SET runtime_json=? WHERE conversation_id=?",
                (json.dumps(runtime), turn.conversation_id),
            )

    def _executor_mutation_sync[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._publication_connection() as (conn, admission):
            conn.execute("BEGIN IMMEDIATE")
            self._apply_signal_mutation(conn, admission)
            result = operation(conn)
            self._record_signal_result(conn, result)
            self._commit_rows(conn, ())
            return result

    async def save_move_retirement(self, conversation_id: str, *, uncertain: bool) -> None:
        """Retain remote turn history while retiring its live source association."""

        def write(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT runtime_json FROM executor_core_turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if row is not None:
                runtime = json.loads(row[0])
                runtime["source_retired"] = not uncertain
                runtime["move_retirement_uncertain"] = uncertain
                conn.execute(
                    "UPDATE executor_core_turns SET runtime_json=? WHERE conversation_id=?",
                    (json.dumps(runtime), conversation_id),
                )

        await self._write(self._executor_mutation_sync, write)

    async def revoke_executor_turn_asks(self, child: ChildReference, turn: TurnToken) -> None:
        """Close callbacks before a remote cancellation can await transport."""

        def write(conn: sqlite3.Connection) -> None:
            context = conn.execute(
                "SELECT child_json,turn_json,runtime_json FROM executor_core_turns "
                "WHERE conversation_id=?",
                (child.conversation_id,),
            ).fetchone()
            if (
                context is not None
                and json.loads(context[0]) == asdict(child)
                and json.loads(context[1]) == asdict(turn)
            ):
                runtime = json.loads(context[2])
                runtime["callbacks_revoked"] = True
                conn.execute(
                    "UPDATE executor_core_turns SET runtime_json=? WHERE conversation_id=?",
                    (json.dumps(runtime), child.conversation_id),
                )
            for row in conn.execute(
                "SELECT ask_key,reference_json FROM executor_asks WHERE conversation_id=?",
                (child.conversation_id,),
            ).fetchall():
                reference = json.loads(row[1])
                token = reference["turn"]
                if (
                    reference["child"] == asdict(child)
                    and token["conversation_id"] == turn.conversation_id
                    and token["turn_number"] == turn.turn_number
                ):
                    conn.execute(
                        "UPDATE executor_asks SET reachability='callback_gone' "
                        "WHERE ask_key=? AND reachability IN ('reachable','uncertain')",
                        (row[0],),
                    )

        await self._write(self._executor_mutation_sync, write)

    async def save_executor_turn(
        self, conversation_id: str, metadata: tuple[str, str, str]
    ) -> None:
        await self.transition_lifecycle(conversation_id, (), executor_turn=metadata)

    async def executor_held_targets(self) -> tuple[str, ...]:
        def read() -> tuple[str, ...]:
            conn = self._connect()
            try:
                return tuple(
                    str(row[0])
                    for row in conn.execute(
                        "SELECT DISTINCT held_target_conversation_id FROM executor_native_messages "
                        "WHERE held_state IN ('pending','dispatching') "
                        "ORDER BY held_target_conversation_id"
                    )
                )
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def read_executor_held_prompts(self, conversation_id: str) -> tuple[tuple[str, str], ...]:
        return await asyncio.to_thread(self.read_executor_held_prompts_sync, conversation_id)

    def read_executor_held_prompts_sync(self, conversation_id: str) -> tuple[tuple[str, str], ...]:
        conn = self._connect()
        try:
            return tuple(
                (str(row[0]), str(row[1]))
                for row in conn.execute(
                    "SELECT held_prompt_json,held_state FROM executor_native_messages WHERE "
                    "held_target_conversation_id=? AND held_state IN "
                    "('pending','dispatching') ORDER BY held_admission_order",
                    (conversation_id,),
                )
            )
        finally:
            conn.close()

    async def mark_executor_held_dispatching(
        self, conversation_id: str, sender_message_ids: tuple[str, ...]
    ) -> bool:
        return await self._write(
            self._mark_executor_held_dispatching_sync, conversation_id, sender_message_ids
        )

    def _mark_executor_held_dispatching_sync(
        self, conversation_id: str, sender_message_ids: tuple[str, ...]
    ) -> bool:
        def write(conn: sqlite3.Connection) -> bool:
            changed = False
            for sender_id in sender_message_ids:
                if (
                    conn.execute(
                        "SELECT 1 FROM executor_native_messages WHERE "
                        "held_target_conversation_id=? AND submission_id=? "
                        "AND held_state='pending'",
                        (conversation_id, sender_id),
                    ).fetchone()
                    is None
                ):
                    continue
                cursor = conn.execute(
                    "UPDATE executor_native_messages SET held_state='dispatching' WHERE "
                    "held_target_conversation_id=? AND submission_id=? "
                    "AND held_state='pending'",
                    (conversation_id, sender_id),
                )
                changed = changed or cursor.rowcount > 0
            return changed

        return self._executor_mutation_sync(write)

    def requeue_executor_held_prompt(self, conversation_id: str, held: object) -> None:
        """Persist a known non-delivery before its replacement enters the held line."""
        from planner.executor.codec import encode_value

        encoded = encode_value(held)
        assert isinstance(encoded, dict)
        conn = self._connect()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT * FROM executor_native_messages WHERE "
                    "held_target_conversation_id=? AND submission_id=? "
                    "AND held_state='dispatching'",
                    (conversation_id, encoded.get("sender_message_id")),
                ).fetchall()
                for row in rows:
                    original = json.loads(row["held_prompt_json"])
                    if any(
                        original[key] != encoded[key]
                        for key in (
                            "content",
                            "sender_label",
                            "sender",
                            "recipient",
                        )
                    ):
                        raise ValueError("requeued held identity or bytes changed")
                    order = conn.execute(
                        "SELECT COALESCE(MAX(held_admission_order),0)+1 FROM "
                        "executor_native_messages WHERE held_target_conversation_id=?",
                        (conversation_id,),
                    ).fetchone()[0]
                    conn.execute(
                        "UPDATE executor_native_messages SET held_prompt_json=?,"
                        "held_admission_order=?,held_state='pending' WHERE "
                        "sender_id=? AND submission_id=?",
                        (
                            json.dumps(encoded, sort_keys=True, separators=(",", ":")),
                            order,
                            row["sender_id"],
                            row["submission_id"],
                        ),
                    )
        finally:
            conn.close()

    def read_executor_runtime_snapshots(
        self,
    ) -> tuple[tuple[ConversationRecord, tuple[str, str, str] | None], ...]:
        """Restore durable native knowledge before the initial runtime publication."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT c.*,e.child_json,e.turn_json,e.runtime_json FROM conversations c "
                "LEFT JOIN executor_core_turns e ON e.conversation_id=c.conversation_id "
                "WHERE e.conversation_id IS NOT NULL OR EXISTS (SELECT 1 FROM "
                "executor_native_messages m WHERE m.held_target_conversation_id=c.conversation_id "
                "AND m.held_state IN ('pending','dispatching'))"
            ).fetchall()
            return tuple(
                (
                    _conversation_record(row),
                    None
                    if row["child_json"] is None
                    else (str(row["child_json"]), str(row["turn_json"]), str(row["runtime_json"])),
                )
                for row in rows
            )
        finally:
            conn.close()

    async def read_executor_turn(self, conversation_id: str) -> tuple[str, str, str] | None:
        def read() -> tuple[str, str, str] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT child_json,turn_json,runtime_json FROM executor_core_turns WHERE "
                    "conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                return None if row is None else (str(row[0]), str(row[1]), str(row[2]))
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    def executor_public_request_id(self, ask: Any) -> str | None:
        from planner.executor.store import ExecutorStore

        return ExecutorStore(self._db_path).public_request_id(ask)

    def accept_executor_effect(
        self,
        envelope: Any,
        acceptor: Any,
        writer: Any,
        mutation: SignalRuntimeMutation | None,
    ) -> Any:
        """Bind receipt acceptance to the same reversible runtime publication.

        Prepare before BEGIN. Apply only a successful new effect after its savepoint
        closes. A replay or refused effect leaves runtime fields unchanged.
        """
        coordinator = self.signal_coordinator
        if coordinator is None:
            return acceptor(envelope, writer)
        admission: SignalAdmission | None = None
        applied = False

        @contextmanager
        def transaction_context(conn: sqlite3.Connection) -> Iterator[None]:
            nonlocal admission
            admission = coordinator.prepare_mutation(conn, mutation, operation_id=uuid4().hex)
            assert self._bind_signal_admission is not None
            with self._bind_signal_admission(conn, admission):
                yield

        def write(conn: sqlite3.Connection, accepted: Any) -> Any:
            nonlocal applied
            result = writer(conn, accepted)
            applied = True
            return result

        def before_commit(conn: sqlite3.Connection) -> None:
            if applied:
                assert admission is not None
                coordinator.apply_mutation(conn, admission)

        return acceptor(
            envelope, write, transaction_context=transaction_context, before_commit=before_commit
        )

    @staticmethod
    def lifecycle_facts_in_transaction(
        conn: sqlite3.Connection, conversation_id: str
    ) -> tuple[LifecycleFact, ...]:
        return read_facts(conn, conversation_id)

    def append_in_transaction(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        *,
        agent_activity: bool = False,
        automatic_compaction_confirmed: bool = False,
        automatic_compaction_result: AutomaticCompactionResult | None = None,
        create_failure_notice: bool = False,
        lifecycle: tuple[LifecycleFact, ...] = (),
    ) -> tuple[StoredConversationEvent, ...]:
        """Canonical append under the executor receipt's existing write transaction.

        The caller owns commit and publishes these rows only after it succeeds.
        """
        if not conn.in_transaction:
            raise ValueError("conversation append requires an open transaction")
        if not payloads and not lifecycle:
            return ()
        snapshot = conversation_attention_snapshot(conn, conversation_id, transaction_open=True)
        written = self._insert_rows(conn, conversation_id, payloads, lifecycle)
        if not written:
            self._participate_in_append(conn, conversation_id, written)
            return written
        last = written[-1]
        if agent_activity:
            conn.execute(
                "UPDATE conversations SET latest_agent_activity_at=?, "
                "latest_agent_activity_sequence=? WHERE conversation_id=?",
                (last.created_at, last.sequence, conversation_id),
            )
        if automatic_compaction_confirmed:
            conn.execute(
                "UPDATE conversations SET "
                "automatically_compacted_through_sequence=latest_agent_activity_sequence, "
                "automatic_compaction_attempted_through_sequence=latest_agent_activity_sequence "
                "WHERE conversation_id=?",
                (conversation_id,),
            )
        elif automatic_compaction_result is AutomaticCompactionResult.not_compacted:
            conn.execute(
                "UPDATE conversations SET "
                "automatic_compaction_attempted_through_sequence=latest_agent_activity_sequence "
                "WHERE conversation_id=?",
                (conversation_id,),
            )
        if create_failure_notice:
            self._create_ticket_failure_notice(
                conn,
                conversation_id=conversation_id,
                event_sequence=last.sequence,
                error_summary=str(getattr(last.payload, "error_summary", None)),
                now=last.created_at,
            )
        if snapshot is not None:
            result = advance_conversation_attention(
                snapshot,
                payloads,
                owner_read_through_sequence=snapshot.owner_read_through_sequence,
                occurred_at=last.created_at,
            )
            capture_conversation_attention(conn, conversation_id, last.created_at, result)
        self._participate_in_append(conn, conversation_id, written)
        return written

    def update_remote_identity_in_transaction(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        *,
        vendor_session_cursor: str | None = None,
        composer_catalog: tuple[ComposerCatalogEntry, ...] | None = None,
    ) -> None:
        if vendor_session_cursor is not None:
            conn.execute(
                "UPDATE conversations SET vendor_session_cursor=? WHERE conversation_id=?",
                (vendor_session_cursor, conversation_id),
            )
        if composer_catalog is not None:
            conn.execute(
                "UPDATE conversations SET composer_catalog=? WHERE conversation_id=?",
                (_composer_catalog_to_json(composer_catalog), conversation_id),
            )

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path, self._busy_timeout_ms)

    def _begin_validated_attention_write(
        self, conn: sqlite3.Connection, conversation_id: str
    ) -> ConversationAttentionSnapshot | None:
        """Acquire the write lock only after deriving and then validating attention."""

        if self.signal_coordinator is not None:
            conn.execute("BEGIN IMMEDIATE")
            return conversation_attention_snapshot(conn, conversation_id, transaction_open=True)
        while True:
            snapshot = conversation_attention_snapshot(conn, conversation_id)
            conn.execute("BEGIN IMMEDIATE")
            if snapshot is not None and conversation_attention_snapshot_is_current(conn, snapshot):
                return snapshot
            if snapshot is None:
                exists = conn.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)
                ).fetchone()
                if exists is None:
                    return None
            conn.execute("ROLLBACK")


def _commit_appended_rows(
    conn: sqlite3.Connection,
    payloads: tuple[ConversationEventPayload, ...],
    *,
    always_announce: bool = False,
    strict_outcome: bool = False,
) -> None:
    """Close an append, announcing it only if a screen outside the conversation reads it.

    An agent at work writes far more rows than there are things to go and look at, and
    every announcement sends every open tab back for its whole screen. So the rows only
    the conversation shows commit quietly; the conversation still gets them, because it
    is handed each row as it is written.
    """
    try:
        if always_announce or conversation_event_kinds_need_the_change_signal(
            conversation_event_payload_kind(payload) for payload in payloads
        ):
            conn.execute("COMMIT")
        else:
            commit_without_change_signal(conn)
    except sqlite3.Error:
        # SQLite can roll a transaction back itself when COMMIT fails. A closed
        # transaction alone never proves that a database error followed a commit.
        raise
    except Exception:
        if strict_outcome or conn.in_transaction:
            raise
        # A publisher can fail after SQLite commits. The rows are already durable.
        # Return their result and never enter another append attempt.
        logging.getLogger("planner.conversation").exception(
            "conversation commit succeeded but its change publication failed"
        )


def ensure_started_conversation_record(
    conn: sqlite3.Connection,
    resolved: ResolvedConversationStart,
    *,
    created_at: int,
) -> None:
    """Ensure that a successfully started Ticket conversation has its durable row.

    The production system writes this row before ``start_conversation`` returns. Contract
    implementations can keep their state elsewhere, as the in-memory implementation does.
    Ticket ownership has a foreign key to the durable record, so the Ticket lifecycle
    fills that representation gap before it attempts the guarded ownership write.

    An existing row must describe the same conversation. A mismatch means that two
    systems used one id for different starts, and must fail instead of attaching it.
    """
    role_materials = resolved.role_materials
    identity_environment_variables_json = _identity_environment_variables_to_json(
        () if role_materials is None else role_materials.identity_environment_variables
    )
    immutable_expected = (
        str(resolved.backend_key),
        str(resolved.workspace_folder),
        str(resolved.workspace_path_policy),
        None if role_materials is None else role_materials.role_text,
        identity_environment_variables_json,
        str(resolved.access),
    )
    inserted_values = (
        str(resolved.backend_key),
        resolved.model,
        resolved.reasoning_effort,
        str(resolved.workspace_folder),
        str(resolved.workspace_path_policy),
        None if role_materials is None else role_materials.role_text,
        identity_environment_variables_json,
        str(resolved.access),
    )
    conn.execute(
        "INSERT OR IGNORE INTO conversations (conversation_id, backend_key, model, "
        "reasoning_effort, workspace_folder, workspace_path_policy, role_text, "
        "identity_environment_variables, access, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (resolved.conversation_id, *inserted_values, created_at),
    )
    row = conn.execute(
        "SELECT backend_key, workspace_folder, workspace_path_policy, role_text, "
        "identity_environment_variables, access FROM conversations "
        "WHERE conversation_id = ?",
        (resolved.conversation_id,),
    ).fetchone()
    actual = None if row is None else tuple(row)
    if actual != immutable_expected:
        raise RuntimeError(
            f"conversation {resolved.conversation_id} started with values that differ "
            "from its durable record"
        )


def _conversation_record(row: sqlite3.Row) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=str(row["conversation_id"]),
        backend_key=ConversationBackendKey(str(row["backend_key"])),
        model=None if row["model"] is None else str(row["model"]),
        reasoning_effort=(
            None if row["reasoning_effort"] is None else str(row["reasoning_effort"])
        ),
        workspace_folder=Path(str(row["workspace_folder"])),
        workspace_path_policy=WorkspacePathPolicy(str(row["workspace_path_policy"])),
        role_text=None if row["role_text"] is None else str(row["role_text"]),
        identity_environment_variables=_identity_environment_variables_from_json(
            str(row["identity_environment_variables"])
        ),
        access=ConversationAccess(str(row["access"])),
        vendor_session_cursor=(
            None if row["vendor_session_cursor"] is None else str(row["vendor_session_cursor"])
        ),
        composer_catalog=_composer_catalog_from_json(str(row["composer_catalog"])),
        latest_sequence=int(row["latest_sequence"]),
        latest_agent_activity_at=(
            None
            if row["latest_agent_activity_at"] is None
            else int(row["latest_agent_activity_at"])
        ),
        latest_agent_activity_sequence=int(row["latest_agent_activity_sequence"]),
        automatically_compacted_through_sequence=int(
            row["automatically_compacted_through_sequence"]
        ),
        automatic_compaction_attempted_through_sequence=int(
            row["automatic_compaction_attempted_through_sequence"]
        ),
        owner_read_through_sequence=int(row["owner_read_through_sequence"]),
        created_at=int(row["created_at"]),
    )


def _stored_event(row: sqlite3.Row) -> StoredConversationEvent:
    kind = ConversationEventKind(str(row["kind"]))
    return StoredConversationEvent(
        conversation_id=str(row["conversation_id"]),
        sequence=int(row["sequence"]),
        kind=kind,
        payload=conversation_event_payload_from_canonical_json(kind, str(row["payload"])),
        created_at=int(row["created_at"]),
    )


def _identity_environment_variables_to_json(
    identity_environment_variables: tuple[tuple[str, str], ...],
) -> str:
    return json.dumps(
        [[name, value] for name, value in identity_environment_variables],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _identity_environment_variables_from_json(stored: str) -> tuple[tuple[str, str], ...]:
    pairs = json.loads(stored)
    return tuple((str(name), str(value)) for name, value in pairs)


def _composer_catalog_to_json(composer_catalog: tuple[ComposerCatalogEntry, ...]) -> str:
    return json.dumps(
        [
            {
                "kind": str(entry.kind),
                "display_text": entry.display_text,
                "insertion_text": entry.insertion_text,
                "description": entry.description,
                "argument_hint": entry.argument_hint,
            }
            for entry in composer_catalog
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _composer_catalog_from_json(stored: str) -> tuple[ComposerCatalogEntry, ...]:
    return tuple(
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind(str(entry["kind"])),
            display_text=str(entry["display_text"]),
            insertion_text=str(entry["insertion_text"]),
            description=str(entry["description"]),
            argument_hint=(
                None if entry.get("argument_hint") is None else str(entry["argument_hint"])
            ),
        )
        for entry in json.loads(stored)
    )
