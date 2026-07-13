"""One faithful ordered ingress for every live session in a Hermes child."""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic as _monotonic
from typing import cast

from planner.minds.contracts import (
    HermesObservation,
    InterruptReceipt,
    SubmissionReceipt,
    SubmitDisposition,
    TransportUnknown,
)
from planner.minds.gateway import (
    GatewayChild,
    GatewayError,
    GatewayRequestHandle,
    GatewayRpcError,
    JsonDict,
)


class LiveSessionDormant(GatewayError):
    """A previously returned live-session lease detached before write admission."""


@dataclass
class _ConsequenceState:
    observations: queue.Queue[HermesObservation | None] = field(default_factory=queue.Queue)
    pre_accept_active_observations: list[HermesObservation] = field(default_factory=list)
    pre_accept_has_registered_predecessor: bool = False
    disposition: SubmitDisposition | None = None
    assigned: bool = False
    released: bool = False
    closed: bool = False
    unknown: bool = False
    counts_as_pending: bool = True
    request_handle: GatewayRequestHandle | None = None
    queued_start_boundary_open: bool = False


@dataclass
class _SessionState:
    stored_session_key: str
    live_session_id: str
    snapshot: JsonDict
    command_lock: threading.Lock = field(default_factory=threading.Lock)
    prompt_admission_lock: threading.Lock = field(default_factory=threading.Lock)
    observed_running: bool | None = None
    routed_pending_consequences: int = 0
    routed_unknown_consequences: int = 0
    waiting_consequences: deque[_ConsequenceState] = field(default_factory=deque)
    active_consequences: list[_ConsequenceState] = field(default_factory=list)
    unknown_lifecycle_barrier: bool = False
    unknown_barrier_terminal_observed: bool = False
    admitted_operations: int = 0
    offline: bool = False
    dormant: bool = False


class SubmissionConsequence:
    """Observations belonging to one Hermes-accepted prompt submission."""

    def __init__(
        self,
        manager: LiveSessionManager,
        session_state: _SessionState,
        consequence_state: _ConsequenceState,
    ) -> None:
        self._manager = manager
        self._session_state = session_state
        self._consequence_state = consequence_state

    def next_observation(self, timeout: float | None = None) -> HermesObservation:
        try:
            item = (
                self._consequence_state.observations.get(timeout=timeout)
                if timeout is not None
                else self._consequence_state.observations.get()
            )
        except queue.Empty:
            raise GatewayError(
                f"no Hermes observation for accepted submission within {timeout}s"
            ) from None
        if item is None:
            raise GatewayError("accepted submission observation stream is closed")
        return item

    def release(self) -> None:
        self._manager._release_consequence(
            self._session_state,
            self._consequence_state,
        )


@dataclass(frozen=True)
class AcceptedSubmission:
    receipt: SubmissionReceipt
    consequence: SubmissionConsequence


class PendingSubmission:
    """A prompt write admitted under the session operation lane, awaiting its receipt."""

    def __init__(
        self,
        manager: LiveSessionManager,
        session_state: _SessionState,
        consequence_state: _ConsequenceState,
        handle: GatewayRequestHandle,
        image_paths: tuple[Path, ...] = (),
    ) -> None:
        self._manager = manager
        self._session_state = session_state
        self._consequence_state = consequence_state
        self._handle = handle
        self._image_paths = image_paths

    def wait(self, timeout: float) -> AcceptedSubmission | TransportUnknown:
        return self._manager._await_consequence_submission(
            self._session_state,
            self._consequence_state,
            self._handle,
            timeout,
            image_paths=self._image_paths,
        )


