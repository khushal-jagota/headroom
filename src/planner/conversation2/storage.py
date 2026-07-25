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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from planner.conversation2.contracts import (
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.conversation2.events import (
    ConversationEventKind,
    ConversationEventPayload,
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

    async def read_events_after(
        self, conversation_id: str, after_sequence: int
    ) -> tuple[StoredConversationEvent, ...]:
        """Every row of this conversation's record past a position, in order."""
        return await asyncio.to_thread(
            self._read_events_after_sync, conversation_id, after_sequence
        )

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

    async def update_current_model_and_reasoning_effort(
        self, conversation_id: str, model: str | None, reasoning_effort: str | None
    ) -> None:
        await asyncio.to_thread(
            self._update_current_model_and_reasoning_effort_sync,
            conversation_id,
            model,
            reasoning_effort,
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
            latest_sequence=0,
            created_at=self._integer_now(),
        )
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations (conversation_id, backend_key, model, "
                "reasoning_effort, workspace_folder, role_text, "
                "identity_environment_variables, access, vendor_session_cursor, "
                "latest_sequence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                "vendor_session_cursor, latest_sequence, created_at FROM conversations "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _conversation_record(row)

    def _append_event_sync(
        self, conversation_id: str, payload: ConversationEventPayload
    ) -> StoredConversationEvent:
        kind = conversation_event_payload_kind(payload)
        payload_json = conversation_event_payload_to_canonical_json(payload)
        created_at = self._integer_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT latest_sequence FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ConversationRecordMissing(conversation_id)
            sequence = int(row["latest_sequence"]) + 1
            conn.execute(
                "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, sequence, str(kind), payload_json, created_at),
            )
            conn.execute(
                "UPDATE conversations SET latest_sequence = ? WHERE conversation_id = ?",
                (sequence, conversation_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return StoredConversationEvent(
            conversation_id=conversation_id,
            sequence=sequence,
            kind=kind,
            payload=payload,
            created_at=created_at,
        )

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

    def _update_current_model_and_reasoning_effort_sync(
        self, conversation_id: str, model: str | None, reasoning_effort: str | None
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE conversations SET model = ?, reasoning_effort = ? "
                "WHERE conversation_id = ?",
                (model, reasoning_effort, conversation_id),
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
