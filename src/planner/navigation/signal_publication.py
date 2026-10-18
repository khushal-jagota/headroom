"""Internal Signal installation and cursor acceptance on the source transaction.

This adapter stores the producer's exact publications and projects their current
attention without inferring policy. Physical preparation does not grant runtime
authority. Only complete installation activates the current public schema.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import cast

from planner.conversation.contracts import (
    ConversationSignalPublicationV1,
    SignalAttachment,
    SignalBatch,
    SignalCursor,
    SignalDeletion,
    SignalReadPermit,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.navigation import data
from planner.navigation.contracts import (
    CompleteAttention,
    CurrentAttentionKnowledge,
    parse_revision,
)
from planner.navigation.delivery_authority import DeliveryAuthority, DeliveryUnavailable
from planner.navigation.schema import SIGNAL_SCHEMA


class SignalInstallationError(ValueError):
    """A publication cannot extend this installed producer boundary."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise SignalInstallationError("Signal navigation acceptance requires a transaction")


def _nonnegative(value: int) -> bool:
    return type(value) is int and value >= 0


def _cursor(cursor: SignalCursor) -> None:
    if not cursor.scope or not cursor.runtime_epoch or not _nonnegative(cursor.sequence):
        raise SignalInstallationError("invalid Signal cursor")


def _publication(publication: ConversationSignalPublicationV1, epoch: str) -> None:
    if (
        not publication.conversation_id
        or publication.runtime_epoch != epoch
        or not _nonnegative(publication.conversation_revision)
    ):
        raise SignalInstallationError("invalid Signal publication fence")
    attention = publication.attention
    if set(attention.signal) != {"working", "message_for_owner"} or any(
        type(value) is not bool
        for value in (
            attention.signal.get("working"),
            attention.signal.get("message_for_owner"),
            attention.awaiting_answer,
            attention.last_turn_failed,
        )
    ):
        raise SignalInstallationError("Signal publication requires complete attention")


def _put(conn: sqlite3.Connection, publication: ConversationSignalPublicationV1) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO navigation_signal_publications VALUES(?,?,?,?)",
        (
            publication.conversation_id,
            publication.runtime_epoch,
            str(publication.conversation_revision),
            _json(asdict(publication.attention)),
        ),
    )


def prepare_schema(conn: sqlite3.Connection) -> None:
    """Prepare private storage without changing public schema or runtime knowledge."""
    _require_transaction(conn)
    for statement in SIGNAL_SCHEMA:
        conn.execute(statement)


def _dirty(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        "INSERT INTO navigation_dirty(kind,id) "
        "SELECT 'navigation_signal_publications',? WHERE NOT EXISTS("
        "SELECT 1 FROM navigation_dirty "
        "WHERE kind='navigation_signal_publications' AND id=?)",
        (conversation_id, conversation_id),
    )


