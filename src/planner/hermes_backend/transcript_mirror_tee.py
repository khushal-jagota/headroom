"""TranscriptMirrorTee: the first product consumer of the S1 tee seam (plan §5).

Implements the `RelayTeeObserver` protocol. It reconstructs settled relay-handled turns
from both frame directions and mirrors their text into the existing Panels chat transcript
store, write-behind and off the conversation path:

- `observe` NEVER touches SQLite. It updates a tiny in-memory per-employee turn accumulator
  and, on a settled turn (completed OR failed), enqueues one record onto a bounded work
  queue and returns. A broken/slow store can never perturb the live stream (which runs on
  `conn.outbound`, a different object this tee never touches).
- One background worker thread drains the queue. It OPENS ITS OWN `sqlite3` connection
  against the DB path, owns it for the worker's lifetime, and CLOSES it on shutdown — the
  connection is created, used, and closed all on the worker thread (the repo's connection
  factory uses default SQLite thread affinity, `db.py`, so a cross-thread connection would
  raise `ProgrammingError`). Any exception is caught and logged, NEVER re-raised.

Scope = completed/failed turns' user text + final assistant text, via the call-only
`planner.chat.data.record_message`. No durable row marker (amended contract): distinguishing
mirrored rows is S4's problem.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planner.chat import data as chat_data
from planner.core.db import connect
from planner.hermes_backend import hermes_frame_translation as tr
from planner.hermes_backend.neutral_relay_config import TEE_WORK_QUEUE_MAX_RECORDS
from planner.hermes_backend.relay_tee import RelayFrameDirection

_LOG = logging.getLogger("planner.hermes_backend.transcript_mirror_tee")

# Chat message roles the mirror writes (chat_messages CHECK allows human/assistant/...).
_ROLE_HUMAN = "human"
_ROLE_ASSISTANT = "assistant"


@dataclass
class _SettledTurnRecord:
    """One settled turn to mirror: the user text and the final assistant text (if any)."""

    employee_entity_id: str
    user_text: str
    assistant_text: str | None


@dataclass
class _TurnAccumulator:
    """Per-employee in-flight turn state, mutated only on the observe (relay) thread.

    `pending_prompt` is the CONSUMABLE reconstruction of the user's next-turn text: successive
    mid-turn `prompt.submit`s merge into it with Hermes's `\\n\\n` rule (server.py:5047), and it
    is captured + cleared when a turn opens. `None` means no relay prompt is pending — so an
    autonomous `message.start` (a step, no downstream prompt) opens a turn with NO human row
    instead of reusing stale text (defect #4)."""

    pending_prompt: str | None = None
    open: bool = False
    turn_user_text: str | None = None


class TranscriptMirrorTee:
    def __init__(
        self,
        *,
        db_path: str,
        now: Callable[[], int],
        connect_fn: Callable[[str], Any] = connect,
    ) -> None:
        self._db_path = db_path
        self._now = now
        self._connect_fn = connect_fn
        self._accumulators: dict[str, _TurnAccumulator] = {}
        self._work: queue.Queue[_SettledTurnRecord | None] = queue.Queue(
            maxsize=TEE_WORK_QUEUE_MAX_RECORDS
        )
        self._worker = threading.Thread(
            target=self._worker_loop, name="transcript-mirror-tee", daemon=True
        )
        self._started = False
        self._start_lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
            self._worker.start()

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Signal the worker to drain and exit; it closes its own connection. Idempotent."""
        with self._start_lock:
            if not self._started:
                return
        self._work.put(None)
        self._worker.join(timeout=timeout)

    def wait_idle(self, *, timeout: float = 5.0) -> bool:
        """Block until the worker has drained every currently-queued record (test barrier —
        never a sleep). Returns False on timeout."""
        self._work.join()
        return True

    # --- the tee seam ------------------------------------------------------

    def observe(
        self,
        *,
        employee_entity_id: str,
        direction: RelayFrameDirection,
        frame: dict[str, Any],
    ) -> None:
        """Enqueue-only. Never raises (a broken accumulator/queue must not kill a reader)."""
        try:
            self._observe(employee_entity_id, direction, frame)
        except Exception:  # pragma: no cover - defensive; observe must never raise
            _LOG.exception("transcript mirror observe failed")

    def _observe(
        self, employee_entity_id: str, direction: RelayFrameDirection, frame: dict[str, Any]
    ) -> None:
        acc = self._accumulators.setdefault(employee_entity_id, _TurnAccumulator())
        if direction == RelayFrameDirection.FROM_DOWNSTREAM_TO_CHILD:
            if frame.get("method") == tr.NATIVE_PROMPT_SUBMIT:
                params = frame.get("params")
                text = params.get("text") if isinstance(params, dict) else None
                acc.pending_prompt = _merge_prompt(
                    acc.pending_prompt, str(text) if isinstance(text, str) else ""
                )
            return
        # FROM_CHILD_TO_DOWNSTREAM
        native_type = _native_event_type(frame)
        if native_type == tr.NATIVE_MESSAGE_START:
            acc.open = True
            # Capture + CONSUME the pending prompt. An autonomous message.start with no
            # pending relay prompt opens the turn with no human text (turn_user_text stays
            # None) — never a duplicate stale row.
            acc.turn_user_text = acc.pending_prompt
            acc.pending_prompt = None
            return
        if native_type == tr.NATIVE_MESSAGE_COMPLETE:
            self._settle(employee_entity_id, acc, assistant_text=_payload_text(frame))
            return
        if native_type == tr.NATIVE_ERROR:
            # A bare error frame closes the turn; no final assistant text exists.
            self._settle(employee_entity_id, acc, assistant_text=None)
            return

    def _settle(
        self, employee_entity_id: str, acc: _TurnAccumulator, *, assistant_text: str | None
    ) -> None:
        if not acc.open:
            return
        record = _SettledTurnRecord(
            employee_entity_id=employee_entity_id,
            user_text=acc.turn_user_text or "",
            assistant_text=assistant_text if assistant_text else None,
        )
        acc.open = False
        acc.turn_user_text = None
        self._enqueue(record)

    def _enqueue(self, record: _SettledTurnRecord) -> None:
        try:
            self._work.put_nowait(record)
        except queue.Full:
            _LOG.warning("transcript mirror work queue full; dropping settled-turn record")

    # --- worker thread -----------------------------------------------------

    def _worker_loop(self) -> None:
        # Acquire the connection INSIDE the caught path: a failed OPEN must be logged and must
        # NOT kill the worker unlogged, else queued records never `task_done` and shutdown/
        # wait_idle block once the queue fills (defect #5). On open failure `conn` stays None;
        # the loop still drains every record (task_done) so the barrier always releases.
        conn: Any | None = None
        try:
            conn = self._connect_fn(self._db_path)
        except Exception:
            _LOG.exception("transcript mirror connection open failed")
        try:
            while True:
                item = self._work.get()
                try:
                    if item is None:
                        return
                    if conn is not None:
                        self._write_record(conn, item)
                except Exception:
                    _LOG.exception("transcript mirror write failed")
                finally:
                    self._work.task_done()
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - defensive close
                    _LOG.exception("transcript mirror connection close failed")

    def _write_record(self, conn: Any, record: _SettledTurnRecord) -> None:
        now = self._now()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if record.user_text:
                chat_data.record_message(
                    conn,
                    record.employee_entity_id,
                    role=_ROLE_HUMAN,
                    text=record.user_text,
                    now=now,
                )
            if record.assistant_text:
                chat_data.record_message(
                    conn,
                    record.employee_entity_id,
                    role=_ROLE_ASSISTANT,
                    text=record.assistant_text,
                    now=now,
                )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _merge_prompt(existing: str | None, new: str) -> str:
    """Merge a queued mid-turn prompt into the pending one, mirroring Hermes's lossless
    consecutive-user merge (`previous + "\\n\\n" + new` when both non-empty, server.py:5047)."""
    if existing is None:
        return new
    if existing and new:
        return f"{existing}\n\n{new}"
    return existing or new


def _native_event_type(frame: dict[str, Any]) -> str | None:
    params = frame.get("params")
    if isinstance(params, dict) and frame.get("method") == "event":
        native_type = params.get("type")
        return str(native_type) if isinstance(native_type, str) else None
    return None


def _payload_text(frame: dict[str, Any]) -> str | None:
    params = frame.get("params")
    if not isinstance(params, dict):
        return None
    payload = params.get("payload")
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    return str(text) if isinstance(text, str) else None
