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
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote, unquote, urlsplit

from planner.chat import data as chat_data
from planner.chat.contracts import (
    ChatActivityObservation,
    ChatPendingClarification,
    ChatState,
    ChattableEntityKind,
    ChatTurn,
    ChatTurnRequest,
    CommandCatalog,
    GatewayStatus,
    HumanChatCompletion,
    HumanChatOutputDelta,
)
from planner.chat.logic.activity import normalize_gateway_activity
from planner.core.adapters.base import GatewayAdapter
from planner.core.errors import ErrorCode, PlannerError
from planner.days.data import read_day
from planner.files.logic.paths import resolve_chat_file
from planner.tickets.contracts import TicketStatus

_log = logging.getLogger("planner.chat")

CHIEF_OF_STAFF_ENTITY_ID = "agent_panels_chief_of_staff"
TOP_LEVEL_AGENT_ENTITY_IDS = frozenset({CHIEF_OF_STAFF_ENTITY_ID})
_IMAGE_ONLY_MODEL_CUE = "Please respond to the attached image."
_HUMAN_RESTART_RECOVERY_MESSAGE = (
    "Panels restarted. Continue the interrupted response in this existing session."
)
_HUMAN_CONTINUATION_MESSAGE = "Continue the previous response in this existing session."


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


def _resolve(
    conn: sqlite3.Connection, entity_id: str, now: int
) -> tuple[ChattableEntityKind, str | None]:
    if entity_id.startswith("t_"):
        row = conn.execute(
            "SELECT employee_session_id FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
        key: str | None = row["employee_session_id"]
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
) -> tuple[ChattableEntityKind, str | None]:
    """Validate or materialize the same entity boundary used by chat turns."""
    return _resolve(conn, entity_id, now)


def _validate_chattable_entity_for_state(
    conn: sqlite3.Connection,
    entity_id: str,
) -> None:
    if entity_id.startswith("t_"):
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (entity_id,)).fetchone() is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
        return
    if entity_id.startswith("day_"):
        raw = entity_id[len("day_") :]
        try:
            canonical = date.fromisoformat(raw).isoformat() == raw
        except ValueError:
            canonical = False
        if canonical:
            return
    if entity_id in TOP_LEVEL_AGENT_ENTITY_IDS:
        return
    raise PlannerError(
        ErrorCode.not_found,
        "no chattable entity for id",
        {"entity_id": entity_id},
    )


def state(conn: sqlite3.Connection, entity_id: str) -> ChatState:
    _validate_chattable_entity_for_state(conn, entity_id)
    return chat_data.read_state(conn, entity_id)


def catalog(gateway: GatewayAdapter) -> CommandCatalog:
    """The gateway's own command/skill registry (pass-through; the route caches it)."""
    return gateway.catalog()


@dataclass(frozen=True)
class _AdmittedHumanChatTurn:
    entity_id: str
    entity_kind: ChattableEntityKind
    turn_id: str
    expected_session_key: str | None
    model_text: str
    mode: Literal["message", "command"]
    image_paths: tuple[Path, ...]
    gateway: GatewayAdapter
    force_fresh_session: bool
    require_existing_session: bool