def read_current_attention(
    conn: sqlite3.Connection,
    conversation_id: str,
) -> CurrentAttentionKnowledge:
    """Read exact accepted publication facts. Missing current coverage is an error."""
    try:
        row = conn.execute(
            "SELECT p.runtime_epoch,p.conversation_revision,p.attention,"
            "i.runtime_epoch,i.installation_id,i.scope FROM navigation_signal_publications p "
            "JOIN navigation_signal_installation i ON i.singleton=1 WHERE p.conversation_id=?",
            (conversation_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise SignalInstallationError("Signal attention storage is unavailable") from exc
    if (
        row is None
        or row[2] is None
        or row[0] != row[3]
        or not all(row[index] for index in (0, 4, 5))
    ):
        raise SignalInstallationError("Signal attention lacks current complete coverage")
    try:
        parse_revision(row[1])
        attention = json.loads(row[2])
        if (
            not isinstance(attention, dict)
            or set(attention) != {"signal", "awaiting_answer", "last_turn_failed"}
            or not isinstance(attention["signal"], dict)
            or set(attention["signal"]) != {"working", "message_for_owner"}
            or any(
                type(value) is not bool
                for value in (
                    attention["signal"]["working"],
                    attention["signal"]["message_for_owner"],
                    attention["awaiting_answer"],
                    attention["last_turn_failed"],
                )
            )
        ):
            raise ValueError("incomplete attention")
    except (ValueError, TypeError, KeyError) as exc:
        raise SignalInstallationError("Signal attention requires exact complete facts") from exc
    current = cast(CompleteAttention, attention)
    return {
        "current": current,
        "durable": {
            "message_for_owner": current["signal"]["message_for_owner"],
            "last_turn_failed": current["last_turn_failed"],
        },
    }


def install(conn: sqlite3.Connection, snapshot: SignalSnapshot, /) -> None:
    """Accept a complete producer snapshot without commit or public producer reads."""
    _require_transaction(conn)
    _cursor(snapshot.cursor)
    if not snapshot.installation_id:
        raise SignalInstallationError("Signal installation requires an identity")
    identities: set[str] = set()
    for publication in snapshot.values:
        _publication(publication, snapshot.cursor.runtime_epoch)
        if publication.conversation_id in identities:
            raise SignalInstallationError("duplicate Signal snapshot conversation")
        identities.add(publication.conversation_id)
    actual = {str(row[0]) for row in conn.execute("SELECT conversation_id FROM conversations")}
    if actual != identities:
        raise SignalInstallationError("Signal snapshot is not the complete conversation set")
    prepare_schema(conn)
    conn.execute("DELETE FROM navigation_signal_publications")
    for publication in snapshot.values:
        _put(conn, publication)
    conn.execute(
        "INSERT OR REPLACE INTO navigation_signal_installation VALUES(1,?,?,?,?,NULL)",
        (
            snapshot.installation_id,
            snapshot.cursor.scope,
            snapshot.cursor.runtime_epoch,
            str(snapshot.cursor.sequence),
        ),
    )
    version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
    if version not in (3, 4):
        raise SignalInstallationError("Signal installation requires navigation schema 3 or 4")
    conn.execute("UPDATE navigation_meta SET schema_version=4 WHERE singleton=1")
    data.rebuild(conn)


def participate(conn: sqlite3.Connection, batch: SignalBatch, /) -> None:
    """Store one producer boundary before the retained navigation finalizer."""
    _require_transaction(conn)
    _cursor(batch.base_cursor)
    _cursor(batch.cursor)
    if (
        batch.cursor.scope != batch.base_cursor.scope
        or batch.cursor.runtime_epoch != batch.base_cursor.runtime_epoch
        or batch.cursor.sequence != batch.base_cursor.sequence + 1
        or not batch.batch_id
        or not batch.fingerprint
        or any(not identity for identity in batch.operation_ids)
        or len(set(batch.operation_ids)) != len(batch.operation_ids)
    ):
        raise SignalInstallationError("invalid Signal batch boundary")
    row = conn.execute(
        "SELECT scope,runtime_epoch,sequence,last_batch FROM navigation_signal_installation "
        "WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise SignalInstallationError("Signal publication is not installed")
    body = _json(asdict(batch))
    current = (str(row[0]), str(row[1]), int(row[2]))
    if current == (batch.cursor.scope, batch.cursor.runtime_epoch, batch.cursor.sequence):
        if row[3] == body:
            return
        raise SignalInstallationError("conflicting Signal batch at installed cursor")
    if current != (
        batch.base_cursor.scope,
        batch.base_cursor.runtime_epoch,
        batch.base_cursor.sequence,
    ):
        raise SignalInstallationError("Signal cursor gap or obsolete batch")
    identities: set[str] = set()
    for publication in batch.replacements:
        _publication(publication, batch.cursor.runtime_epoch)
        if publication.conversation_id in identities:
            raise SignalInstallationError("duplicate Signal batch conversation")
        identities.add(publication.conversation_id)
    for deletion in batch.deletions:
        if (
            not deletion.conversation_id
            or deletion.runtime_epoch != batch.cursor.runtime_epoch
            or not _nonnegative(deletion.conversation_revision)
            or deletion.conversation_id in identities
        ):
            raise SignalInstallationError("invalid or duplicate Signal deletion")
        identities.add(deletion.conversation_id)
    candidates: tuple[ConversationSignalPublicationV1 | SignalDeletion, ...] = (
        *batch.replacements,
        *batch.deletions,
    )
    for candidate in candidates:
        previous = conn.execute(
            "SELECT runtime_epoch,conversation_revision FROM navigation_signal_publications "
            "WHERE conversation_id=?",
            (candidate.conversation_id,),
        ).fetchone()
        if previous is not None and (
            previous[0] != candidate.runtime_epoch
            or int(previous[1]) >= candidate.conversation_revision
        ):
            raise SignalInstallationError("obsolete Signal conversation fence")
    for publication in batch.replacements:
        _put(conn, publication)
        _dirty(conn, publication.conversation_id)
    for deletion in batch.deletions:
        conn.execute(
            "INSERT OR REPLACE INTO navigation_signal_publications VALUES(?,?,?,NULL)",
            (deletion.conversation_id, deletion.runtime_epoch, str(deletion.conversation_revision)),
        )
        _dirty(conn, deletion.conversation_id)
    for association in batch.associations:
        for conversation_id in (
            association.before_conversation_id,
            association.after_conversation_id,
        ):
            if conversation_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO navigation_dirty VALUES('conversations',?)",
                    (conversation_id,),
                )
    conn.execute(
        "UPDATE navigation_signal_installation SET sequence=?,last_batch=? WHERE singleton=1",
        (str(batch.cursor.sequence), body),
    )


def validate_installation(conn: sqlite3.Connection, permit: SignalReadPermit, /) -> None:
    """Check only SQL installation identity. Never acquire a producer gate here."""
    try:
        row = conn.execute(
            "SELECT installation_id,scope,runtime_epoch FROM navigation_signal_installation "
            "WHERE singleton=1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise DeliveryUnavailable("Signal navigation installation is unavailable") from exc
    if row is None or tuple(row) != (permit.installation_id, permit.scope, permit.runtime_epoch):
        raise DeliveryUnavailable("Signal navigation installation changed")


def delivery_authority(attachment: SignalAttachment) -> DeliveryAuthority:
    """Bind the exact domain permit API without recreating domain attachment."""

    def capture_permit() -> object:
        try:
            return attachment.capture_permit()
        except SignalUnavailable as exc:
            raise DeliveryUnavailable(str(exc)) from exc

    def require_permit(value: object) -> SignalReadPermit:
        if not isinstance(value, SignalReadPermit):
            raise DeliveryUnavailable("Signal delivery requires its exact permit type")
        return value

    def validate(value: object) -> None:
        try:
            attachment.validate(require_permit(value))
        except SignalUnavailable as exc:
            raise DeliveryUnavailable(str(exc)) from exc

    def validate_snapshot(conn: sqlite3.Connection, value: object) -> None:
        validate_installation(conn, require_permit(value))

    return DeliveryAuthority(capture_permit, validate, validate_snapshot)
