"""Server-owned Chat turns and durable Chat state.

Human ingress resolves a chattable entity, records one turn, and consumes the
gateway stream in the background. The first announced session key is persisted
before prompt delivery. Framework-free: no FastAPI or Pydantic.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from planner.chat import data as chat_data
from planner.chat.contracts import (
    ChatHistory,
    ChatState,
    ChatStateMessage,
    ChatTurn,
    CommandCatalog,
    GatewayStatus,
)
from planner.chat.logic.activity import normalize_gateway_activity
from planner.core.adapters.base import GatewayAdapter
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.days.data import read_day
from planner.files.logic.paths import resolve_chat_file
from planner.tickets.contracts import TicketStatus

_log = logging.getLogger("planner.chat")

CHIEF_OF_STAFF_ENTITY_ID = "agent_panels_chief_of_staff"
TOP_LEVEL_AGENT_ENTITY_IDS = frozenset({CHIEF_OF_STAFF_ENTITY_ID})
_IMAGE_ONLY_MODEL_CUE = "Please respond to the attached image."


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
        agent_key: str | None = row["chat_session_key"]
        return "agent_chat_session", agent_key
    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})


def resolve_chattable_entity(
    conn: sqlite3.Connection, entity_id: str, now: int
) -> tuple[str, str | None]:
    """Validate or materialize the same entity boundary used by chat turns."""
    return _resolve(conn, entity_id, now)


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


def _legacy_state_messages(history_result: ChatHistory) -> tuple[ChatStateMessage, ...]:
    out: list[ChatStateMessage] = []
    for index, message in enumerate(history_result.messages, start=1):
        role = message.role.lower()
        if role in ("user", "human"):
            product_role = "human"
        elif role in ("system", "tool"):
            product_role = "system"
        else:
            product_role = "assistant"
        out.append(
            ChatStateMessage(
                id=-index,
                role=product_role,
                text=message.text,
                created_at=message.created_at,
                turn_id=None,
            )
        )
    return tuple(out)


def state(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    now: int,
) -> ChatState:
    kind, stored_key = _resolve(conn, entity_id, now)
    legacy_messages: tuple[ChatStateMessage, ...] = ()
    if stored_key is not None:
        row = conn.execute(
            "SELECT 1 FROM chat_messages WHERE entity_id = ? LIMIT 1", (entity_id,)
        ).fetchone()
        if row is None:
            result = history(conn, gateway, entity_id, now)
            stored_key = result.session_key
            legacy_messages = _legacy_state_messages(result)
    return chat_data.read_state(
        conn, entity_id, session_key=stored_key, legacy_messages=legacy_messages
    )


def pause_turn(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    now: int,
) -> ChatTurn:
    """Interrupt the visible active chat turn without touching ticket runtime status."""
    _resolve(conn, entity_id, now)
    active = chat_data.read_active_turn(conn, entity_id)
    if active is None:
        raise PlannerError(ErrorCode.not_found, "no active chat turn", {"entity_id": entity_id})
    session_key = active.session_key
    if not session_key:
        raise PlannerError(
            ErrorCode.validation,
            "active chat turn has no session key yet",
            {"entity_id": entity_id, "turn_id": active.id},
        )
    try:
        gateway.interrupt(session_key, entity_id)
    except PlannerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    settled = chat_data.finish_turn(
        conn,
        active.id,
        entity_id=entity_id,
        reply_text="",
        output_role=active.output_role,
        status="interrupted",
        now=now,
    )
    return settled or active


def catalog(gateway: GatewayAdapter) -> CommandCatalog:
    """The gateway's own command/skill registry (pass-through; the route caches it)."""
    return gateway.catalog()


