"""Shared worker-role Hermes gateway child.

One process hosts many live Hermes sessions. Request responses are demuxed by
JSON-RPC id in GatewayChild; one session manager routes ordered observations to
the accepted operation that owns each consequence.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

from planner.chat.contracts import (
    CommandCatalog,
    CommandCategory,
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
    HumanChatOutputDelta,
)
from planner.chat.logic.activity import normalize_gateway_activity
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.errors import ErrorCode, PlannerError
from planner.minds.config import hermes_src_root
from planner.minds.contracts import OnEvent, RunResult, TransportUnknown
from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    JsonDict,
    SpawnFn,
    spawn_popen,
)
from planner.minds.sessions import (
    AcceptedSubmission,
    LiveSession,
    LiveSessionDormant,
    LiveSessionManager,
    PendingSubmission,
)
from planner.tickets.contracts import EmployeeSessionHistory, EmployeeSessionHistoryMessage
from planner.worker_context.contracts import PreparedWorkerPrompt, WorkerContextService
from planner.worker_context.service import EmptyWorkerContextService

SESSION_COLS = 100
SESSION_SOURCE = "planner"
CHAT_SOURCE = "planner-chat"
BUSY_CODE = 4009
NOT_FOUND_CODE = 4007
LIVE_SESSION_NOT_FOUND_CODE = 4001


class SharedGatewayBusy(Exception):
    """Hermes rejected a prompt or command because this session is already running."""

    def __init__(self, session_key: str | None = None) -> None:
        super().__init__("session busy")
        self.session_key = session_key


class EntityRoutingGateway:
    """Route chat calls for named top-level entities to their own gateway child."""

    def __init__(
        self,
        default_gateway: SharedGateway,
        entity_gateways: Mapping[str, SharedGateway],
    ) -> None:
        self._default_gateway = default_gateway
        self._entity_gateways = dict(entity_gateways)

    def _gateway_for(self, entity_id: str) -> SharedGateway:
        return self._entity_gateways.get(entity_id, self._default_gateway)

    def status(self) -> GatewayStatus:
        return self._default_gateway.status()

    def status_for_entity(self, entity_id: str) -> GatewayStatus:
        return self._gateway_for(entity_id).status()

    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory:
        return self._gateway_for(ticket_id).read_employee_session_history(
            employee_session_id,
            ticket_id,
        )

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]:
        gateway = self._gateway_for(entity_id)
        if not image_paths:
            yield from gateway.run_human_turn(
                session_key,
                entity_id,
                text,
                mode,
                bind_session_key,
                require_existing_session=require_existing_session,
            )
        else:
            yield from gateway.run_human_turn(
                session_key,
                entity_id,
                text,
                mode,
                bind_session_key,
                image_paths,
                require_existing_session=require_existing_session,
            )

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self._gateway_for(entity_id).interrupt(session_key, entity_id)

    def respond_to_clarification(
        self, session_key: str, entity_id: str, request_id: str, answer: str
    ) -> None:
        self._gateway_for(entity_id).respond_to_clarification(
            session_key,
            entity_id,
            request_id,
            answer,
        )

    def catalog(self) -> CommandCatalog:
        return self._default_gateway.catalog()

    def shutdown(self, *, deadline: float | None = None) -> None:
        seen: set[int] = set()
        for gateway in (self._default_gateway, *self._entity_gateways.values()):
            ident = id(gateway)
            if ident in seen:
                continue
            seen.add(ident)
            gateway.shutdown(deadline=deadline)


class SharedGateway:
    """Lifecycle owner for one role-configured shared Hermes gateway child."""

    def __init__(
        self,
        *,
        hermes_python: str | Path,
        home: str | Path,
        worker_role: str,
        spawn: SpawnFn = spawn_popen,
        base_env: Mapping[str, str] | None = None,
        ready_timeout: float = READY_TIMEOUT_DEFAULT,
        request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
        worker_context: WorkerContextService | None = None,
    ) -> None:
        self._python = Path(hermes_python).expanduser()
        self._home = Path(home).expanduser()
        self._worker_role = worker_role
        self._spawn = spawn
        self._base_env = dict(base_env if base_env is not None else os.environ)
        self._ready_timeout = ready_timeout
        self._request_timeout = request_timeout
        self._worker_context = worker_context or EmptyWorkerContextService()
        self._lock = threading.Lock()
        self._child: GatewayChild | None = None
        self._session_manager: LiveSessionManager | None = None
        self._live_session_ids_by_stored_key: dict[str, str] = {}

    def start(self) -> None:
        self._child_or_spawn()

    def shutdown(self, *, deadline: float | None = None) -> None:
        with self._lock:
            child = self._child
            session_manager = self._session_manager
            self._child = None
            self._session_manager = None
            self._live_session_ids_by_stored_key.clear()
        try:
            if session_manager is not None:
                session_manager.shutdown(deadline=deadline)
        finally:
            if child is not None:
                if deadline is None:
                    child.shutdown()
                else:
                    child.shutdown(grace=max(0.0, deadline - _monotonic()))

    def live_session(self, session_key: str) -> LiveSession | None:
        """Return the gateway-owned ingress for a currently live stored session."""
        with self._lock:
            manager = self._session_manager
        return manager.session(session_key) if manager is not None else None

    def status(self) -> GatewayStatus:
        if not self._python.exists():
            return GatewayStatus(
                available=False, detail=f"hermes interpreter not found: {self._python}"
            )
        child = self._child
        return GatewayStatus(available=child is None or child.alive)

    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory:
        try:
            child = self._child_or_spawn()
            resumed = child.request(
                "session.resume",
                {
                    "session_id": employee_session_id,
                    "cols": SESSION_COLS,
                    "lazy": True,
                    "source": CHAT_SOURCE,
                },
                timeout=self._request_timeout,
            )
            stored = str(resumed.get("resumed") or employee_session_id)
            self._remember_session_identity(
                child,
                str(resumed.get("session_id") or ""),
                employee_session_id,
                stored,
            )
            self._bind_live_session(
                child,
                stored,
                str(resumed.get("session_id") or ""),
                resumed,
            )
            raw_messages = resumed.get("messages")
            messages = self._normalize_employee_session_history_messages(raw_messages)
            return EmployeeSessionHistory(messages=messages, employee_session_id=stored)
        except GatewayRpcError as exc:
            if exc.code == NOT_FOUND_CODE:
                return EmployeeSessionHistory(
                    messages=(), employee_session_id=employee_session_id
                )
            raise PlannerError(
                ErrorCode.gateway_offline,
                "Employee session history failed",
                {"detail": str(exc), "ticket_id": ticket_id},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "Employee session history failed",
                {"detail": str(exc), "ticket_id": ticket_id},
            ) from exc

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
        resolved_key = session_key
        try:
            child = self._child_or_spawn()
            _, resolved_key = self._resume_or_create(
                child,
                session_key,
                SESSION_SOURCE,
                allow_create=not require_existing_session,
            )
            if resolved_key and on_session_key is not None:
                on_session_key(resolved_key)
            return self._submit_employee_consequence(
                self._required_live_session(resolved_key),
                resolved_key,
                entity_id,
                prompt_text,
                on_event,
            )
        except SharedGatewayBusy:
            raise
        except GatewayRpcError as exc:
            return RunResult("errored", "", None, resolved_key, str(exc))
        except GatewayError as exc:
            return RunResult("errored", "", None, resolved_key, str(exc))

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]:
        try:
            child = self._child_or_spawn()
            if mode == "command" and text == "/new":
                if require_existing_session:
                    raise GatewayError("existing session is required")
                _, candidate = self._resume_or_create(child, None, CHAT_SOURCE)
                live_session = self._bind_human_live_session(
                    self._required_live_session(candidate), bind_session_key
                )
                if live_session.stored_session_key != candidate:
                    raise GatewayError("fresh Chat session binding did not retain its candidate")
                yield from self._human_command_completion("New session started.", "system")
                return
            _, candidate = self._resume_or_create(
                child,
                session_key,
                CHAT_SOURCE,
                allow_create=not require_existing_session,
                reuse_live_session=True,
            )
            live_session = self._bind_human_live_session(
                self._required_live_session(candidate), bind_session_key
            )
            stored = live_session.stored_session_key
            if mode == "command":
                yield from self._observe_human_command(
                    stored,
                    entity_id,
                    text,
                    bind_session_key=bind_session_key,
                )
            else:
                yield from self._observe_human_consequence(
                    stored,
                    entity_id,
                    text,
                    "assistant",
                    image_paths=image_paths,
                    bind_session_key=bind_session_key,
                )
        except SharedGatewayBusy as exc:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": exc.session_key},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway stream failed", {"detail": str(exc)}
            ) from exc

    def interrupt(self, session_key: str, entity_id: str) -> None:
        try:
            child = self._child_or_spawn()
            live_session = self.live_session(session_key)
            if live_session is None:
                live_session_id = self._live_session_id_for_stored_key(child, session_key)
                self._bind_live_session(child, session_key, live_session_id, {})
                live_session = self._required_live_session(session_key)
            receipt = live_session.interrupt(timeout=self._request_timeout)
            if isinstance(receipt, TransportUnknown):
                raise GatewayError(receipt.detail)
        except GatewayRpcError as exc:
            if exc.code in (NOT_FOUND_CODE, LIVE_SESSION_NOT_FOUND_CODE):
                raise PlannerError(
                    ErrorCode.not_found,
                    "chat session not found",
                    {"entity_id": entity_id, "session_key": session_key},
                ) from exc
            raise PlannerError(
                ErrorCode.gateway_offline,
                "chat gateway interrupt failed",
                {"detail": str(exc), "entity_id": entity_id},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "chat gateway interrupt failed",
                {"detail": str(exc), "entity_id": entity_id},
            ) from exc

    def respond_to_clarification(
        self, session_key: str, entity_id: str, request_id: str, answer: str
    ) -> None:
        try:
            live_session = self.live_session(session_key)
            if live_session is None:
                raise GatewayError(f"Hermes live session is unavailable for {session_key}")
            live_session.request(
                "clarify.respond",
                {
                    "session_id": live_session.live_session_id,
                    "request_id": request_id,
                    "answer": answer,
                },
                timeout=self._request_timeout,
            )
        except GatewayRpcError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "clarification response failed",
                {"detail": str(exc), "entity_id": entity_id, "session_key": session_key},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "clarification response failed",
                {"detail": str(exc), "entity_id": entity_id, "session_key": session_key},
            ) from exc

    def catalog(self) -> CommandCatalog:
        try:
            child = self._child_or_spawn()
            return self._build_catalog(
                child.request("commands.catalog", timeout=self._request_timeout)
            )
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway catalog failed", {"detail": str(exc)}
            ) from exc

    def _human_command_completion(
        self, reply: str, role: str
    ) -> Iterator[HumanChatObservation]:
        if reply:
            yield HumanChatOutputDelta(reply)
        yield HumanChatCompletion(
            reply,
            "system" if role == "system" else "assistant",
        )

    def _observe_human_command(
        self,
        stored: str,
        entity_id: str,
        command: str,
        *,
        bind_session_key: HumanSessionKeyBinder,
    ) -> Iterator[HumanChatObservation]:
        pending, prepared, reply, kind, resolved_stored = (
            self._begin_human_command_operation(
                stored,
                entity_id,
                command,
                bind_session_key,
            )
        )
        if pending is None or prepared is None:
            yield from self._human_command_completion(reply, kind)
            return
        accepted = self._accept_pending_submission(
            pending,
            prepared,
            resolved_stored,
            entity_id,
        )
        yield from self._observe_accepted_human_submission(accepted, kind)

    def _begin_human_command_operation(
        self,
        stored: str,
        entity_id: str,
        command: str,
        bind_session_key: HumanSessionKeyBinder,
    ) -> tuple[PendingSubmission | None, PreparedWorkerPrompt | None, str, str, str]:
        live_session = self._bind_human_live_session(
            self._live_session_for_human_write(stored), bind_session_key
        )
        effective_stored = live_session.stored_session_key
        try:
            result = self._begin_command_operation(
                live_session,
                effective_stored,
                entity_id,
                command,
            )
        except LiveSessionDormant:
            live_session = self._bind_human_live_session(
                self._resume_live_session_for_human_write(effective_stored), bind_session_key
            )
            effective_stored = live_session.stored_session_key
            result = self._begin_command_operation(
                live_session,
                effective_stored,
                entity_id,
                command,
            )
        return (*result, live_session.stored_session_key)

    def _begin_command_operation(
        self,
        live_session: LiveSession,
        stored: str,
        entity_id: str,
        command: str,
    ) -> tuple[PendingSubmission | None, PreparedWorkerPrompt | None, str, str]:
        name, arg = self._split_command(command)
        with live_session.ordered_operation() as operation:
            try:
                payload = operation.request(
                    "slash.exec",
                    {"session_id": live_session.live_session_id, "command": command},
                    timeout=self._request_timeout,
                )
            except GatewayRpcError as exc:
                if exc.code == BUSY_CODE:
                    raise SharedGatewayBusy(stored) from exc
                if exc.code != 4018:
                    raise
                payload = operation.request(
                    "command.dispatch",
                    {
                        "session_id": live_session.live_session_id,
                        "name": name,
                        "arg": arg,
                    },
                    timeout=self._request_timeout,
                )
            if str(payload.get("type") or "") == "alias":
                target_name, target_arg = self._split_command(
                    str(payload.get("target") or "")
                )
                combined = (
                    f"{target_arg} {arg}".strip()
                    if target_arg and arg
                    else (arg or target_arg)
                )
                payload = operation.request(
                    "command.dispatch",
                    {
                        "session_id": live_session.live_session_id,
                        "name": target_name,
                        "arg": combined,
                    },
                    timeout=self._request_timeout,
                )
            payload_type = str(payload.get("type") or "")
            if payload_type in ("skill", "send"):
                prepared = self._worker_context.prepare(
                    entity_id,
                    str(payload.get("message") or ""),
                )
                pending = operation.begin_submission(prepared.model_text)
                if isinstance(pending, TransportUnknown):
                    raise GatewayError(pending.detail)
                return pending, prepared, "", "assistant"
            if payload_type in ("exec", "plugin"):
                return None, None, str(payload.get("output") or ""), "system"
            output = str(payload.get("output") or payload.get("message") or "")
            warning = str(payload.get("warning") or "")
            reply = (output + "\n" + warning).strip() if warning else output
            return None, None, reply, "system"

    def _accept_pending_submission(
        self,
        pending: PendingSubmission,
        prepared: PreparedWorkerPrompt,
        stored: str,
        entity_id: str,
    ) -> AcceptedSubmission:
        try:
            accepted = pending.wait(self._request_timeout)
        except GatewayRpcError as exc:
            if exc.code == BUSY_CODE:
                raise SharedGatewayBusy(stored) from exc
            raise
        if isinstance(accepted, TransportUnknown):
            raise GatewayError(accepted.detail)
        if prepared.receipts:
            self._worker_context.acknowledge(entity_id, prepared.receipts)
        return accepted

    def _child_or_spawn(self) -> GatewayChild:
        previous_manager: LiveSessionManager | None = None
        with self._lock:
            if self._child is not None and self._child.alive:
                return self._child
            previous_manager = self._session_manager
            child = GatewayChild(str(self._python), self._env(), spawn=self._spawn)
            child.wait_ready(self._ready_timeout)
            session_manager = LiveSessionManager(child)
            self._live_session_ids_by_stored_key.clear()
            self._child = child
            self._session_manager = session_manager
        if previous_manager is not None:
            previous_manager.shutdown()
        return child

    def _env(self) -> dict[str, str]:
        env = dict(self._base_env)
        env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(self._python))
        env["HERMES_HOME"] = str(self._home)
        env["HERMES_TUI_SKILLS"] = self._worker_role
        return env

    def _resume_or_create(
        self,
        child: GatewayChild,
        session_key: str | None,
        source: str,
        *,
        allow_create: bool = True,
        reuse_live_session: bool = False,
    ) -> tuple[str, str]:
        if session_key and reuse_live_session:
            live_session = self.live_session(session_key)
            if live_session is not None:
                return live_session.live_session_id, live_session.stored_session_key
        if session_key:
            try:
                resumed = child.request(
                    "session.resume",
                    {"session_id": session_key},
                    timeout=self._request_timeout,
                )
                live_session_id = str(resumed.get("session_id") or "")
                stored_key = str(resumed.get("resumed") or session_key)
                self._remember_session_identity(child, live_session_id, session_key, stored_key)
                self._bind_live_session(child, stored_key, live_session_id, resumed)
                return live_session_id, stored_key
            except GatewayRpcError as exc:
                if exc.code == BUSY_CODE:
                    raise SharedGatewayBusy(session_key) from exc
                if exc.code != NOT_FOUND_CODE:
                    raise
                if not allow_create:
                    raise
        elif not allow_create:
            raise GatewayRpcError(NOT_FOUND_CODE, "existing session is required")
        if not allow_create:
            raise GatewayRpcError(NOT_FOUND_CODE, "existing session was not found")
        created = child.request(
            "session.create",
            {"source": source, "cols": SESSION_COLS},
            timeout=self._request_timeout,
        )
        live_sid = str(created.get("session_id") or "")
        stored = str(created.get("stored_session_id") or live_sid)
        self._remember_session_identity(child, live_sid, stored)
        self._bind_live_session(child, stored, live_sid, created)
        return live_sid, stored

    def _bind_live_session(
        self,
        child: GatewayChild,
        stored_session_key: str,
        live_session_id: str,
        snapshot: JsonDict,
    ) -> None:
        if not stored_session_key or not live_session_id:
            return
        with self._lock:
            manager = self._session_manager if self._child is child else None
        if manager is not None:
            manager.bind(stored_session_key, live_session_id, snapshot=snapshot)

    def _remember_session_identity(
        self,
        child: GatewayChild,
        live_session_id: str,
        *stored_keys: str,
    ) -> None:
        if not live_session_id:
            return
        with self._lock:
            if self._child is not child:
                return
            for stored_key in stored_keys:
                if stored_key:
                    self._live_session_ids_by_stored_key[stored_key] = live_session_id

    def _live_session_id_for_stored_key(self, child: GatewayChild, stored_key: str) -> str:
        with self._lock:
            if self._child is child:
                return self._live_session_ids_by_stored_key.get(stored_key, stored_key)
        return stored_key

    def _required_live_session(self, stored_key: str) -> LiveSession:
        live_session = self.live_session(stored_key)
        if live_session is None:
            raise GatewayError(f"Hermes session ingress is unavailable for {stored_key}")
        return live_session

    def _resume_live_session_for_human_write(self, stored_key: str) -> LiveSession:
        child = self._child_or_spawn()
        _, resumed_stored_key = self._resume_or_create(
            child,
            stored_key,
            CHAT_SOURCE,
            allow_create=False,
        )
        return self._required_live_session(resumed_stored_key)

    def _live_session_for_human_write(self, stored_key: str) -> LiveSession:
        live_session = self.live_session(stored_key)
        if live_session is not None:
            return live_session
        return self._resume_live_session_for_human_write(stored_key)

    def _bind_human_live_session(
        self,
        candidate: LiveSession,
        bind_session_key: HumanSessionKeyBinder,
    ) -> LiveSession:
        """Return the live Hermes handle for the durable key chosen by Panels."""
        seen: set[str] = set()
        while True:
            candidate_key = candidate.stored_session_key
            if candidate_key in seen:
                raise GatewayError("human Chat session binding did not converge")
            seen.add(candidate_key)
            effective_key = bind_session_key(candidate_key)
            if effective_key == candidate_key:
                return candidate
            effective_live = self.live_session(effective_key)
            if effective_live is not None:
                candidate = effective_live
                continue
            candidate = self._resume_live_session_for_human_write(effective_key)

    def _submit_human_consequence(
        self,
        stored_key: str,
        text: str,
        *,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
    ) -> tuple[AcceptedSubmission | TransportUnknown, str]:
        live_session = self._bind_human_live_session(
            self._live_session_for_human_write(stored_key), bind_session_key
        )
        try:
            accepted = live_session.submit_consequence(
                text,
                timeout=self._request_timeout,
                image_paths=image_paths,
            )
        except LiveSessionDormant:
            # The prior consumer released between reuse lookup and write admission.
            # No prompt was written, so one resume is safe; prompt outcomes are never retried.
            live_session = self._bind_human_live_session(
                self._resume_live_session_for_human_write(live_session.stored_session_key),
                bind_session_key,
            )
            accepted = live_session.submit_consequence(
                text,
                timeout=self._request_timeout,
                image_paths=image_paths,
            )
        return accepted, live_session.stored_session_key

    def _submit_employee_consequence(
        self,
        live_session: LiveSession,
        stored_key: str,
        entity_id: str,
        text: str,
        on_event: OnEvent | None,
    ) -> RunResult:
        with live_session.ordered_operation() as operation:
            if live_session.pending_consequence_count:
                raise SharedGatewayBusy(stored_key)
            prepared = self._worker_context.prepare(entity_id, text)
            pending = operation.begin_submission(prepared.model_text)
        if isinstance(pending, TransportUnknown):
            return RunResult(
                "errored",
                "",
                None,
                stored_key,
                "Hermes prompt submission outcome is unknown; "
                f"the employee prompt was not retried: {pending.detail}",
            )
        try:
            accepted = pending.wait(self._request_timeout)
        except GatewayRpcError as exc:
            if exc.code == BUSY_CODE:
                raise SharedGatewayBusy(stored_key) from exc
            raise
        if isinstance(accepted, TransportUnknown):
            return RunResult(
                "errored",
                "",
                None,
                stored_key,
                "Hermes prompt submission outcome is unknown; "
                f"the employee prompt was not retried: {accepted.detail}",
            )
        context_acknowledged = False
        if prepared.receipts and accepted.receipt.disposition in ("streaming", "steered"):
            self._worker_context.acknowledge(entity_id, prepared.receipts)
            context_acknowledged = True
        if accepted.receipt.disposition == "steered":
            accepted.consequence.release()
            return RunResult(
                "errored",
                "",
                None,
                stored_key,
                "Hermes delivered the employee prompt by steering the active execution; "
                "no independent employee execution was created",
            )
        try:
            while True:
                observation = accepted.consequence.next_observation()
                if prepared.receipts and not context_acknowledged:
                    self._worker_context.acknowledge(entity_id, prepared.receipts)
                    context_acknowledged = True
                event = {
                    "type": observation.event_type,
                    "session_id": observation.live_session_id,
                    "payload": observation.payload,
                }
                self._notify(on_event, event)
                if observation.event_type == "error":
                    return RunResult(
                        "errored",
                        "",
                        None,
                        stored_key,
                        str(observation.payload.get("message") or "gateway error event"),
                    )
                if observation.event_type != "message.complete":
                    continue
                text_out = str(observation.payload.get("text") or "")
                usage_raw = observation.payload.get("usage")
                usage = usage_raw if isinstance(usage_raw, dict) else None
                status = str(observation.payload.get("status") or "complete")
                if status == "complete":
                    return RunResult("complete", text_out, usage, stored_key, None)
                if status == "interrupted":
                    return RunResult("interrupted", text_out, usage, stored_key, None)
                return RunResult(
                    "errored",
                    text_out,
                    usage,
                    stored_key,
                    text_out or "run ended with status=error",
                )
        finally:
            accepted.consequence.release()

    def _observe_human_consequence(
        self,
        stored_key: str,
        entity_id: str,
        text: str,
        role: str,
        *,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[HumanChatObservation]:
        prepared = self._worker_context.prepare(entity_id, text)
        try:
            accepted, _resolved_stored_key = self._submit_human_consequence(
                stored_key,
                prepared.model_text,
                bind_session_key=bind_session_key,
                image_paths=image_paths,
            )
        except GatewayRpcError as exc:
            if exc.code == BUSY_CODE:
                raise SharedGatewayBusy(stored_key) from exc
            raise
        if isinstance(accepted, TransportUnknown):
            raise GatewayError(accepted.detail)
        if prepared.receipts:
            self._worker_context.acknowledge(entity_id, prepared.receipts)
        yield from self._observe_accepted_human_submission(accepted, role)

    def _observe_accepted_human_submission(
        self,
        accepted: AcceptedSubmission,
        role: str,
    ) -> Iterator[HumanChatObservation]:
        seen_delta = False
        try:
            while True:
                observation = accepted.consequence.next_observation()
                etype = observation.event_type
                payload = observation.payload
                if etype == "error":
                    raise GatewayError(str(payload.get("message") or "gateway error event"))
                activity = normalize_gateway_activity(etype, payload)
                if activity is not None:
                    yield activity
                if etype == "message.delta":
                    delta = str(payload.get("text") or payload.get("delta") or "")
                    if delta:
                        seen_delta = True
                        yield HumanChatOutputDelta(delta)
                if etype == "message.complete":
                    text_out = str(payload.get("text") or "")
                    gw_status = str(payload.get("status") or "complete")
                    if gw_status in ("complete", "interrupted"):
                        if text_out and not seen_delta:
                            yield HumanChatOutputDelta(text_out)
                        yield HumanChatCompletion(
                            text_out,
                            "system" if role == "system" else "assistant",
                        )
                        return
                    raise GatewayError(text_out or "run ended with status=error")
        finally:
            accepted.consequence.release()

    @staticmethod
    def _notify(on_event: OnEvent | None, event: JsonDict) -> None:
        if on_event is None:
            return
        on_event(event)

    @staticmethod
    def _split_command(command: str) -> tuple[str, str]:
        parts = command.strip().split(maxsplit=1)
        name = parts[0].lstrip("/") if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        return name, arg

    @staticmethod
    def _build_catalog(raw: dict[str, Any]) -> CommandCatalog:
        def _pairs(seq: Any) -> tuple[tuple[str, str], ...]:
            out: list[tuple[str, str]] = []
            for item in seq or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append((str(item[0]), str(item[1])))
            return tuple(out)

        pairs = raw.get("pairs") or []
        skill_count = int(raw.get("skill_count") or 0)
        skills = _pairs(pairs[-skill_count:]) if skill_count > 0 else ()
        categories = tuple(
            CommandCategory(name=str(cat.get("name") or ""), pairs=_pairs(cat.get("pairs")))
            for cat in (raw.get("categories") or [])
            if isinstance(cat, dict)
        )
        canon_raw = raw.get("canon")
        canon = (
            {str(k): str(v) for k, v in canon_raw.items()} if isinstance(canon_raw, dict) else {}
        )
        sub_raw = raw.get("sub")
        sub = (
            {str(k): [str(x) for x in (v or [])] for k, v in sub_raw.items()}
            if isinstance(sub_raw, dict)
            else {}
        )
        return CommandCatalog(categories=categories, skills=skills, canon=canon, sub=sub)

    @classmethod
    def _normalize_employee_session_history_messages(
        cls, raw_messages: Any
    ) -> tuple[EmployeeSessionHistoryMessage, ...]:
        if not isinstance(raw_messages, list):
            return ()
        messages: list[EmployeeSessionHistoryMessage] = []
        for index, raw in enumerate(raw_messages, start=1):
            if not isinstance(raw, dict):
                continue
            text = cls._message_text(raw)
            if not text:
                continue
            role = str(raw.get("role") or raw.get("author") or raw.get("type") or "assistant")
            created_at = cls._message_created_at(raw, index)
            messages.append(
                EmployeeSessionHistoryMessage(role=role, text=text, created_at=created_at)
            )
        return tuple(messages)

    @classmethod
    def _message_text(cls, raw: dict[str, Any]) -> str:
        for key in ("text", "content", "message", "output"):
            value = raw.get(key)
            text = cls._stringify_message_content(value)
            if text:
                return text
        return ""

    @classmethod
    def _stringify_message_content(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                text = cls._stringify_message_content(item)
                if text:
                    parts.append(text)
            return "\n".join(parts)
        if isinstance(value, dict):
            for key in ("text", "content", "message", "output"):
                text = cls._stringify_message_content(value.get(key))
                if text:
                    return text
        return ""

    @staticmethod
    def _message_created_at(raw: dict[str, Any], fallback: int) -> int:
        for key in ("created_at", "timestamp", "time"):
            value = raw.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(float(value))
                except ValueError:
                    continue
        return fallback
