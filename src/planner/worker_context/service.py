"""Composition boundary for SQLite-backed pending worker context."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from planner.worker_context import data
from planner.worker_context.contracts import (
    PreparedWorkerPrompt,
    WorkerContextReceipt,
    WorkerContextSnapshot,
)

_CONTEXT_START = "\n\n[Pending worker context]\n"
_CONTEXT_END = "\n[/Pending worker context]"


def _prepare_prompt(prompt_text: str, snapshot: WorkerContextSnapshot) -> PreparedWorkerPrompt:
    if not snapshot.items:
        return PreparedWorkerPrompt(model_text=prompt_text, receipts=())
    context_lines = "\n".join(f"- {item.text}" for item in snapshot.items)
    return PreparedWorkerPrompt(
        model_text=f"{prompt_text}{_CONTEXT_START}{context_lines}{_CONTEXT_END}",
        receipts=snapshot.receipts,
    )


class SqliteWorkerContextService:
    def __init__(self, conn_factory: Callable[[], sqlite3.Connection]) -> None:
        self._conn_factory = conn_factory

    def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
        conn = self._conn_factory()
        try:
            return _prepare_prompt(prompt_text, data.snapshot(conn, worker_entity_id))
        finally:
            conn.close()

    def acknowledge(
        self, worker_entity_id: str, receipts: tuple[WorkerContextReceipt, ...]
    ) -> None:
        conn = self._conn_factory()
        try:
            conn.execute("BEGIN IMMEDIATE")
            data.acknowledge(conn, worker_entity_id, receipts)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class EmptyWorkerContextService:
    """Compatibility default for gateways composed without a context store."""

    def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
        return PreparedWorkerPrompt(model_text=prompt_text, receipts=())

    def acknowledge(
        self, worker_entity_id: str, receipts: tuple[WorkerContextReceipt, ...]
    ) -> None:
        return None