def start_human_turn(
    conn_factory: Callable[[], sqlite3.Connection],
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    mode: str,
    now: int,
    now_fn: Callable[[], int] | None = None,
    *,
    image_references: Sequence[str] = (),
    db_path: str | Path | None = None,
) -> ChatTurn:
    if mode not in ("message", "command"):
        raise PlannerError(ErrorCode.validation, "mode must be message or command")
    conn = conn_factory()
    try:
        _reject_if_ticket_worker_running(conn, entity_id)
        _resolve(conn, entity_id, now)
        image_paths = _resolve_turn_images(db_path, entity_id, image_references)
        visible_text = _visible_human_text(text, image_references)
        model_text = text or (_IMAGE_ONLY_MODEL_CUE if image_paths else "")
        turn = chat_data.start_turn(
            conn,
            entity_id,
            origin="human",
            mode=mode,
            visible_role="human",
            visible_text=visible_text,
            output_role="system" if mode == "command" else "assistant",
            phase="thinking",
            activity_label="Thinking",
            now=now,
        )
    finally:
        conn.close()

    thread = threading.Thread(
        target=_run_human_turn,
        args=(
            conn_factory,
            gateway,
            entity_id,
            turn.id,
            model_text,
            mode,
            now_fn or _unix_now,
            image_paths,
        ),
        name=f"chat-turn-{turn.id}",
        daemon=True,
    )
    thread.start()
    return turn


def _run_human_turn(
    conn_factory: Callable[[], sqlite3.Connection],
    gateway: GatewayAdapter,
    entity_id: str,
    turn_id: str,
    text: str,
    mode: str,
    now_fn: Callable[[], int],
    image_paths: tuple[Path, ...] = (),
) -> None:
    conn = conn_factory()
    try:
        now = now_fn()
        _reject_if_ticket_worker_running(conn, entity_id)
        entity_kind, stored_key = _resolve(conn, entity_id, now)
        effective_key = stored_key
        announced_session_key: str | None = None

        def persist_session_before_prompt(session_key: str) -> None:
            nonlocal announced_session_key, effective_key
            if session_key == announced_session_key:
                _reject_if_ticket_worker_running(conn, entity_id)
            else:
                announced_session_key = session_key
                if session_key == effective_key:
                    _reject_if_ticket_worker_running(conn, entity_id)
                else:
                    effective_key = _persist_key(
                        conn,
                        entity_kind,
                        entity_id,
                        effective_key,
                        session_key,
                        now_fn(),
                        reject_running_ticket=True,
                    )
            assert effective_key is not None
            chat_data.attach_session_key(
                conn, turn_id, entity_id=entity_id, session_key=effective_key, now=now_fn()
            )

        if not image_paths:
            chunks = gateway.stream(
                stored_key, entity_id, text, mode, persist_session_before_prompt
            )
        else:
            chunks = gateway.stream(
                stored_key,
                entity_id,
                text,
                mode,
                persist_session_before_prompt,
                image_paths=image_paths,
            )
        for chunk in chunks:
            now = now_fn()
            if chunk.type == "session":
                if chunk.session_key:
                    persist_session_before_prompt(chunk.session_key)
                continue
            if chunk.type == "activity":
                if chunk.activity is not None:
                    chat_data.record_turn_activity(
                        conn,
                        turn_id,
                        entity_id=entity_id,
                        observation=chunk.activity,
                        now=now,
                    )
                else:
                    label = chunk.text.strip() or "Working"
                    chat_data.set_turn_activity(
                        conn,
                        turn_id,
                        entity_id=entity_id,
                        phase="doing",
                        activity_label=label,
                        now=now,
                    )
                continue
            if chunk.type == "token":
                chat_data.append_turn_output(
                    conn, turn_id, entity_id=entity_id, delta=chunk.text, now=now
                )
                continue
            if chunk.type == "done":
                session_key = chunk.session_key
                if session_key and session_key != effective_key:
                    if session_key == announced_session_key:
                        assert effective_key is not None
                        session_key = effective_key
                    else:
                        session_key = _persist_key(
                            conn, entity_kind, entity_id, effective_key, session_key, now
                        )
                    chat_data.attach_session_key(
                        conn, turn_id, entity_id=entity_id, session_key=session_key, now=now
                    )
                output_role = "system" if chunk.kind == "system" else "assistant"
                chat_data.finish_turn(
                    conn,
                    turn_id,
                    entity_id=entity_id,
                    reply_text=chunk.reply_text,
                    output_role=output_role,
                    now=now,
                )
                return
        chat_data.fail_turn(
            conn,
            turn_id,
            entity_id=entity_id,
            error="chat turn ended without completion",
            now=now_fn(),
        )
    except PlannerError as exc:
        chat_data.fail_turn(
            conn, turn_id, entity_id=entity_id, error=exc.message, now=now_fn()
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("chat turn crashed (entity=%s turn=%s)", entity_id, turn_id)
        chat_data.fail_turn(
            conn, turn_id, entity_id=entity_id, error=f"chat turn crashed: {exc}", now=now_fn()
        )
    finally:
        conn.close()


def _resolve_turn_images(
    db_path: str | Path | None, entity_id: str, image_references: Sequence[str]
) -> tuple[Path, ...]:
    if not image_references:
        return ()
    if db_path is None:
        raise PlannerError(ErrorCode.validation, "image storage is unavailable")
    return tuple(
        _resolve_turn_image(db_path, entity_id, image_reference)
        for image_reference in image_references
    )


def _resolve_turn_image(db_path: str | Path, entity_id: str, image_reference: str) -> Path:
    parsed = urlsplit(image_reference)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise PlannerError(ErrorCode.validation, "image reference must be a managed chat file")
    parts = parsed.path.split("/")
    if len(parts) < 5 or parts[:4] != ["", "files", "chats", entity_id]:
        raise PlannerError(
            ErrorCode.validation,
            "image reference must belong to this chat",
            {"entity_id": entity_id},
        )
    relative_path = unquote("/".join(parts[4:]))
    try:
        chat_file = resolve_chat_file(db_path, entity_id, relative_path)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, "invalid managed chat image") from exc
    canonical_reference = (
        f"/files/chats/{quote(chat_file.entity_id, safe='')}/"
        f"{quote(chat_file.relative_path, safe='/')}"
    )
    if image_reference != canonical_reference:
        raise PlannerError(ErrorCode.validation, "image reference is not canonical")
    return chat_file.absolute_path


