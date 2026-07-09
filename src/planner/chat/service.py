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

from planner.chat.contracts import (
    ChatHistory,
    ChatSendResult,
    ChatStreamChunk,
    CommandCatalog,
    CommandRunResult,
    GatewayStatus,
)
from planner.core.adapters.base import GatewayAdapter
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.days.data import read_day
from planner.tickets.contracts import TicketStatus

_log = logging.getLogger("planner.chat")

CHIEF_OF_STAFF_ENTITY_ID = "agent_panels_chief_of_staff"
TOP_LEVEL_AGENT_ENTITY_IDS = frozenset({CHIEF_OF_STAFF_ENTITY_ID})


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
    if entity_id in TOP_LEVEL_AGENT_ENTITY_IDS:
        conn.execute(
            "INSERT OR IGNORE INTO agent_chat_sessions "
            "(id, chat_session_key, created_at, updated_at) VALUES (?, NULL, ?, ?)",
            (entity_id, now, now),
        )
        row = conn.execute(
            "SELECT chat_session_key FROM agent_chat_sessions WHERE id = ?", (entity_id,)
        ).fetchone()
        key: str | None = row["chat_session_key"]
        return "agent_chat_session", key
    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})


def _reject_if_ticket_worker_running(conn: sqlite3.Connection, entity_id: str) -> None:
    if not entity_id.startswith("t_"):
        return
    row = conn.execute(
        "SELECT ticket_status FROM tickets WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return
    if row["ticket_status"] == TicketStatus.agent_running_step.value:
        raise PlannerError(
            ErrorCode.already_running,
            "ticket worker is already running",
            {"entity_id": entity_id},
        )


def _persist_key(
    conn: sqlite3.Connection,
    kind: str,
    entity_id: str,
    stored_key: str | None,
    minted_key: str,
    now: int,
    *,
    reject_running_ticket: bool = False,
) -> str:
    """Persist a (re)minted session key onto the entity and log one chat_session_created
    event, in a single transaction. Two cases: the first reply (stored_key is None), and a
    re-mint — the adapter created a fresh session because the stored key was stale (gateway
    restarted) or rotated, so stored_key is set but the returned key differs. Persisting the
    re-mint is what stops the dead key being resumed forever. On a lost first-write race no
    event is logged and the winner's key is adopted. Returns the effective key."""
    table_by_kind = {
        "ticket": "tickets",
        "day": "days",
        "agent_chat_session": "agent_chat_sessions",
    }
    table = table_by_kind[kind]  # fixed map, never request input
    with _txn(conn):
        if kind == "ticket" and reject_running_ticket:
            row = conn.execute(
                "SELECT ticket_status FROM tickets WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is not None and row["ticket_status"] == TicketStatus.agent_running_step.value:
                raise PlannerError(
                    ErrorCode.already_running,
                    "ticket worker is already running",
                    {"entity_id": entity_id},
                )
        if stored_key is None:
            cursor = conn.execute(
                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? "
                "WHERE id = ? AND chat_session_key IS NULL",
                (minted_key, now, entity_id),
            )
            if cursor.rowcount == 1:
                append_event(
                    conn, entity_id, EventKind.chat_session_created,
                    {"session_key": minted_key}, now,
                )
                return minted_key
            row = conn.execute(
                f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            winner: str = row["chat_session_key"]
            return winner
        # re-mint: replace the known-stale key with the fresh one (the old session is gone).
        conn.execute(
            f"UPDATE {table} SET chat_session_key = ?, updated_at = ? WHERE id = ?",
            (minted_key, now, entity_id),
        )
        append_event(
            conn, entity_id, EventKind.chat_session_created,
            {"session_key": minted_key}, now,
        )
        return minted_key


def send(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    now: int,
) -> ChatSendResult:
    _reject_if_ticket_worker_running(conn, entity_id)
    kind, stored_key = _resolve(conn, entity_id, now)
    effective_key = stored_key

    def persist_session_before_prompt(session_key: str) -> None:
        nonlocal effective_key
        if session_key == effective_key:
            _reject_if_ticket_worker_running(conn, entity_id)
            return
        effective_key = _persist_key(
            conn,
            kind,
            entity_id,
            effective_key,
            session_key,
            now,
            reject_running_ticket=True,
        )

    # The gateway call is IO; it runs outside any transaction.
    try:
        result = gateway.send(stored_key, entity_id, text, persist_session_before_prompt)
    except PlannerError:
        raise  # already structured (offline adapter raises gateway_offline)
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    if result.session_key != effective_key:  # callback-free adapters still persist here
        effective_key = _persist_key(conn, kind, entity_id, effective_key, result.session_key, now)
    if effective_key != result.session_key:  # lost the first-write race; adopt the winner
        result = ChatSendResult(reply_text=result.reply_text, session_key=effective_key)
    return result


def history(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    now: int,
) -> ChatHistory:
    kind, stored_key = _resolve(conn, entity_id, now)
    if stored_key is None:
        return ChatHistory(messages=(), session_key=None)
    try:
        result = gateway.history(stored_key, entity_id)
    except PlannerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    if result.session_key is not None and result.session_key != stored_key:
        effective = _persist_key(conn, kind, entity_id, stored_key, result.session_key, now)
        if effective != result.session_key:
            result = ChatHistory(messages=result.messages, session_key=effective)
    return result


def catalog(gateway: GatewayAdapter) -> CommandCatalog:
    """The gateway's own command/skill registry (pass-through; the route caches it)."""
    return gateway.catalog()


def stream(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    mode: str,
    now: int,
) -> Iterator[ChatStreamChunk]:
    """Stream a chat message or command and persist the completed session key.

    The gateway owns live token production; this service owns the same session-key
    persistence rule used by send()/run_command(). A streaming gateway yields an
    internal ``session`` chunk before the prompt starts so worker tools can resolve
    their ticket while the turn is still running.
    """
    _reject_if_ticket_worker_running(conn, entity_id)
    entity_kind, stored_key = _resolve(conn, entity_id, now)
    effective_key = stored_key

    def persist_session_before_prompt(session_key: str) -> None:
        nonlocal effective_key
        if session_key == effective_key:
            _reject_if_ticket_worker_running(conn, entity_id)
            return
        effective_key = _persist_key(
            conn,
            entity_kind,
            entity_id,
            effective_key,
            session_key,
            now,
            reject_running_ticket=True,
        )

    try:
        chunks = gateway.stream(stored_key, entity_id, text, mode, persist_session_before_prompt)
        for chunk in chunks:
            if chunk.type == "session":
                if chunk.session_key and chunk.session_key != effective_key:
                    persist_session_before_prompt(chunk.session_key)
                continue
            if chunk.type != "done":
                yield chunk
                continue
            session_key = chunk.session_key
            if session_key != effective_key:
                session_key = _persist_key(
                    conn, entity_kind, entity_id, effective_key, session_key, now
                )
            yield ChatStreamChunk(
                type="done",
                reply_text=chunk.reply_text,
                session_key=session_key,
                kind=chunk.kind,
            )
    except PlannerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc


def run_command(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    command: str,
    now: int,
) -> CommandRunResult:
    """Run a /command on the entity's own mind. Same first-reply key-persist + one
    chat_session_created event as send (a command can be the very first message)."""
    _reject_if_ticket_worker_running(conn, entity_id)
    kind, stored_key = _resolve(conn, entity_id, now)
    effective_key = stored_key

    def persist_session_before_prompt(session_key: str) -> None:
        nonlocal effective_key
        if session_key == effective_key:
            _reject_if_ticket_worker_running(conn, entity_id)
            return
        effective_key = _persist_key(
            conn,
            kind,
            entity_id,
            effective_key,
            session_key,
            now,
            reject_running_ticket=True,
        )

    try:
        result = gateway.run_command(
            stored_key, entity_id, command, persist_session_before_prompt
        )
    except PlannerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    if result.session_key != effective_key:
        effective_key = _persist_key(conn, kind, entity_id, effective_key, result.session_key, now)
    if effective_key != result.session_key:
        result = CommandRunResult(
            reply_text=result.reply_text, session_key=effective_key, kind=result.kind
        )
    return result


def status(gateway: GatewayAdapter) -> GatewayStatus:
    result = gateway.status()
    if result.detail is not None:
        _log.info("gateway status detail: %s", result.detail)  # logged, not shown
    return result
