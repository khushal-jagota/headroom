"""SQLite ownership for conversations and the rows they record.

Ordinary calls open their own connection inside a worker thread and close it before
returning. The specialized atomic prompt batch instead receives an already-open
application transaction so its transcript rows and the caller's mutation share one
commit.

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
import sqlite3
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventKind,
    ConversationEventPayload,
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
from planner.core.contracts import PrincipalKind
from planner.core.db import commit_without_change_signal, connect
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
    ) -> None:
        self._db_path = db_path
        self._integer_now = integer_now
        self._busy_timeout_ms = busy_timeout_ms

    async def create_conversation(self, resolved: ResolvedConversationStart) -> ConversationRecord:
        """Write a conversation's row. Raises ``ConversationAlreadyStarted`` for a repeat."""
        return await asyncio.to_thread(self._create_conversation_sync, resolved)

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
    ) -> StoredConversationEvent:
        """Write the next row of this conversation's record and return it as written."""
        return await asyncio.to_thread(
            self._append_event_sync,
            conversation_id,
            payload,
            agent_activity,
            automatic_compaction_confirmed,
            owner_read_through_sequence,
        )

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
    ) -> tuple[StoredConversationEvent, ...]:
        """Atomically append silence markers followed by their turn ending."""
        return await asyncio.to_thread(
            self._append_turn_ending_sync,
            conversation_id,
            payloads,
            agent_activity,
            automatic_compaction_confirmed,
            automatic_compaction_result,
        )

    async def advance_owner_read_through_sequence(
        self, conversation_id: str, through_sequence: int
    ) -> ConversationRecord | None:
        """Move the owner's position forward, bounded by the current record."""
        return await asyncio.to_thread(
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
        transaction_connection: sqlite3.Connection | None = None,
        commit_mutation: Callable[[], None] | None = None,
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
        if transaction_connection is not None:
            return self._append_delivered_prompt_sync(
                conversation_id,
                prompt,
                model_change,
                extra_prompts,
                owner_read_through_sequence,
                transaction_connection=transaction_connection,
                commit_mutation=commit_mutation,
            )
        return await asyncio.to_thread(
            self._append_delivered_prompt_sync,
            conversation_id,
            prompt,
            model_change,
            extra_prompts,
            owner_read_through_sequence,
            None,
            commit_mutation,
        )

    async def read_events_after(
        self, conversation_id: str, after_sequence: int
    ) -> tuple[StoredConversationEvent, ...]:
        """Every row of this conversation's record past a position, in order."""
        return await asyncio.to_thread(
            self._read_events_after_sync, conversation_id, after_sequence
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
        await asyncio.to_thread(
            self._update_vendor_session_cursor_sync, conversation_id, vendor_session_cursor
        )

    async def replace_composer_catalog(
        self, conversation_id: str, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        """Put the whole composer catalog where the old one was.

        A backend reports the catalog it has now, not what moved in it. The prior catalog
        is out of date rather than partly right, so this does not merge entries.
        """
        await asyncio.to_thread(
            self._replace_composer_catalog_sync, conversation_id, composer_catalog
        )

    # --- inside the worker thread ---

    def _create_conversation_sync(self, resolved: ResolvedConversationStart) -> ConversationRecord:
        role_materials = resolved.role_materials
        record = ConversationRecord(
            conversation_id=resolved.conversation_id,
            backend_key=resolved.backend_key,
            model=resolved.model,
            reasoning_effort=resolved.reasoning_effort,
            workspace_folder=resolved.workspace_folder,
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
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations (conversation_id, backend_key, model, "
                "reasoning_effort, workspace_folder, role_text, "
                "identity_environment_variables, access, vendor_session_cursor, "
                "composer_catalog, latest_sequence, latest_agent_activity_at, "
                "latest_agent_activity_sequence, automatically_compacted_through_sequence, "
                "automatic_compaction_attempted_through_sequence, "
                "owner_read_through_sequence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.conversation_id,
                    str(record.backend_key),
                    record.model,
                    record.reasoning_effort,
                    str(record.workspace_folder),
                    record.role_text,
                    _identity_environment_variables_to_json(record.identity_environment_variables),
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
        except sqlite3.IntegrityError as already_there:
            raise ConversationAlreadyStarted(record.conversation_id) from already_there
        finally:
            conn.close()
        return record

    def _read_conversation_sync(self, conversation_id: str) -> ConversationRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id, backend_key, model, reasoning_effort, "
                "workspace_folder, role_text, identity_environment_variables, access, "
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
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE conversations SET owner_read_through_sequence = "
                    "MAX(owner_read_through_sequence, MIN(?, latest_sequence)) "
                    "WHERE conversation_id = ?",
                    (through_sequence, conversation_id),
                )
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _conversation_record(row)

    def _append_event_sync(
        self,
        conversation_id: str,
        payload: ConversationEventPayload,
        agent_activity: bool,
        automatic_compaction_confirmed: bool,
        owner_read_through_sequence: int | None,
    ) -> StoredConversationEvent:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            written = self._insert_rows(conn, conversation_id, (payload,))
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
            _commit_appended_rows(conn, (payload,))
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
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
        transaction_connection: sqlite3.Connection | None = None,
        commit_mutation: Callable[[], None] | None = None,
    ) -> tuple[StoredConversationEvent, ...]:
        prompts: tuple[ConversationEventPayload, ...] = (prompt, *extra_prompts)
        payloads: tuple[ConversationEventPayload, ...] = (
            prompts if model_change is None else (model_change, *prompts)
        )
        conn = transaction_connection or self._connect()
        owns_connection = transaction_connection is None
        try:
            if owns_connection:
                conn.execute("BEGIN IMMEDIATE")
            elif not conn.in_transaction:
                raise ValueError("the shared conversation transaction is not open")
            written = self._insert_rows(conn, conversation_id, payloads)
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
            if commit_mutation is not None:
                commit_mutation()
            if transaction_connection is None:
                _commit_appended_rows(conn, payloads)
            else:
                # The shared unit can contain application-visible mutations, so it must
                # use the ordinary signalling commit rather than the transcript-only
                # quiet commit.
                conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns_connection:
                conn.close()
        return written

    def _append_turn_ending_sync(
        self,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
        agent_activity: bool,
        automatic_compaction_confirmed: bool,
        automatic_compaction_result: AutomaticCompactionResult | None,
    ) -> tuple[StoredConversationEvent, ...]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            written = self._insert_rows(conn, conversation_id, payloads)
            ended = written[-1]
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
            _commit_appended_rows(conn, payloads)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return written

    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        payloads: tuple[ConversationEventPayload, ...],
    ) -> tuple[StoredConversationEvent, ...]:
        """Add rows to the end of a conversation's record. A transaction must be open.

        Where the record has got to is read once and moved once, so a run of rows written
        together is numbered consecutively with no gap for anyone else to write into: the
        transaction is immediate, so a second writer is waiting for this one's lock.
        """
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
                "'prompt_discarded','message_to_owner') LIMIT 1",
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
                "MAX(CASE WHEN e.kind = 'message_to_owner' THEN e.sequence END) "
                "AS latest_owner_message, "
                "(SELECT json_extract(te.payload, '$.ending') "
                " FROM conversation_events te "
                " WHERE te.conversation_id = c.conversation_id AND te.kind = 'turn_ended' "
                " ORDER BY te.sequence DESC LIMIT 1) AS last_turn_ending "
                "FROM conversations c LEFT JOIN conversation_events e "
                "ON e.conversation_id = c.conversation_id "
                f"WHERE c.conversation_id IN ({placeholders}) GROUP BY c.conversation_id",
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
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE conversations SET vendor_session_cursor = ? WHERE conversation_id = ?",
                (vendor_session_cursor, conversation_id),
            )
        finally:
            conn.close()

    def _replace_composer_catalog_sync(
        self, conversation_id: str, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        serialized_catalog = _composer_catalog_to_json(composer_catalog)
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE conversations SET composer_catalog = ? "
                "WHERE conversation_id = ? AND composer_catalog IS NOT ?",
                (serialized_catalog, conversation_id, serialized_catalog),
            )
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path, self._busy_timeout_ms)