class LiveSessionOperation:
    """One same-session write lane held across a compound Hermes operation."""

    def __init__(self, manager: LiveSessionManager, state: _SessionState) -> None:
        self._manager = manager
        self._state = state
        self._entered = False

    def __enter__(self) -> LiveSessionOperation:
        self._manager._admit_operation(self._state)
        self._state.command_lock.acquire()
        try:
            with self._manager._lock:
                self._manager._require_live(self._state)
        except BaseException:
            self._state.command_lock.release()
            self._manager._release_operation_admission(self._state)
            raise
        self._entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._entered:
            self._entered = False
            self._state.command_lock.release()
            self._manager._release_operation_admission(self._state)

    def request(
        self,
        method: str,
        params: JsonDict | None = None,
        *,
        timeout: float,
    ) -> JsonDict:
        if not self._entered:
            raise RuntimeError("live session operation is not entered")
        return self._manager._request_while_locked(self._state, method, params or {}, timeout)

    def begin_submission(self, text: str) -> PendingSubmission | TransportUnknown:
        if not self._entered:
            raise RuntimeError("live session operation is not entered")
        self._manager._acquire_prompt_admission_during_operation(self._state)
        try:
            return self._manager._begin_consequence_submission_while_locked(
                self._state,
                text,
            )
        finally:
            self._state.prompt_admission_lock.release()


class LiveSession:
    """A lightweight ordered observation lane for one live Hermes session."""

    def __init__(self, manager: LiveSessionManager, state: _SessionState) -> None:
        self._manager = manager
        self._state = state

    @property
    def stored_session_key(self) -> str:
        return self._state.stored_session_key

    @property
    def live_session_id(self) -> str:
        return self._state.live_session_id

    @property
    def snapshot(self) -> JsonDict:
        return self._state.snapshot

    @property
    def pending_consequence_count(self) -> int:
        with self._manager._lock:
            return (
                self._state.routed_pending_consequences
                + self._state.routed_unknown_consequences
            )

    @property
    def active(self) -> bool:
        with self._manager._lock:
            if (
                self._manager._closing
                or self._state.offline
                or not self._manager._child.alive
            ):
                return False
            return bool(
                self._state.observed_running
                or self._state.routed_pending_consequences
                or self._state.routed_unknown_consequences
            )

    @property
    def offline(self) -> bool:
        with self._manager._lock:
            return (
                self._manager._closing
                or self._state.offline
                or not self._manager._child.alive
            )

    def submit_consequence(
        self,
        text: str,
        *,
        timeout: float,
        image_paths: tuple[Path, ...] = (),
    ) -> AcceptedSubmission | TransportUnknown:
        return self._manager._submit_consequence(
            self._state,
            text,
            timeout,
            image_paths=image_paths,
        )

    def request(
        self,
        method: str,
        params: JsonDict | None = None,
        *,
        timeout: float,
    ) -> JsonDict:
        return self._manager._request(self._state, method, params, timeout)

    def ordered_operation(self) -> LiveSessionOperation:
        return LiveSessionOperation(self._manager, self._state)

    def interrupt(self, *, timeout: float) -> InterruptReceipt | TransportUnknown:
        return self._manager._interrupt(self._state, timeout)

    def detach_if_dormant(self) -> bool:
        """Detach lightweight state only after observed idle and no local consequence."""
        return self._manager._detach_if_dormant(self._state)


