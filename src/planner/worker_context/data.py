"""SQLite operations for the keyed pending worker-context map.

These functions do not open transactions. Producers call ``set_context`` inside the
same transaction as their domain edit; the composed runtime service opens its own
short connections for snapshots and acknowledgements.
"""

from __future__ import annotations

import sqlite3

from planner.worker_context.contracts import (
    PendingWorkerContext,
    WorkerContextReceipt,
    WorkerContextSnapshot,
)


def set_context(
    conn: sqlite3.Connection, worker_entity_id: str, context_key: str, text: str
) -> PendingWorkerContext:
    conn.execute(
        "INSERT INTO pending_worker_context "
        "(worker_entity_id, context_key, text, revision) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(worker_entity_id, context_key) DO UPDATE SET "
        "text = excluded.text, revision = pending_worker_context.revision + 1",
        (worker_entity_id, context_key, text),
    )
    row = conn.execute(
        "SELECT context_key, text, revision FROM pending_worker_context "
        "WHERE worker_entity_id = ? AND context_key = ?",
        (worker_entity_id, context_key),
    ).fetchone()
    if row is None:  # pragma: no cover - the upsert above guarantees the row
        raise RuntimeError("pending worker context upsert did not create a row")
    return PendingWorkerContext(
        context_key=str(row["context_key"]),
        text=str(row["text"]),
        revision=int(row["revision"]),
    )


def snapshot(conn: sqlite3.Connection, worker_entity_id: str) -> WorkerContextSnapshot:
    rows = conn.execute(
        "SELECT context_key, text, revision FROM pending_worker_context "
        "WHERE worker_entity_id = ? ORDER BY context_key",
        (worker_entity_id,),
    ).fetchall()
    return WorkerContextSnapshot(
        items=tuple(
            PendingWorkerContext(
                context_key=str(row["context_key"]),
                text=str(row["text"]),
                revision=int(row["revision"]),
            )
            for row in rows
        )
    )


def acknowledge(
    conn: sqlite3.Connection,
    worker_entity_id: str,
    receipts: tuple[WorkerContextReceipt, ...],
) -> None:
    for receipt in receipts:
        conn.execute(
            "DELETE FROM pending_worker_context "
            "WHERE worker_entity_id = ? AND context_key = ? AND revision = ?",
            (worker_entity_id, receipt.context_key, receipt.revision),
        )
