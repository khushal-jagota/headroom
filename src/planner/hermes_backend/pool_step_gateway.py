"""PoolStepGateway — submit an employee step through the ticket's pool child and settle
by OWNING the terminal from the `prompt.submit` ACK disposition (S3 §1; Collision #A, A1).

It presents the EXACT interface `EmployeeStepRunner` calls (`run_ticket_step`/`interrupt`/
`status`), TYPE-importing `RunResult`/`OnEvent`/`SharedGatewayBusy` from `minds/` — no
`minds/` edit. Settlement uses only what stock Hermes already returns:

- `prompt.submit` returns an ACK whose `status` is the DISPOSITION
  (streaming/queued/steered) — sessions/service.py:556.
- `steered` → errored immediately, NO terminal watched (mirrors shared_gateway.py:852-861).
- `streaming` → this submission owns the CURRENT execution: own the next terminal.
- `queued` → own only the execution AFTER the interrupted predecessor's terminal: SKIP one
  terminal, own the next (the exact hole in the naive design; A1 closes it).
- `message.complete{status:complete|interrupted}` → RunResult complete/interrupted; an
  `error` event → errored; child death → errored "child reset" (shared_gateway.py:874-898).
- The runner thread blocks on a settlement Event, drains streamed frames, and calls
  `on_event` ITSELF (never the stdout thread — Codex Finding 2) in the {type,session_id,
  payload} shape `observe_worker_gateway_event` consumes (shared_gateway.py:868-873).
- No whole-step timeout: settle on a lifecycle terminal or child death only (Finding 9).
"""

from __future__ import annotations

from collections.abc import Callable

from planner.chat.contracts import GatewayStatus
from planner.hermes_backend.employee_child_pool import (
    EmployeeChildPool,
    TurnSubmission,
)
from planner.hermes_backend.raw_frame_transport import RawFrameTransportError
from planner.minds.contracts import OnEvent, RunResult
from planner.minds.gateway import JsonDict
from planner.minds.shared_gateway import BUSY_CODE, SharedGatewayBusy

_SETTLE_POLL_SECONDS = 0.05


class PoolStepGateway:
    """The step transport injected into the EmployeeStepRunner when the relay flag is on."""

    def __init__(self, *, pool: EmployeeChildPool) -> None:
        self._pool = pool

    def status(self) -> GatewayStatus:
        # The pool is composed only when the relay backend is available; the runner reads
        # only `.available`.
        return GatewayStatus(available=True)

    def run_ticket_step(
        self,
        session_key: str | None,
        entity_id: str,
        prompt_text: str,
        on_event: OnEvent | None = None,
        on_session_key: Callable[[str], None] | None = None,
        *,
        require_existing_session: bool = False,
    ) -> RunResult:
        try:
            record = self._pool.child_for_employee(entity_id)
        except RawFrameTransportError as exc:
            return RunResult("errored", "", None, session_key, str(exc))
        except Exception as exc:  # noqa: BLE001 — a spawn/adoption failure surfaces as errored
            return RunResult("errored", "", None, session_key, str(exc))

        resolved_key = record.stored_session_id
        if on_session_key is not None:
            on_session_key(resolved_key)

        live_session_id = self._pool.live_session_id_for(entity_id)
        if not live_session_id:
            return RunResult(
                "errored", "", None, resolved_key, "no live session for the pool child"
            )

        # Register the submission BEFORE submit so it can observe its own ACK frame and arm
        # itself IN emission order on the stdout thread (race-free; the pool owns the arming).
        submission = self._pool.register_turn_submission(entity_id)
        try:
            try:
                disposition = self._pool.submit_step_prompt(
                    record, live_session_id, prompt_text, submission
                )
            except RawFrameTransportError as exc:
                if _is_busy(exc):
                    raise SharedGatewayBusy(session_key=resolved_key) from exc
                return RunResult("errored", "", None, resolved_key, str(exc))

            if disposition == "steered":
                # Delivered by steering; no independent execution to watch (mirrors
                # shared_gateway.py:852-861). The submission also self-settles "steered" on the
                # ACK, but we short-circuit here without waiting on any terminal.
                return RunResult(
                    "errored",
                    "",
                    None,
                    resolved_key,
                    "Hermes delivered the employee prompt by steering the active execution; "
                    "no independent employee execution was created",
                )

            # The submission is already armed (by observing its ACK); settle when our terminal
            # arrives, draining our owned frames to on_event on THIS (runner) thread.
            return self._settle(submission, on_event, resolved_key)
        finally:
            self._pool.unregister_turn_submission(entity_id, submission)

    def _settle(
        self, submission: TurnSubmission, on_event: OnEvent | None, resolved_key: str
    ) -> RunResult:
        """Block the runner thread until a lifecycle terminal or child death; drain streamed
        frames and call `on_event` HERE (the runner thread that owns the SQLite connection)."""
        while True:
            self._drain_frames(submission, on_event)
            if submission.settled.wait(_SETTLE_POLL_SECONDS):
                # One final drain so any frame enqueued alongside the terminal is delivered.
                self._drain_frames(submission, on_event)
                break

        kind = submission.terminal_kind
        payload = submission.terminal_payload
        if kind == "dead":
            return RunResult("errored", "", None, resolved_key, "child reset during step")
        if kind == "error":
            message = str(payload.get("message") or "gateway error event")
            return RunResult("errored", "", None, resolved_key, message)
        # message.complete
        text_out = str(payload.get("text") or "")
        usage_raw = payload.get("usage")
        usage = usage_raw if isinstance(usage_raw, dict) else None
        status = str(payload.get("status") or "complete")
        if status == "complete":
            return RunResult("complete", text_out, usage, resolved_key, None)
        if status == "interrupted":
            return RunResult("interrupted", text_out, usage, resolved_key, None)
        return RunResult(
            "errored",
            text_out,
            usage,
            resolved_key,
            text_out or "run ended with status=error",
        )

    def _drain_frames(self, submission: TurnSubmission, on_event: OnEvent | None) -> None:
        while True:
            try:
                frame = submission.frames.get_nowait()
            except Exception:  # queue.Empty
                return
            if on_event is not None:
                shaped = _shape_event(frame)
                if shaped is not None:
                    on_event(shaped)

    def interrupt(
        self,
        session_key: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        # The runner passes the stored session_key; the pool resolves the LIVE id itself
        # (the correct interrupt target). A no-op when no live id is known (§1.6).
        if deadline is None:
            # The runner always passes an absolute deadline at shutdown; without one there is
            # no bounded interrupt to issue.
            return
        self._pool.interrupt_live_turn(entity_id, deadline=deadline)


def _is_busy(exc: RawFrameTransportError) -> bool:
    """The pool surfaces a child RPC error as a message string carrying the native code
    (employee_child_pool.py: `relay child rpc error for prompt.submit: <code> <message>`).
    A busy submit is BUSY_CODE 4009 (shared_gateway.py:836-837)."""
    return str(BUSY_CODE) in str(exc)


def _shape_event(frame: JsonDict) -> JsonDict | None:
    """Reshape a native event frame {method:event, params:{type,session_id,payload}} into the
    {type,session_id,payload} shape `observe_worker_gateway_event` consumes
    (shared_gateway.py:868-873). Native event frame shape: fake.ev / service.py:948-954."""
    params = frame.get("params")
    if not isinstance(params, dict):
        return None
    event_type = params.get("type")
    if not isinstance(event_type, str):
        return None
    payload = params.get("payload")
    return {
        "type": event_type,
        "session_id": params.get("session_id"),
        "payload": payload if isinstance(payload, dict) else {},
    }