def _visible_human_text(text: str, image_references: Sequence[str]) -> str:
    if not image_references:
        return text
    markdown = "\n".join(
        f"![Attached image]({image_reference})" for image_reference in image_references
    )
    return f"{text}\n\n{markdown}" if text else markdown


def _unix_now() -> int:
    import time

    return int(time.time())


def start_worker_turn(
    conn: sqlite3.Connection, entity_id: str, *, visible_text: str, now: int
) -> ChatTurn:
    return chat_data.start_turn(
        conn,
        entity_id,
        origin="worker",
        mode="worker_step",
        visible_role="worker",
        visible_text=visible_text,
        output_role="assistant",
        phase="thinking",
        activity_label="Thinking",
        now=now,
    )


def attach_worker_session_key(
    conn: sqlite3.Connection, entity_id: str, turn_id: str, session_key: str, now: int
) -> None:
    chat_data.attach_session_key(
        conn, turn_id, entity_id=entity_id, session_key=session_key, now=now
    )


def observe_worker_gateway_event(
    conn: sqlite3.Connection, entity_id: str, turn_id: str, event: dict[str, object], now: int
) -> None:
    etype = str(event.get("type") or "")
    raw = event.get("payload")
    payload = raw if isinstance(raw, dict) else {}
    if etype == "message.delta":
        delta = str(payload.get("text") or payload.get("delta") or "")
        chat_data.append_turn_output(conn, turn_id, entity_id=entity_id, delta=delta, now=now)
        return
    observation = normalize_gateway_activity(etype, payload)
    if observation is not None:
        chat_data.record_turn_activity(
            conn, turn_id, entity_id=entity_id, observation=observation, now=now
        )


def finish_worker_turn(
    conn: sqlite3.Connection,
    entity_id: str,
    turn_id: str,
    reply_text: str,
    status: str,
    now: int,
) -> None:
    chat_data.finish_turn(
        conn,
        turn_id,
        entity_id=entity_id,
        reply_text=reply_text,
        output_role="assistant",
        status=status,
        now=now,
    )


def fail_worker_turn(
    conn: sqlite3.Connection, entity_id: str, turn_id: str, error: str, now: int
) -> None:
    chat_data.fail_turn(conn, turn_id, entity_id=entity_id, error=error, now=now)


def status(gateway: GatewayAdapter) -> GatewayStatus:
    result = gateway.status()
    if result.detail is not None:
        _log.info("gateway status detail: %s", result.detail)  # logged, not shown
    return result