class ChatTurnLifecycle:
    """The one deep module for human Chat admission, execution, and visible Pause."""

    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        gateway_provider: Callable[[], GatewayAdapter],
        now: Callable[[], int],
        db_path: str | Path,
        chief_pool_owned: Callable[[], bool] = lambda: False,
    ) -> None:
        self._conn_factory = conn_factory
        self._gateway_provider = gateway_provider
        self._now = now
        self._db_path = db_path
        self._chief_pool_owned = chief_pool_owned
        self._clarification_answer_lock = threading.Lock()

    def _reject_chief_pool_owned_crossover(self, entity_id: str) -> None:
        """Pool-ownership crossover guard: when the relay backend owns the Chief employee,
        no legacy chat lifecycle op may reach a gateway for the Chief (that would spawn a
        contending child against the pool-owned durable session). Raise BEFORE any gateway
        is captured. Ticket/day entities never hit this branch — only the Chief entity."""
        if self._chief_pool_owned() and entity_id == CHIEF_OF_STAFF_ENTITY_ID:
            raise PlannerError(
                ErrorCode.validation,
                "the Chief employee is pool-owned; use the neutral relay pane",
                {"entity_id": entity_id},
            )

    @staticmethod
    def _ensure_ticket_worker_not_running(
        conn: sqlite3.Connection, entity_id: str
    ) -> None:
        if not entity_id.startswith("t_"):
            return
        row = conn.execute(
            "SELECT ticket_status FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is not None and row["ticket_status"] == TicketStatus.agent_running_step.value:
            raise PlannerError(
                ErrorCode.already_running,
                "ticket worker is already running",
                {"entity_id": entity_id},
            )

    @staticmethod
    def _validate_request(request: ChatTurnRequest) -> Literal["message", "command"]:
        if request.mode not in ("message", "command"):
            raise PlannerError(ErrorCode.validation, "mode must be message or command")
        if not request.text and not request.image_references:
            raise PlannerError(ErrorCode.validation, "text is required")
        if request.image_references and request.mode != "message":
            raise PlannerError(ErrorCode.validation, "images are supported only for messages")
        return cast(Literal["message", "command"], request.mode)

    def start_human_turn(self, entity_id: str, request: ChatTurnRequest) -> ChatTurn:
        self._reject_chief_pool_owned_crossover(entity_id)
        mode = self._validate_request(request)
        admission_now = self._now()
        conn = self._conn_factory()
        try:
            self._ensure_ticket_worker_not_running(conn, entity_id)
            _resolve(conn, entity_id, admission_now)
            image_paths = _resolve_turn_images(
                self._db_path, entity_id, request.image_references
            )
            visible_text = _visible_human_text(request.text, request.image_references)
            model_text = request.text or (_IMAGE_ONLY_MODEL_CUE if image_paths else "")
            gateway = self._gateway_provider()
            with _txn(conn):
                entity_kind, expected_session_key = _resolve(conn, entity_id, admission_now)
                self._ensure_ticket_worker_not_running(conn, entity_id)
                turn = chat_data.start_turn_in_transaction(
                    conn,
                    entity_id,
                    origin="human",
                    mode=mode,
                    visible_role="human",
                    visible_text=visible_text,
                    output_role="system" if mode == "command" else "assistant",
                    phase="thinking",
                    activity_label="Thinking",
                    now=admission_now,
                )
        finally:
            conn.close()

        admitted = _AdmittedHumanChatTurn(
            entity_id=entity_id,
            entity_kind=entity_kind,
            turn_id=turn.id,
            expected_session_key=expected_session_key,
            model_text=model_text,
            mode=mode,
            image_paths=image_paths,
            gateway=gateway,
            force_fresh_session=mode == "command" and request.text == "/new",
            require_existing_session=False,
        )
        self._launch_execution(admitted)
        return turn

    def continue_human_turn(self, entity_id: str, turn_id: str) -> ChatTurn:
        """Continue one eligible terminal turn without replaying its original prompt."""
        self._reject_chief_pool_owned_crossover(entity_id)
        now = self._now()
        conn = self._conn_factory()
        try:
            entity_kind, _ = _resolve(conn, entity_id, now)
            self._ensure_ticket_worker_not_running(conn, entity_id)
            turn, admitted_session_key = chat_data.start_human_continuation_turn(
                conn,
                entity_id,
                turn_id,
                entity_kind=entity_kind,
                visible_text=_HUMAN_CONTINUATION_MESSAGE,
                now=now,
            )
        finally:
            conn.close()
        gateway = self._gateway_provider()
        mode = cast(Literal["message", "command"], turn.mode)
        admitted = _AdmittedHumanChatTurn(
            entity_id=entity_id,
            entity_kind=entity_kind,
            turn_id=turn.id,
            expected_session_key=admitted_session_key,
            model_text=_HUMAN_CONTINUATION_MESSAGE,
            mode=mode,
            image_paths=(),
            gateway=gateway,
            force_fresh_session=False,
            require_existing_session=True,
        )
        self._launch_execution(admitted)
        return turn

    def recover_human_turn(
        self,
        entity_id: str,
        mode: str,
    ) -> ChatTurn | None:
        """Strictly resume one stale product-visible human turn after restart."""
        if mode not in ("message", "command"):
            raise PlannerError(ErrorCode.validation, "mode must be message or command")
        recovery_mode = cast(Literal["message", "command"], mode)
        now = self._now()
        conn = self._conn_factory()
        try:
            if entity_id.startswith("t_"):
                row = conn.execute(
                    "SELECT ticket_status FROM tickets WHERE id = ?", (entity_id,)
                ).fetchone()
                if (
                    row is not None
                    and row["ticket_status"] == TicketStatus.agent_running_step.value
                ):
                    return None
            entity_kind, stored_session_key = _resolve(conn, entity_id, now)
            active = chat_data.read_active_turn(conn, entity_id)
            if active is None:
                return None
            if self._chief_pool_owned() and entity_id == CHIEF_OF_STAFF_ENTITY_ID:
                # Pool-ownership crossover guard, settle-not-raise variant: the pool now
                # owns the Chief's durable session, so a stale running human turn left across
                # a restart cannot be resumed against a live chief child. SETTLE it (no
                # gateway capture) and return; the neutral relay pane owns the Chief now.
                chat_data.settle_chat_turn(
                    conn,
                    active.id,
                    entity_id=entity_id,
                    status="errored",
                    reply_text="",
                    output_role="system"
                    if active.output_role == "system"
                    else "assistant",
                    error="Chief is pool-owned; stale turn superseded",
                    now=now,
                )
                return None
            if stored_session_key is None:
                chat_data.settle_chat_turn(
                    conn,
                    active.id,
                    entity_id=entity_id,
                    status="errored",
                    reply_text="",
                    output_role="system"
                    if active.output_role == "system"
                    else "assistant",
                    error="restart recovery has no existing session",
                    now=now,
                )
                return None
            gateway = self._gateway_provider()
            turn = chat_data.roll_running_turn_for_recovery(
                conn,
                entity_id,
                origin="human",
                mode=recovery_mode,
                visible_role="system",
                visible_text=_HUMAN_RESTART_RECOVERY_MESSAGE,
                output_role="system" if recovery_mode == "command" else "assistant",
                phase="thinking",
                activity_label="Restarting chat",
                now=now,
                expected_session_key=stored_session_key,
            )
            if turn is None:
                return None
        finally:
            conn.close()

        admitted = _AdmittedHumanChatTurn(
            entity_id=entity_id,
            entity_kind=entity_kind,
            turn_id=turn.id,
            expected_session_key=stored_session_key,
            model_text=_HUMAN_RESTART_RECOVERY_MESSAGE,
            mode=recovery_mode,
            image_paths=(),
            gateway=gateway,
            force_fresh_session=False,
            require_existing_session=True,
        )
        self._launch_execution(admitted)
        return turn

    def pause_active_turn(self, entity_id: str) -> ChatTurn:
        self._reject_chief_pool_owned_crossover(entity_id)
        conn = self._conn_factory()
        try:
            now = self._now()
            _resolve(conn, entity_id, now)
            active = chat_data.read_active_turn(conn, entity_id)
            if active is None:
                raise PlannerError(
                    ErrorCode.not_found, "no active chat turn", {"entity_id": entity_id}
                )
            session_key = chat_data.read_running_turn_session_key(
                conn,
                active.id,
                entity_id=entity_id,
            )
            if not session_key:
                raise PlannerError(
                    ErrorCode.validation,
                    "active chat turn has no session key yet",
                    {"entity_id": entity_id, "turn_id": active.id},
                )
            try:
                self._gateway_provider().interrupt(session_key, entity_id)
            except PlannerError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PlannerError(
                    ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
                ) from exc
            return chat_data.settle_chat_turn(
                conn,
                active.id,
                entity_id=entity_id,
                status="interrupted",
                reply_text="",
                output_role="system" if active.output_role == "system" else "assistant",
                error=None,
                now=now,
            )
        finally:
            conn.close()

    def _answer_pending_clarification(
        self, entity_id: str, *, request_id: str, answer: str
    ) -> ChatTurn:
        self._reject_chief_pool_owned_crossover(entity_id)
        with self._clarification_answer_lock:
            return self._answer_pending_clarification_serially(
                entity_id,
                request_id=request_id,
                answer=answer,
            )

    def _answer_pending_clarification_serially(
        self, entity_id: str, *, request_id: str, answer: str
    ) -> ChatTurn:
        answer = answer.strip()
        if not request_id.strip():
            raise PlannerError(ErrorCode.validation, "request_id is required")
        if not answer:
            raise PlannerError(ErrorCode.validation, "answer is required")
        conn = self._conn_factory()
        try:
            _resolve(conn, entity_id, self._now())
            turn_id, session_key, _employee_session_id, clarification = (
                chat_data.read_pending_clarification_answer_target(
                    conn,
                    entity_id,
                    request_id,
                )
            )
            gateway = self._gateway_provider()
            try:
                gateway.respond_to_clarification(
                    session_key,
                    entity_id,
                    request_id,
                    answer,
                )
            except PlannerError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PlannerError(
                    ErrorCode.gateway_offline,
                    "clarification response failed",
                    {"cause": str(exc), "entity_id": entity_id},
                ) from exc
            return chat_data.mirror_accepted_clarification_answer(
                conn,
                entity_id,
                turn_id=turn_id,
                request_id=request_id,
                question=clarification.question,
                answer=answer,
                now=self._now(),
            )
        finally:
            conn.close()

    def _launch_execution(self, admitted: _AdmittedHumanChatTurn) -> None:
        thread = threading.Thread(
            target=self._execute_turn,
            args=(admitted,),
            name=f"chat-turn-{admitted.turn_id}",
            daemon=True,
        )
        thread.start()

    def _execute_turn(self, admitted: _AdmittedHumanChatTurn) -> None:
        conn = self._conn_factory()
        effective_session_key = admitted.expected_session_key
        force_fresh_pending = admitted.force_fresh_session

        def bind_session_key(candidate_session_key: str) -> str:
            nonlocal effective_session_key, force_fresh_pending
            effective_session_key = chat_data.bind_human_turn_session(
                conn,
                admitted.turn_id,
                entity_kind=admitted.entity_kind,
                entity_id=admitted.entity_id,
                expected_session_key=effective_session_key,
                candidate_session_key=candidate_session_key,
                force_fresh_session=force_fresh_pending,
                now=self._now(),
            )
            force_fresh_pending = False
            return effective_session_key

        try:
            observations = admitted.gateway.run_human_turn(
                admitted.expected_session_key,
                admitted.entity_id,
                admitted.model_text,
                admitted.mode,
                bind_session_key,
                admitted.image_paths,
                require_existing_session=admitted.require_existing_session,
            )
            for observation in observations:
                now = self._now()
                if isinstance(observation, ChatActivityObservation):
                    chat_data.record_turn_activity(
                        conn,
                        admitted.turn_id,
                        entity_id=admitted.entity_id,
                        observation=observation,
                        now=now,
                    )
                elif isinstance(observation, HumanChatOutputDelta):
                    chat_data.append_turn_output(
                        conn,
                        admitted.turn_id,
                        entity_id=admitted.entity_id,
                        delta=observation.text,
                        now=now,
                    )
                elif isinstance(observation, HumanChatCompletion):
                    chat_data.settle_chat_turn(
                        conn,
                        admitted.turn_id,
                        entity_id=admitted.entity_id,
                        status="complete",
                        reply_text=observation.text,
                        output_role=observation.role,
                        error=None,
                        now=now,
                    )
                    return
                else:
                    raise PlannerError(
                        ErrorCode.validation, "unsupported human chat observation"
                    )
            chat_data.settle_chat_turn(
                conn,
                admitted.turn_id,
                entity_id=admitted.entity_id,
                status="errored",
                reply_text="",
                output_role="system" if admitted.mode == "command" else "assistant",
                error="chat turn ended without completion",
                now=self._now(),
            )
        except PlannerError as exc:
            chat_data.settle_chat_turn(
                conn,
                admitted.turn_id,
                entity_id=admitted.entity_id,
                status="errored",
                reply_text="",
                output_role="system" if admitted.mode == "command" else "assistant",
                error=exc.message,
                now=self._now(),
            )
        except Exception as exc:  # noqa: BLE001
            _log.exception(
                "chat turn crashed (entity=%s turn=%s)",
                admitted.entity_id,
                admitted.turn_id,
            )
            chat_data.settle_chat_turn(
                conn,
                admitted.turn_id,
                entity_id=admitted.entity_id,
                status="errored",
                reply_text="",
                output_role="system" if admitted.mode == "command" else "assistant",
                error=f"chat turn crashed: {exc}",
                now=self._now(),
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


def answer_pending_clarification(
    lifecycle: ChatTurnLifecycle,
    entity_id: str,
    *,
    request_id: str,
    answer: str,
) -> ChatTurn:
    return lifecycle._answer_pending_clarification(
        entity_id,
        request_id=request_id,
        answer=answer,
    )


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
    if etype == "clarify.request":
        request_id = payload.get("request_id")
        question = payload.get("question")
        choices_raw = payload.get("choices")
        if not isinstance(request_id, str) or not request_id.strip():
            return
        if not isinstance(question, str) or not question.strip():
            return
        choices: tuple[str, ...] = ()
        if isinstance(choices_raw, list):
            choices = tuple(item for item in choices_raw if isinstance(item, str))
        chat_data.record_pending_clarification(
            conn,
            turn_id,
            entity_id=entity_id,
            clarification=ChatPendingClarification(
                request_id=request_id,
                question=question,
                choices=choices,
            ),
            now=now,
        )
        return
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