def _commit_appended_rows(
    conn: sqlite3.Connection, payloads: tuple[ConversationEventPayload, ...]
) -> None:
    """Close an append, announcing it only if a screen outside the conversation reads it.

    An agent at work writes far more rows than there are things to go and look at, and
    every announcement sends every open tab back for its whole screen. So the rows only
    the conversation shows commit quietly; the conversation still gets them, because it
    is handed each row as it is written.
    """
    if conversation_event_kinds_need_the_change_signal(
        conversation_event_payload_kind(payload) for payload in payloads
    ):
        conn.execute("COMMIT")
        return
    commit_without_change_signal(conn)


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
        None if role_materials is None else role_materials.role_text,
        identity_environment_variables_json,
        str(resolved.access),
    )
    inserted_values = (
        str(resolved.backend_key),
        resolved.model,
        resolved.reasoning_effort,
        str(resolved.workspace_folder),
        None if role_materials is None else role_materials.role_text,
        identity_environment_variables_json,
        str(resolved.access),
    )
    conn.execute(
        "INSERT OR IGNORE INTO conversations (conversation_id, backend_key, model, "
        "reasoning_effort, workspace_folder, role_text, "
        "identity_environment_variables, access, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (resolved.conversation_id, *inserted_values, created_at),
    )
    row = conn.execute(
        "SELECT backend_key, workspace_folder, role_text, "
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