class LiveSessionManager:
    """Own the single child feed and demultiplex it into logical session lanes."""

    def __init__(self, child: GatewayChild) -> None:
        self._child = child
        self._ingress = child.claim_session_event_ingress()
        self._lock = threading.Lock()
        self._sessions_by_live_id: dict[str, _SessionState] = {}
        self._sessions_by_stored_key: dict[str, _SessionState] = {}
        self._unbound: dict[str, deque[HermesObservation]] = {}
        self._request_handles: set[GatewayRequestHandle] = set()
        self._next_sequence = 1
        self._closed = False
        self._closing = False
        self._shutdown_error: GatewayError | None = None
        self._shutdown_complete = threading.Event()
        self._router = threading.Thread(
            target=self._route_events,
            name="minds-session-ingress",
            daemon=True,
        )
        self._router.start()

    def bind(
        self,
        stored_session_key: str,
        live_session_id: str,
        *,
        snapshot: JsonDict | None = None,
    ) -> LiveSession:
        if not stored_session_key or not live_session_id:
            raise ValueError("stored_session_key and live_session_id are required")
        with self._lock:
            if self._closed or self._closing:
                raise GatewayError("live session manager is shut down")
            previous = self._sessions_by_stored_key.get(stored_session_key)
            if previous is not None and not previous.dormant:
                previous.snapshot = dict(snapshot or previous.snapshot)
                running = previous.snapshot.get("running")
                if isinstance(running, bool):
                    previous.observed_running = running
                if previous.live_session_id != live_session_id:
                    if self._sessions_by_live_id.get(previous.live_session_id) is previous:
                        self._sessions_by_live_id.pop(previous.live_session_id, None)
                    previous.live_session_id = live_session_id
                    self._sessions_by_live_id[live_session_id] = previous
                    buffered = self._unbound.pop(live_session_id, ())
                    for observation in buffered:
                        self._apply_lifecycle_observation(
                            previous,
                            observation.event_type,
                            observation.payload,
                        )
                return LiveSession(self, previous)
            state = _SessionState(
                stored_session_key=stored_session_key,
                live_session_id=live_session_id,
                snapshot=dict(snapshot or {}),
            )
            running = state.snapshot.get("running")
            if isinstance(running, bool):
                state.observed_running = running
            self._sessions_by_live_id[live_session_id] = state
            self._sessions_by_stored_key[stored_session_key] = state
            buffered = self._unbound.pop(live_session_id, ())
            for observation in buffered:
                self._apply_lifecycle_observation(
                    state,
                    observation.event_type,
                    observation.payload,
                )
        return LiveSession(self, state)

    def session(self, stored_session_key: str) -> LiveSession | None:
        with self._lock:
            state = self._sessions_by_stored_key.get(stored_session_key)
            if state is None or state.dormant:
                return None
            return LiveSession(self, state)

    def shutdown(self, *, deadline: float | None = None) -> None:
        with self._lock:
            if self._closing:
                wait_for_other_shutdown = True
                states: tuple[_SessionState, ...] = ()
            elif self._closed:
                shutdown_error = self._shutdown_error
                if shutdown_error is not None:
                    raise shutdown_error
                return
            else:
                self._closing = True
                wait_for_other_shutdown = False
                states = tuple(self._sessions_by_live_id.values())
                request_handles = tuple(self._request_handles)
        if wait_for_other_shutdown:
            timeout = None if deadline is None else max(0.0, deadline - _monotonic())
            completed = self._shutdown_complete.wait(timeout)
            if not completed:
                raise GatewayError(
                    "Hermes session manager shutdown did not complete before deadline"
                )
            with self._lock:
                shutdown_error = self._shutdown_error
            if shutdown_error is not None:
                raise shutdown_error
            return

        for handle in request_handles:
            handle.cancel("live session manager shut down with request outcome unknown")

        acquired_command_locks: list[threading.Lock] = []
        try:
            for state in sorted(states, key=lambda item: item.live_session_id):
                if deadline is None:
                    acquired = state.command_lock.acquire()
                else:
                    acquired = state.command_lock.acquire(
                        timeout=max(0.0, deadline - _monotonic())
                    )
                if not acquired:
                    raise GatewayError(
                        "Hermes session command lock was not acquired before shutdown deadline"
                    )
                acquired_command_locks.append(state.command_lock)
            with self._lock:
                self._closed = True
                for state in states:
                    state.offline = True
                    for consequence in (
                        *state.waiting_consequences,
                        *state.active_consequences,
                    ):
                        consequence.observations.put(None)
        except GatewayError as exc:
            with self._lock:
                self._closing = False
                self._shutdown_error = exc
            self._shutdown_complete.set()
            raise
        finally:
            for command_lock in reversed(acquired_command_locks):
                command_lock.release()

        router_shutdown_error: GatewayError | None = None
        try:
            self._ingress.close()
            router_timeout = 2.0 if deadline is None else max(0.0, deadline - _monotonic())
            self._router.join(timeout=router_timeout)
            if self._router.is_alive():
                router_shutdown_error = GatewayError(
                    f"Hermes session ingress router did not terminate within {router_timeout}s"
                )
        finally:
            with self._lock:
                self._closing = False
                self._shutdown_error = router_shutdown_error
            self._shutdown_complete.set()
        if router_shutdown_error is not None:
            raise router_shutdown_error

    def _submit_consequence(
        self,
        state: _SessionState,
        text: str,
        timeout: float,
        *,
        image_paths: tuple[Path, ...],
    ) -> AcceptedSubmission | TransportUnknown:
        self._admit_operation(state)
        prompt_admission_held = False
        operation_admission_held = True
        try:
            with state.command_lock:
                self._acquire_prompt_admission_during_operation(state)
                prompt_admission_held = True
                with self._lock:
                    self._require_live(state)
                attached_image_paths: list[Path] = []
                try:
                    for image_path in image_paths:
                        self._request_while_locked(
                            state,
                            "image.attach",
                            {"session_id": state.live_session_id, "path": str(image_path)},
                            timeout,
                        )
                        attached_image_paths.append(image_path)
                except GatewayError:
                    self._detach_image_paths_while_locked_best_effort(
                        state, tuple(attached_image_paths), timeout
                    )
                    raise
                pending = self._begin_consequence_submission_while_locked(
                    state,
                    text,
                    image_paths=tuple(attached_image_paths),
                )
            if not image_paths:
                state.prompt_admission_lock.release()
                prompt_admission_held = False
                self._release_operation_admission(state)
                operation_admission_held = False
            if isinstance(pending, TransportUnknown):
                return pending
            return pending.wait(timeout)
        finally:
            if prompt_admission_held:
                state.prompt_admission_lock.release()
            if operation_admission_held:
                self._release_operation_admission(state)

    def _begin_consequence_submission_while_locked(
        self,
        state: _SessionState,
        text: str,
        *,
        image_paths: tuple[Path, ...] = (),
    ) -> PendingSubmission | TransportUnknown:
        consequence_state = _ConsequenceState()
        transport_error: GatewayError | None
        with self._lock:
            self._require_live(state)
            consequence_state.queued_start_boundary_open = state.observed_running is not True
            state.waiting_consequences.append(consequence_state)
            state.routed_pending_consequences += 1
            try:
                handle = self._child.begin_request(
                    "prompt.submit",
                    {"session_id": state.live_session_id, "text": text},
                )
            except GatewayError as exc:
                self._mark_routed_submission_unknown_locked(state, consequence_state)
                transport_error = exc
            else:
                consequence_state.request_handle = handle
                self._request_handles.add(handle)
                transport_error = None
        if transport_error is not None:
            return TransportUnknown(
                method="prompt.submit",
                detail=str(transport_error),
                child_offline=not self._child.alive,
            )
        return PendingSubmission(
            self,
            state,
            consequence_state,
            handle,
            image_paths,
        )

    def _await_consequence_submission(
        self,
        state: _SessionState,
        consequence_state: _ConsequenceState,
        handle: GatewayRequestHandle,
        timeout: float,
        *,
        image_paths: tuple[Path, ...],
    ) -> AcceptedSubmission | TransportUnknown:
        try:
            try:
                result = handle.wait(timeout)
            finally:
                self._untrack_request_handle(handle)
        except GatewayRpcError:
            self._reject_consequence(state, consequence_state)
            self._detach_image_paths_best_effort(state, image_paths, timeout)
            raise
        except GatewayError as exc:
            self._mark_routed_submission_unknown(state, consequence_state)
            return TransportUnknown(
                method="prompt.submit",
                detail=str(exc),
                child_offline=not self._child.alive,
            )
        disposition = str(result.get("status") or "")
        if disposition not in ("streaming", "queued", "steered"):
            self._reject_consequence(state, consequence_state)
            self._detach_image_paths_best_effort(state, image_paths, timeout)
            raise GatewayError(f"unexpected prompt.submit disposition: {disposition!r}")
        typed_disposition = cast(SubmitDisposition, disposition)
        with self._lock:
            self._apply_submission_disposition_locked(
                state,
                consequence_state,
                typed_disposition,
            )
        receipt = SubmissionReceipt(typed_disposition)
        return AcceptedSubmission(
            receipt,
            SubmissionConsequence(self, state, consequence_state),
        )

    def _detach_image_paths(
        self,
        state: _SessionState,
        image_paths: tuple[Path, ...],
        timeout: float,
    ) -> None:
        for image_path in image_paths:
            self._request(
                state,
                "image.detach",
                {"session_id": state.live_session_id, "path": str(image_path)},
                timeout,
            )

    def _detach_image_paths_best_effort(
        self,
        state: _SessionState,
        image_paths: tuple[Path, ...],
        timeout: float,
    ) -> None:
        for image_path in image_paths:
            try:
                self._request(
                    state,
                    "image.detach",
                    {"session_id": state.live_session_id, "path": str(image_path)},
                    timeout,
                )
            except GatewayError:
                # Compensation is best-effort; preserve the original Hermes failure.
                pass

    def _detach_image_paths_while_locked_best_effort(
        self,
        state: _SessionState,
        image_paths: tuple[Path, ...],
        timeout: float,
    ) -> None:
        for image_path in image_paths:
            try:
                self._request_while_locked(
                    state,
                    "image.detach",
                    {"session_id": state.live_session_id, "path": str(image_path)},
                    timeout,
                )
            except GatewayError:
                # Compensation is best-effort; preserve the original Hermes failure.
                pass

    def _request(
        self,
        state: _SessionState,
        method: str,
        params: JsonDict | None,
        timeout: float,
    ) -> JsonDict:
        with state.command_lock:
            with self._lock:
                self._require_live(state)
            handle = self._child.begin_request(method, params)
            self._track_request_handle(handle)
        try:
            try:
                return handle.wait(timeout)
            finally:
                self._untrack_request_handle(handle)
        except GatewayError:
            raise

    def _request_while_locked(
        self,
        state: _SessionState,
        method: str,
        params: JsonDict,
        timeout: float,
    ) -> JsonDict:
        with self._lock:
            self._require_live(state)
            handle = self._child.begin_request(method, params)
            self._request_handles.add(handle)
        try:
            return handle.wait(timeout)
        finally:
            self._untrack_request_handle(handle)

    def _interrupt(
        self,
        state: _SessionState,
        timeout: float,
    ) -> InterruptReceipt | TransportUnknown:
        with state.command_lock:
            with self._lock:
                self._require_live(state)
            try:
                handle = self._child.begin_request(
                    "session.interrupt",
                    {"session_id": state.live_session_id},
                )
            except GatewayError as exc:
                return TransportUnknown(
                    method="session.interrupt",
                    detail=str(exc),
                    child_offline=not self._child.alive,
                )
            self._track_request_handle(handle)
        try:
            try:
                result = handle.wait(timeout)
            finally:
                self._untrack_request_handle(handle)
        except GatewayRpcError:
            raise
        except GatewayError as exc:
            return TransportUnknown(
                method="session.interrupt",
                detail=str(exc),
                child_offline=not self._child.alive,
            )
        return InterruptReceipt(result)

    def _track_request_handle(self, handle: GatewayRequestHandle) -> None:
        with self._lock:
            if self._closed or self._closing:
                cancel = True
            else:
                self._request_handles.add(handle)
                cancel = False
        if cancel:
            handle.cancel("live session manager shut down with request outcome unknown")

    def _untrack_request_handle(self, handle: GatewayRequestHandle) -> None:
        with self._lock:
            self._request_handles.discard(handle)

    def _mark_routed_submission_unknown(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        with self._lock:
            self._mark_routed_submission_unknown_locked(state, consequence)

    def _mark_routed_submission_unknown_locked(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        if consequence.unknown:
            return
        consequence.unknown = True
        consequence.released = True
        self._settle_routed_pending_locked(state, consequence)
        state.routed_unknown_consequences += 1
        state.unknown_lifecycle_barrier = True
        state.unknown_barrier_terminal_observed = False
        self._remove_waiting_consequence(state, consequence)
        if consequence in state.active_consequences:
            state.active_consequences.remove(consequence)
        consequence.pre_accept_active_observations.clear()
        consequence.pre_accept_has_registered_predecessor = False

    def _apply_submission_disposition_locked(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
        disposition: SubmitDisposition,
    ) -> None:
        if consequence.disposition is not None:
            return
        consequence.disposition = disposition
        if consequence.assigned or consequence.closed:
            return
        if disposition == "steered":
            state.unknown_lifecycle_barrier = False
            state.unknown_barrier_terminal_observed = False
            self._accept_consequence_with_buffered_lifecycle(state, consequence)
        elif disposition == "streaming":
            state.unknown_lifecycle_barrier = False
            state.unknown_barrier_terminal_observed = False
            if (
                consequence.pre_accept_active_observations
                and not consequence.pre_accept_has_registered_predecessor
            ):
                self._accept_consequence_with_buffered_lifecycle(state, consequence)
            else:
                consequence.pre_accept_active_observations.clear()
                consequence.pre_accept_has_registered_predecessor = False
        else:
            if state.unknown_barrier_terminal_observed:
                consequence.queued_start_boundary_open = True
                state.unknown_lifecycle_barrier = False
                state.unknown_barrier_terminal_observed = False
            elif any(
                observation.event_type in ("message.complete", "error")
                for observation in consequence.pre_accept_active_observations
            ):
                consequence.queued_start_boundary_open = True
            consequence.pre_accept_active_observations.clear()
            consequence.pre_accept_has_registered_predecessor = False

    def _reconcile_ready_submission_receipts_locked(self, state: _SessionState) -> None:
        for consequence in tuple(state.waiting_consequences):
            if consequence.disposition is not None or consequence.unknown:
                continue
            handle = consequence.request_handle
            if handle is None:
                continue
            rpc_error = handle.response_rpc_error_if_ready()
            if rpc_error is not None:
                self._reject_consequence_locked(state, consequence)
                continue
            result = handle.response_result_if_ready()
            if result is None:
                continue
            disposition = str(result.get("status") or "")
            if disposition in ("streaming", "queued", "steered"):
                self._apply_submission_disposition_locked(
                    state,
                    consequence,
                    cast(SubmitDisposition, disposition),
                )

    def _reject_consequence(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        with self._lock:
            self._reject_consequence_locked(state, consequence)

    def _reject_consequence_locked(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        if consequence.closed:
            return
        self._remove_waiting_consequence(state, consequence)
        if consequence in state.active_consequences:
            state.active_consequences.remove(consequence)
        self._settle_routed_pending_locked(state, consequence)
        consequence.closed = True
        consequence.observations.put(None)

    def _release_consequence(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        with self._lock:
            if consequence.released:
                return
            consequence.released = True
            if not consequence.closed:
                consequence.observations.put(None)
            else:
                self._settle_routed_pending_locked(state, consequence)
                self._detach_state_if_dormant_locked(state)

    @staticmethod
    def _settle_routed_pending_locked(
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        if not consequence.counts_as_pending:
            return
        consequence.counts_as_pending = False
        if state.routed_pending_consequences:
            state.routed_pending_consequences -= 1

    @staticmethod
    def _remove_waiting_consequence(
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        try:
            state.waiting_consequences.remove(consequence)
        except ValueError:
            pass

    def _accept_consequence_with_buffered_lifecycle(
        self,
        state: _SessionState,
        consequence: _ConsequenceState,
    ) -> None:
        buffered = tuple(consequence.pre_accept_active_observations)
        consequence.pre_accept_active_observations.clear()
        consequence.pre_accept_has_registered_predecessor = False
        self._remove_waiting_consequence(state, consequence)
        for observation in buffered:
            if not consequence.released:
                consequence.observations.put(observation)
        if buffered and buffered[-1].event_type in ("message.complete", "error"):
            consequence.closed = True
            if not consequence.released:
                consequence.observations.put(None)
            if consequence.released:
                self._settle_routed_pending_locked(state, consequence)
                self._detach_state_if_dormant_locked(state)
            return
        consequence.assigned = True
        state.active_consequences.append(consequence)

    def _detach_if_dormant(self, state: _SessionState) -> bool:
        with self._lock:
            self._require_live(state)
            return self._detach_state_if_dormant_locked(state)

    def _detach_state_if_dormant_locked(self, state: _SessionState) -> bool:
        if (
            state.observed_running is not False
            or state.routed_pending_consequences
            or state.routed_unknown_consequences
            or state.admitted_operations
        ):
            return False
        state.dormant = True
        if self._sessions_by_live_id.get(state.live_session_id) is state:
            self._sessions_by_live_id.pop(state.live_session_id, None)
        if self._sessions_by_stored_key.get(state.stored_session_key) is state:
            self._sessions_by_stored_key.pop(state.stored_session_key, None)
        return True

    def _admit_operation(self, state: _SessionState) -> None:
        with self._lock:
            self._require_live(state)
            state.admitted_operations += 1

    def _release_operation_admission(self, state: _SessionState) -> None:
        with self._lock:
            if state.admitted_operations:
                state.admitted_operations -= 1
            self._detach_state_if_dormant_locked(state)

    def _acquire_prompt_admission_during_operation(
        self,
        state: _SessionState,
    ) -> None:
        if state.prompt_admission_lock.acquire(blocking=False):
            return
        state.command_lock.release()
        try:
            state.prompt_admission_lock.acquire()
        finally:
            state.command_lock.acquire()
        try:
            with self._lock:
                self._require_live(state)
        except BaseException:
            state.prompt_admission_lock.release()
            raise

    @property
    def router_alive(self) -> bool:
        return self._router.is_alive()

    def _route_events(self) -> None:
        while True:
            event = self._ingress.next_event()
            if event is None:
                self._mark_offline()
                return
            raw_live_session_id = event.get("session_id")
            if raw_live_session_id is None:
                continue
            live_session_id = str(raw_live_session_id)
            event_type = str(event.get("type") or "")
            raw_payload = event.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            with self._lock:
                observation = HermesObservation(
                    sequence=self._next_sequence,
                    live_session_id=live_session_id,
                    event_type=event_type,
                    payload=payload,
                )
                self._next_sequence += 1
                state = self._sessions_by_live_id.get(live_session_id)
                if state is None:
                    self._unbound.setdefault(live_session_id, deque()).append(observation)
                else:
                    was_running = state.observed_running is True
                    self._reconcile_ready_submission_receipts_locked(state)
                    routed_terminal = self._route_consequence_observation(
                        state,
                        observation,
                        was_running=was_running,
                    )
                    self._apply_lifecycle_observation(state, event_type, payload)
                    if routed_terminal:
                        self._detach_state_if_dormant_locked(state)

    @staticmethod
    def _activate_next_consequence(state: _SessionState) -> None:
        while state.waiting_consequences:
            consequence = state.waiting_consequences.popleft()
            if consequence.closed:
                continue
            consequence.assigned = True
            state.active_consequences.append(consequence)
            if consequence.disposition == "queued":
                while (
                    state.waiting_consequences
                    and state.waiting_consequences[0].disposition == "queued"
                ):
                    merged = state.waiting_consequences.popleft()
                    if merged.closed:
                        continue
                    merged.assigned = True
                    state.active_consequences.append(merged)
            return

    @staticmethod
    def _waiting_consequence_can_start(
        state: _SessionState,
        *,
        was_running: bool,
    ) -> bool:
        for consequence in state.waiting_consequences:
            if consequence.closed:
                continue
            if consequence.disposition == "streaming":
                return True
            if consequence.disposition == "queued":
                return consequence.queued_start_boundary_open
            return not was_running
        return False

    def _route_consequence_observation(
        self,
        state: _SessionState,
        observation: HermesObservation,
        *,
        was_running: bool,
    ) -> bool:
        event_type = observation.event_type
        lifecycle_activity = event_type in (
            "reasoning.delta",
            "thinking.delta",
            "message.start",
            "message.delta",
            "tool.start",
            "tool.delta",
            "tool.end",
            "tool.complete",
            "command.start",
        )
        terminal = event_type in ("message.complete", "error")
        if state.unknown_lifecycle_barrier and not state.active_consequences:
            pending_receipt = False
            for waiting in state.waiting_consequences:
                if waiting.disposition is None and not waiting.assigned:
                    waiting.pre_accept_active_observations.append(observation)
                    pending_receipt = True
            if terminal:
                for waiting in state.waiting_consequences:
                    if waiting.disposition == "queued" and not waiting.assigned:
                        waiting.queued_start_boundary_open = True
                state.unknown_barrier_terminal_observed = True
                if not pending_receipt:
                    state.unknown_lifecycle_barrier = False
                    state.unknown_barrier_terminal_observed = False
            return False
        if (
            lifecycle_activity
            and not state.active_consequences
            and self._waiting_consequence_can_start(state, was_running=was_running)
        ):
            self._activate_next_consequence(state)
        elif (
            terminal
            and not state.active_consequences
            and self._waiting_consequence_can_start(state, was_running=was_running)
        ):
            # Hermes may emit an owned completion without a visible start.
            self._activate_next_consequence(state)

        targets = tuple(state.active_consequences)
        if targets or was_running:
            for waiting in state.waiting_consequences:
                if waiting.disposition is None and not waiting.assigned:
                    waiting.pre_accept_active_observations.append(observation)
                    if targets:
                        waiting.pre_accept_has_registered_predecessor = True
        for consequence in targets:
            if not consequence.released:
                consequence.observations.put(observation)

        if not terminal:
            return False
        for waiting in state.waiting_consequences:
            if waiting.disposition == "queued" and not waiting.assigned:
                waiting.queued_start_boundary_open = True
        for consequence in targets:
            consequence.closed = True
            if not consequence.released:
                consequence.observations.put(None)
            if consequence.unknown:
                # The event is quarantined from later accepted work, but cannot prove
                # the timed-out write's delivery outcome.
                continue
            if consequence.released:
                self._settle_routed_pending_locked(state, consequence)
        state.active_consequences.clear()
        return bool(targets)

    def _mark_offline(self) -> None:
        with self._lock:
            states = tuple(self._sessions_by_live_id.values())
            for state in states:
                if not state.offline:
                    state.offline = True
                    for consequence in (
                        *state.waiting_consequences,
                        *state.active_consequences,
                    ):
                        consequence.observations.put(None)

    def _require_live(self, state: _SessionState) -> None:
        if self._closed or self._closing or state.offline:
            raise GatewayError("Hermes child is offline")
        if state.dormant:
            raise LiveSessionDormant("Hermes session ingress is dormant")

    @staticmethod
    def _apply_lifecycle_observation(
        state: _SessionState,
        event_type: str,
        payload: JsonDict,
    ) -> None:
        if event_type == "session.info":
            running = payload.get("running")
            if isinstance(running, bool):
                state.observed_running = running
            return
        if event_type in ("message.start", "tool.start", "command.start"):
            state.observed_running = True
            return
        if event_type == "message.complete":
            state.observed_running = False
