"""Durable, one-use revision feedback for the next Ticket worker prompt."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Final

from planner.core.contracts import Principal, PrincipalKind
from planner.worker_context.contracts import PendingWorkerContext, WorkerContextReceipt

REVISION_FEEDBACK_CONTEXT_KEY: Final = "ticket_revision_feedback"


@dataclass(frozen=True)
class RevisionFeedbackItem:
    sender: Principal
    message: str


@dataclass(frozen=True)
class PendingRevisionFeedback:
    ticket_id: str
    stage: str
    items: tuple[RevisionFeedbackItem, ...]
    revision: int

    def as_worker_context(self) -> PendingWorkerContext:
        blocks = [
            f"Revision feedback from {item.sender.kind.value} {item.sender.id} "
            f"for stage {self.stage}:\n{item.message}"
            for item in self.items
        ]
        return PendingWorkerContext(
            context_key=REVISION_FEEDBACK_CONTEXT_KEY,
            text="\n\n".join(blocks),
            revision=self.revision,
        )


def _items_to_json(items: tuple[RevisionFeedbackItem, ...]) -> str:
    return json.dumps(
        [
            {
                "sender_kind": item.sender.kind.value,
                "sender_id": item.sender.id,
                "message": item.message,
            }
            for item in items
        ],
        separators=(",", ":"),
    )


def _items_from_json(raw: str) -> tuple[RevisionFeedbackItem, ...]:
    values = json.loads(raw)
    return tuple(
        RevisionFeedbackItem(
            sender=Principal(PrincipalKind(value["sender_kind"]), str(value["sender_id"])),
            message=str(value["message"]),
        )
        for value in values
    )


def set_feedback(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    stage: str,
    sender: Principal,
    message: str,
    now: int,
) -> PendingRevisionFeedback:
    """Replace any older feedback: one Ticket has at most one pending feedback batch."""
    items = (RevisionFeedbackItem(sender=sender, message=message),)
    conn.execute(
        "INSERT INTO ticket_revision_feedback "
        "(ticket_id, stage, feedback_json, revision, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?) "
        "ON CONFLICT(ticket_id) DO UPDATE SET "
        "stage=excluded.stage, feedback_json=excluded.feedback_json, "
        "revision=ticket_revision_feedback.revision + 1, updated_at=excluded.updated_at",
        (ticket_id, stage, _items_to_json(items), now, now),
    )
    row = conn.execute(
        "SELECT stage, feedback_json, revision FROM ticket_revision_feedback WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - the upsert guarantees the row
        raise RuntimeError("revision feedback upsert did not create a row")
    return PendingRevisionFeedback(
        ticket_id=ticket_id,
        stage=str(row["stage"]),
        items=_items_from_json(str(row["feedback_json"])),
        revision=int(row["revision"]),
    )


def snapshot(conn: sqlite3.Connection, ticket_id: str) -> PendingRevisionFeedback | None:
    """Read feedback only while the Ticket remains at the rejected Stage."""
    row = conn.execute(
        "SELECT f.stage, f.feedback_json, f.revision "
        "FROM ticket_revision_feedback f JOIN tickets t ON t.id=f.ticket_id "
        "WHERE f.ticket_id=? AND f.stage=t.stage",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return PendingRevisionFeedback(
        ticket_id=ticket_id,
        stage=str(row["stage"]),
        items=_items_from_json(str(row["feedback_json"])),
        revision=int(row["revision"]),
    )


def acknowledge(
    conn: sqlite3.Connection, ticket_id: str, receipt: WorkerContextReceipt
) -> None:
    conn.execute(
        "DELETE FROM ticket_revision_feedback WHERE ticket_id=? AND revision=?",
        (ticket_id, receipt.revision),
    )


def discard(conn: sqlite3.Connection, ticket_id: str) -> None:
    conn.execute("DELETE FROM ticket_revision_feedback WHERE ticket_id=?", (ticket_id,))
