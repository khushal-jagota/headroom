"""The chat passthrough (§11). Resolves the chat entity (ticket or day), sends
through the gateway adapter, and — on the first reply only — persists the minted
session key onto the entity and logs one chat_session_created event in a single
transaction. Framework-free: no FastAPI/pydantic (SPEC §14 / server.py docstring).
Times arrive as unix-second ints from the caller's clock."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import GatewayAdapter
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.days.data import read_day

_log = logging.getLogger("planner.chat")


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _resolve(conn: sqlite3.Connection, entity_id: str, now: int) -> tuple[str, str | None]:
    if entity_id.startswith("t_"):
        row = conn.execute(
            "SELECT chat_session_key FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
        key: str | None = row["chat_session_key"]
        return "ticket", key
    if entity_id.startswith("day_"):
        # A day id is a canonical YYYY-MM-DD; round-trip the suffix because
        # fromisoformat alone accepts compact variants. A non-canonical id names
        # no day, so it is not_found rather than a materialized empty day.
        raw = entity_id[len("day_") :]
        try:
            canonical = date.fromisoformat(raw).isoformat() == raw
        except ValueError:
            canonical = False
        if not canonical:
            raise PlannerError(
                ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id}
            )
        day = read_day(conn, entity_id, now)  # §3.4: materializes if absent
        return "day", day.chat_session_key
    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})


def send(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    now: int,
) -> ChatSendResult:
    kind, stored_key = _resolve(conn, entity_id, now)
    # The gateway call is IO; it runs outside any transaction.
    try:
        result = gateway.send(stored_key, entity_id, text)
    except PlannerError:
        raise  # already structured (offline adapter raises gateway_offline)
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    if stored_key is None:  # first reply → persist the key and log one event
        table = "tickets" if kind == "ticket" else "days"  # fixed map, never request input
        with _txn(conn):
            cursor = conn.execute(
                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? "
                "WHERE id = ? AND chat_session_key IS NULL",
                (result.session_key, now, entity_id),
            )
            if cursor.rowcount == 1:
                append_event(
                    conn,
                    entity_id,
                    EventKind.chat_session_created,
                    {"session_key": result.session_key},
                    now,
                )
            else:  # a concurrent first send won under the write lock; adopt its key
                row = conn.execute(
                    f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
                ).fetchone()
                result = ChatSendResult(
                    reply_text=result.reply_text, session_key=row["chat_session_key"]
                )
    return result


def status(gateway: GatewayAdapter) -> GatewayStatus:
    result = gateway.status()
    if result.detail is not None:
        _log.info("gateway status detail: %s", result.detail)  # logged, not shown
    return result
