"""SQLite ownership for conversations and the rows they record.

Every call here opens its own connection inside a worker thread and closes it before
returning, so nothing is shared across threads and no connection outlives the one
operation it was opened for.

Appending a row is one immediate transaction: take the write lock, read where the
conversation's record has got to, insert the next row, move the conversation's marker
forward, commit. Two writers cannot both decide they own the same sequence number,
because the second one waits for the first one's lock.

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

from planner.conversation.contracts import (
    AgentCommand,
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationEventKind,
    ConversationEventPayload,
    ModelChangedEventPayload,
    PromptEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.core.db import connect

DEFAULT_BUSY_TIMEOUT_MILLISECONDS = 5000


class ConversationRecordMissing(RuntimeError):
    """A conversation's own row is not there, so its record has nowhere to go.

    Rows are numbered per conversation and the conversation's row is what holds the
    number, so appending without it is not something to paper over.
    """


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """A conversation as it is stored: what it was started with, and where it is now.

    ``model`` and ``reasoning_effort`` are the current values, which a delivery carrying a
    change moves. ``vendor_session_cursor`` is the backend's own session identity — it is
    internal, it is rebindable, and it is what a lazy resume starts from.
    ``available_commands`` is what the backend last said a person may type at this agent,
    kept here so it is still there when no child is.
    """

    conversation_id: str
    backend_key: ConversationBackendKey
    model: str | None
    reasoning_effort: str | None
    workspace_folder: Path
    role_text: str | None
    identity_environment_variables: tuple[tuple[str, str], ...]
    access: ConversationAccess
    vendor_session_cursor: str | None
    available_commands: tuple[AgentCommand, ...]
    latest_sequence: int
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
class StoredConversationEvent:
    """One row of a conversation's record, as it was written."""

    conversation_id: str
    sequence: int
    kind: ConversationEventKind
    payload: ConversationEventPayload
    created_at: int


class ConversationStore:
    """One short-lived connection per call, and one immediate transaction per append."""

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
        self, conversation_id: str, payload: ConversationEventPayload
    ) -> StoredConversationEvent:
        """Write the next row of this conversation's record and return it as written."""
        return await asyncio.to_thread(self._append_event_sync, conversation_id, payload)

    async def append_delivered_prompt(
        self,
        conversation_id: str,
        *,
        prompt: PromptEventPayload,
        model_change: ModelChangedEventPayload | None,
    ) -> tuple[StoredConversationEvent, ...]:
        """Write everything one delivery leaves behind, as one thing that either all
        happened or none of it did.

        A delivery that carried a change leaves three marks: the change is recorded, the
        conversation is moved onto the new values, and the prompt is recorded. They are one
        transaction because they are one fact. Written separately, a failure part-way
        through leaves a notebook nobody can read straight: a change recorded for a prompt
        that is not there, or a conversation moved onto a model its record never mentions.

        The change is written before the prompt, because it is what the prompt ran under.
        Returns the rows in the order they were written.
        """
        return await asyncio.to_thread(
            self._append_delivered_prompt_sync, conversation_id, prompt, model_change
        )

    async def read_events_after(
        self, conversation_id: str, after_sequence: int
    ) -> tuple[StoredConversationEvent, ...]:
        """Every row of this conversation's record past a position, in order."""
        return await asyncio.to_thread(
            self._read_events_after_sync, conversation_id, after_sequence
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

    async def replace_available_commands(
        self, conversation_id: str, available_commands: tuple[AgentCommand, ...]
    ) -> None:
        """Put the whole command list where the old one was.

        A backend reports the menu it has now, not what moved in it, so what was there
        before is out of date rather than partly right. Merging would keep a command the
        agent has stopped answering to.
        """
        await asyncio.to_thread(
            self._replace_available_commands_sync, conversation_id, available_commands
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
            available_commands=(),
            latest_sequence=0,
            created_at=self._integer_now(),
        )
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations (conversation_id, backend_key, model, "
                "reasoning_effort, workspace_folder, role_text, "
                "identity_environment_variables, access, vendor_session_cursor, "
                "available_commands, latest_sequence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    _available_commands_to_json(record.available_commands),
                    record.latest_sequence,
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
                "vendor_session_cursor, available_commands, latest_sequence, created_at "
                "FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _conversation_record(row)

    def _append_event_sync(
        self, conversation_id: str, payload: ConversationEventPayload
    ) -> StoredConversationEvent:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            written = self._insert_rows(conn, conversation_id, (payload,))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return written[0]

    def _append_delivered_prompt_sync(
        self,
        conversation_id: str,
        prompt: PromptEventPayload,
        model_change: ModelChangedEventPayload | None,
    ) -> tuple[StoredConversationEvent, ...]:
        payloads: tuple[ConversationEventPayload, ...] = (
            (prompt,) if model_change is None else (model_change, prompt)
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            written = self._insert_rows(conn, conversation_id, payloads)
            if model_change is not None:
                conn.execute(
                    "UPDATE conversations SET model = ?, reasoning_effort = ? "
                    "WHERE conversation_id = ?",
                    (model_change.model, model_change.reasoning_effort, conversation_id),
                )
            conn.execute("COMMIT")
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
        return {
            str(row["conversation_id"]): int(row["latest_turn_ended_sequence"]) for row in rows
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

    def _replace_available_commands_sync(
        self, conversation_id: str, available_commands: tuple[AgentCommand, ...]
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE conversations SET available_commands = ? WHERE conversation_id = ?",
                (_available_commands_to_json(available_commands), conversation_id),
            )
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path, self._busy_timeout_ms)


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
        available_commands=_available_commands_from_json(str(row["available_commands"])),
        latest_sequence=int(row["latest_sequence"]),
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


def _available_commands_to_json(available_commands: tuple[AgentCommand, ...]) -> str:
    return json.dumps(
        [
            {
                "name": command.name,
                "description": command.description,
                "argument_hint": command.argument_hint,
            }
            for command in available_commands
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _available_commands_from_json(stored: str) -> tuple[AgentCommand, ...]:
    return tuple(
        AgentCommand(
            name=str(command["name"]),
            description=str(command["description"]),
            argument_hint=(
                None if command.get("argument_hint") is None else str(command["argument_hint"])
            ),
        )
        for command in json.loads(stored)
    )
