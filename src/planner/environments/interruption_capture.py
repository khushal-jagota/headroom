"""Read-only account of turns ended by an update stop; the installer runs it, bounded.

The helper owns the canonical conversation lease only while it captures evidence.
It cannot initialize an application database or migrate a source schema.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from planner.conversation.deploy_interruptions.contracts import CapturedTurn, CaptureManifest
from planner.conversation.deploy_interruptions.files import write_manifest
from planner.conversation.lifecycle_storage import (
    acquire_runtime_ownership,
    retained_run_is_unresolved,
)

_LOG = logging.getLogger(__name__)


def capture_stopped_database(db_path: Path, deployment_id: str, capture_id: str) -> CaptureManifest:
    """Run only in the disposable helper. All writes are diagnostic files."""
    captured_at = int(time.time())
    # Publish uncertainty first. A timeout after this point retains an honest outcome.
    write_manifest(
        db_path,
        CaptureManifest(
            deployment_id,
            capture_id,
            captured_at,
            "unavailable",
            "The interruption capture did not complete.",
            (),
        ),
    )
    try:
        with acquire_runtime_ownership(str(db_path)):
            with closing(
                sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)
            ) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                turns = read_unresolved_turns(conn)
            result = CaptureManifest(
                deployment_id, capture_id, captured_at, "captured", None, turns
            )
            write_manifest(db_path, result)
            return result
    except Exception as exc:
        result = CaptureManifest(
            deployment_id,
            capture_id,
            captured_at,
            "unavailable",
            f"Interruption capture failed: {type(exc).__name__}: {exc}"[:8192],
            (),
        )
    write_manifest(db_path, result)
    return result


def read_unresolved_turns(
    conn: sqlite3.Connection, *, conversation_ids: tuple[str, ...] | None = None,
    include_custody_unavailable: bool = False,
) -> tuple[CapturedTurn, ...]:
    """Turns still intent or running, outside unresolved Mac runs, optionally for some
    conversations only."""
    tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    epoch_table = "conversation_runtime_epoch" in tables
    row = None
    if epoch_table:
        row = conn.execute(
            "SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1"
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT runtime_epoch FROM signal_publication_meta WHERE singleton=1"
        ).fetchone()
    epoch = str(row[0]) if row is not None else None
    occurrences = "conversation_turn_occurrences" in tables
    only = (
        ""
        if conversation_ids is None
        else f"AND conversation_id IN ({','.join('?' for _ in conversation_ids)}) "
    )
    eligible = "status IN ('intent','running')"
    if include_custody_unavailable:
        eligible += (
            " OR (status='unavailable' AND EXISTS(SELECT 1 FROM conversation_child_custody c "
            "JOIN conversation_turn_occurrences o ON o.conversation_id=c.conversation_id "
            "AND o.occurrence_id=c.occurrence_id AND o.runtime_epoch=c.runtime_epoch "
            "WHERE c.conversation_id=conversation_lifecycle_facts.conversation_id "
            "AND o.turn_number=conversation_lifecycle_facts.turn_number) "
            "AND EXISTS(SELECT 1 FROM conversation_lifecycle_facts prompt "
            "WHERE prompt.conversation_id=conversation_lifecycle_facts.conversation_id "
            "AND prompt.turn_number=conversation_lifecycle_facts.turn_number "
            "AND prompt.kind='attempt' AND prompt.operation='prompt' "
            "AND prompt.status IN ('started','unknown')) "
            "AND NOT EXISTS(SELECT 1 FROM conversation_lifecycle_facts cancelled "
            "WHERE cancelled.conversation_id=conversation_lifecycle_facts.conversation_id "
            "AND cancelled.turn_number=conversation_lifecycle_facts.turn_number "
            "AND cancelled.kind='attempt' AND cancelled.operation='cancel' "
            "AND cancelled.status='accepted'))"
        )
    facts = conn.execute(
        "SELECT conversation_id,turn_number,status FROM conversation_lifecycle_facts "
        "WHERE kind='turn' AND (" + eligible + ") "
        + only
        + "ORDER BY conversation_id,turn_number",
        conversation_ids or (),
    ).fetchall()
    captured: list[CapturedTurn] = []
    for fact in facts:
        conversation_id = str(fact["conversation_id"])
        if retained_run_is_unresolved(conn, conversation_id):
            continue
        occurrence = (
            conn.execute(
                "SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences "
                "WHERE conversation_id=? AND turn_number=?",
                (conversation_id, fact["turn_number"]),
            ).fetchone()
            if occurrences
            else None
        )
        occurrence_id = str(occurrence[0]) if occurrence is not None else None
        turn_epoch = str(occurrence[1]) if occurrence is not None else epoch
        if occurrence_id is None and turn_epoch is None:
            raise ValueError("unresolved legacy turn has no persisted runtime epoch")
        ticket = conn.execute(
            "SELECT id,title,worker_step_claim_revision,worker_step_claim FROM tickets "
            "WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if ticket is not None:
            captured.append(
                CapturedTurn(
                    conversation_id,
                    int(fact["turn_number"]),
                    occurrence_id,
                    turn_epoch,
                    str(fact["status"]),
                    str(ticket["title"]),
                    "ticket",
                    str(ticket["id"]),
                    int(ticket["worker_step_claim_revision"]),
                    str(ticket["worker_step_claim"]),
                )
            )
            continue
        historical_ticket = conn.execute(
            "SELECT h.owner_id,t.title FROM conversation_history_catalog h "
            "JOIN tickets t ON t.id=h.owner_id "
            "WHERE h.conversation_id=? AND h.owner_kind='ticket' LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if historical_ticket is not None:
            captured.append(
                CapturedTurn(
                    conversation_id,
                    int(fact["turn_number"]),
                    occurrence_id,
                    turn_epoch,
                    str(fact["status"]),
                    str(historical_ticket[1]),
                    "ticket",
                    str(historical_ticket[0]),
                    None,
                    None,
                )
            )
            continue
        owner = conn.execute(
            "SELECT h.owner_id,m.title FROM conversation_history_catalog h "
            "JOIN managers m ON m.id=h.owner_id "
            "WHERE h.conversation_id=? AND h.owner_kind='manager' LIMIT 1",
            (conversation_id,),
        ).fetchone()
        chief = conn.execute(
            "SELECT 1 FROM agents WHERE agent_key='chief_of_staff' AND conversation_id=?",
            (conversation_id,),
        ).fetchone()
        captured.append(
            CapturedTurn(
                conversation_id,
                int(fact["turn_number"]),
                occurrence_id,
                turn_epoch,
                str(fact["status"]),
                str(owner[1])
                if owner is not None
                else ("Chief of Staff" if chief is not None else "Agent conversation"),
                "manager" if owner is not None else ("chief" if chief is not None else "unknown"),
                str(owner[0]) if owner is not None else ("chief" if chief is not None else None),
                None,
                None,
            )
        )
    return tuple(captured)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("expected database, deployment ID, capture ID")
    result = capture_stopped_database(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    print(json.dumps({"state": result.state, "detail": (result.detail or "")[:1000]}))


if __name__ == "__main__":
    main()
