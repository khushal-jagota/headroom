"""Exact domain fixtures prove internal adapters, not producer or assembled truth.

The existing synthetic navigation transaction host supplies all storage. No
connected server, runtime owner, agent home, provider, or live database is used.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from tests.integration.test_navigation_transactions import add_ticket, database, values

from planner.conversation.contracts import (
    ConversationAttention,
    ConversationSignalPublicationV1,
    SignalAssociationEffect,
    SignalBatch,
    SignalCursor,
    SignalDeletion,
    SignalReadPermit,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.navigation import snapshot as navigation_snapshot
from planner.navigation.delivery_authority import DeliveryUnavailable
from planner.navigation.signal_publication import (
    SignalInstallationError,
    delivery_authority,
    install,
    participate,
    validate_installation,
)


def publication(identity: str, revision: int = 0) -> ConversationSignalPublicationV1:
    return ConversationSignalPublicationV1(
        identity,
        "runtime",
        revision,
        ConversationAttention(
            signal={"working": True, "message_for_owner": False},
            awaiting_answer=False,
            last_turn_failed=True,
        ),
    )


def complete() -> SignalSnapshot:
    return SignalSnapshot(
        "installation",
        SignalCursor("producer-scope", "runtime", 4),
        (publication("current"), publication("historical", 12)),
    )


def batch(sequence: int = 5) -> SignalBatch:
    return SignalBatch(
        SignalCursor("producer-scope", "runtime", sequence - 1),
        SignalCursor("producer-scope", "runtime", sequence),
        f"batch-{sequence}",
        (f"operation-{sequence}",),
        f"fingerprint-{sequence}",
        (),
        (),
        (),
    )


@pytest.fixture
def signal_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = database(tmp_path)
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES(?,'codex','/synthetic','full',0)",
        [("current",), ("historical",)],
    )
    add_ticket(conn, "ticket")
    conn.execute("UPDATE tickets SET conversation_id='current' WHERE id='ticket'")
    conn.execute("INSERT INTO ticket_conversations VALUES('historical','ticket')")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def install_fixture(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    install(conn, complete())
    conn.commit()


def test_complete_install_and_private_schema_rollback_preserve_public_shape(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    before = dump(conn)
    old_epoch = navigation_snapshot(conn)["epoch"]
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET conversation_id='historical' WHERE id='ticket'")
    install(conn, complete())
    actual = values(conn)
    addresses = [value for (kind, _), value in actual.items() if kind == "address"]
    assert any(value["conversation_id"] == "historical" and value["current"] for value in addresses)
    assert navigation_snapshot(conn)["epoch"] != old_epoch
    assert navigation_snapshot(conn)["schema_version"] == 3
    assert "attention" not in actual["conversation", "current"]
    assert conn.execute("SELECT COUNT(*) FROM navigation_signal_publications").fetchone()[0] == 2
    conn.rollback()
    assert dump(conn) == before
    install_fixture(conn)
    assert navigation_snapshot(conn)["epoch"] != old_epoch


def test_complete_install_rejects_partial_duplicate_and_invalid_fences_atomically(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    baseline = dump(conn)
    original = complete()
    invalid = (
        replace(original, values=original.values[:1]),
        replace(original, values=(*original.values, original.values[0])),
        replace(original, values=(publication("current", -1), publication("historical"))),
        replace(
            original,
            values=(
                replace(publication("current"), runtime_epoch="old"),
                publication("historical"),
            ),
        ),
        replace(original, cursor=replace(original.cursor, sequence=-1)),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            install(conn, candidate)
        conn.rollback()
        assert dump(conn) == baseline


def test_cursor_accepts_exact_duplicate_and_rejects_conflicts_gaps_and_foreign_epochs(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    accepted = replace(batch(), replacements=(publication("current", 100),))
    conn.execute("BEGIN")
    participate(conn, accepted)
    conn.commit()
    baseline = dump(conn)
    conn.execute("BEGIN")
    participate(conn, accepted)
    conn.commit()
    assert dump(conn) == baseline
    invalid = (
        replace(accepted, fingerprint="different"),
        replace(accepted, replacements=(publication("current", 101),)),
        batch(7),
        batch(4),
        replace(
            batch(6),
            base_cursor=SignalCursor("foreign", "runtime", 5),
            cursor=SignalCursor("foreign", "runtime", 6),
        ),
        replace(
            batch(6),
            base_cursor=SignalCursor("producer-scope", "old", 5),
            cursor=SignalCursor("producer-scope", "old", 6),
        ),
        replace(batch(6), replacements=(publication("current", 101), publication("current", 102))),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            participate(conn, candidate)
        conn.rollback()
        assert dump(conn) == baseline


def test_association_and_order_only_batches_advance_cursor_without_signal_wire_change(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    before = navigation_snapshot(conn)
    conn.execute("BEGIN")
    participate(conn, batch())
    conn.commit()
    assert navigation_snapshot(conn) == before
    assert conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "5"
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET conversation_id='historical' WHERE id='ticket'")
    participate(
        conn,
        replace(
            batch(6),
            associations=(
                SignalAssociationEffect(
                    "ticket",
                    "ticket",
                    "current",
                    "current",
                    "historical",
                ),
            ),
        ),
    )
    conn.commit()
    addresses = [value for (kind, _), value in values(conn).items() if kind == "address"]
    assert any(value["conversation_id"] == "historical" and value["current"] for value in addresses)
    assert conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "6"


def test_deletion_tombstone_rejects_obsolete_delete_and_same_epoch_resurrection(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    conn.execute("BEGIN")
    conn.execute("DELETE FROM ticket_conversations WHERE conversation_id='historical'")
    conn.execute("DELETE FROM conversations WHERE conversation_id='historical'")
    participate(conn, replace(batch(), deletions=(SignalDeletion("historical", "runtime", 20),)))
    conn.commit()
    assert tuple(
        conn.execute(
            "SELECT conversation_revision,attention FROM navigation_signal_publications "
            "WHERE conversation_id='historical'"
        ).fetchone()
    ) == ("20", None)
    before = dump(conn)
    invalid = (
        replace(batch(6), replacements=(publication("historical", 19),)),
        replace(batch(6), deletions=(SignalDeletion("historical", "runtime", 20),)),
        replace(batch(6), deletions=(SignalDeletion("current", "runtime", -1),)),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            participate(conn, candidate)
        conn.rollback()
        assert dump(conn) == before


def test_batch_rollback_restores_source_publication_cursor_and_large_integer_fences(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    huge = 2**70
    conn.execute("BEGIN")
    install(conn, replace(complete(), cursor=SignalCursor("producer-scope", "runtime", huge)))
    conn.commit()
    before = dump(conn)
    conn.execute("BEGIN")
    conn.execute("UPDATE conversations SET latest_sequence=5 WHERE conversation_id='current'")
    participate(conn, replace(batch(huge + 1), replacements=(publication("current", huge + 10),)))
    stored = conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0]
    assert stored == str(huge + 1)
    conn.rollback()
    assert dump(conn) == before


def test_exact_attachment_binding_maps_unavailable_and_rejects_old_sql_installation(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    permit = SignalReadPermit("producer-scope", "runtime", "installation", 1)

    class Attachment:
        available = True

        def capture_permit(self) -> SignalReadPermit:
            self.validate(permit)
            return permit

        def validate(self, value: SignalReadPermit, /) -> None:
            if not self.available or value != permit:
                raise SignalUnavailable("injected attachment unavailable")

        def close(self) -> None:
            self.available = False

    attachment = Attachment()
    callbacks = delivery_authority(attachment)
    assert callbacks.capture_permit() == permit
    callbacks.validate(permit)
    conn.execute("BEGIN")
    callbacks.validate_snapshot(conn, permit)
    install(conn, replace(complete(), installation_id="replacement"))
    with pytest.raises(DeliveryUnavailable, match="installation changed"):
        validate_installation(conn, permit)
    conn.rollback()
    attachment.close()
    with pytest.raises(DeliveryUnavailable, match="injected attachment"):
        callbacks.capture_permit()
    with pytest.raises(DeliveryUnavailable, match="injected attachment"):
        callbacks.validate(permit)
    with pytest.raises(DeliveryUnavailable, match="exact permit"):
        callbacks.validate(object())
