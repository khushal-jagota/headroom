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
from typing import Any

from planner.chat.contracts import (
    ChatHistory,
    ChatMessage,
    ChatSendResult,
    ChatStreamChunk,
    CommandCatalog,
    CommandCategory,
    CommandRunResult,
    GatewayStatus,
)
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
from planner.worker_context.contracts import PreparedWorkerPrompt, WorkerContextService
from planner.worker_context.service import EmptyWorkerContextService, visible_prompt_text

SESSION_COLS = 100
SESSION_SOURCE = "planner"
CHAT_SOURCE = "planner-chat"
BUSY_CODE = 4009
NOT_FOUND_CODE = 4007
LIVE_SESSION_NOT_FOUND_CODE = 4001


def _activity_label_for_gateway_event(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type not in ("tool.start", "tool.delta", "tool.end", "command.start"):
        return None
    for key in ("label", "name", "command", "tool_name"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    raw_tool = payload.get("tool")
    tool = raw_tool if isinstance(raw_tool, dict) else {}
    for key in ("label", "name"):
        value = str(tool.get(key) or "").strip()
        if value:
            return value
    return "Working"


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

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        return self._gateway_for(entity_id).history(session_key, entity_id)

    def send(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        on_session_key: Callable[[str], None] | None = None,
    ) -> ChatSendResult:
        return self._gateway_for(entity_id).send(session_key, entity_id, text, on_session_key)

    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_path: Path | None = None,
    ) -> Iterator[ChatStreamChunk]:
        gateway = self._gateway_for(entity_id)
        if image_path is None:
            yield from gateway.stream(session_key, entity_id, text, mode, on_session_key)
        else:
            yield from gateway.stream(
                session_key, entity_id, text, mode, on_session_key, image_path
            )

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self._gateway_for(entity_id).interrupt(session_key, entity_id)

    def catalog(self) -> CommandCatalog:
        return self._default_gateway.catalog()

    def run_command(
        self,
        session_key: str | None,
        entity_id: str,
        command: str,
        on_session_key: Callable[[str], None] | None = None,
    ) -> CommandRunResult:
        return self._gateway_for(entity_id).run_command(
            session_key, entity_id, command, on_session_key
        )

    def shutdown(self) -> None:
        seen: set[int] = set()
        for gateway in (self._default_gateway, *self._entity_gateways.values()):
            ident = id(gateway)
            if ident in seen:
                continue
            seen.add(ident)
            gateway.shutdown()


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

    def shutdown(self) -> None:
        with self._lock:
            child = self._child
            session_manager = self._session_manager
            self._child = None
            self._session_manager = None
            self._live_session_ids_by_stored_key.clear()
        try:
            if session_manager is not None:
                session_manager.shutdown()
        finally:
            if child is not None:
                child.shutdown()

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

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        if session_key is None:
            return ChatHistory(messages=(), session_key=None)
        try:
            child = self._child_or_spawn()
            resumed = child.request(
                "session.resume",
                {
                    "session_id": session_key,
                    "cols": SESSION_COLS,
                    "lazy": True,
                    "source": CHAT_SOURCE,
                },
                timeout=self._request_timeout,
            )
            stored = str(resumed.get("resumed") or session_key)
            self._remember_session_identity(
                child,
                str(resumed.get("session_id") or ""),
                session_key,
                stored,
            )
            self._bind_live_session(
                child,
                stored,
                str(resumed.get("session_id") or ""),
                resumed,
            )
            raw_messages = resumed.get("messages")
            messages = self._normalize_history_messages(raw_messages)
            return ChatHistory(messages=messages, session_key=stored)
        except GatewayRpcError as exc:
            if exc.code == NOT_FOUND_CODE:
                return ChatHistory(messages=(), session_key=session_key)
            raise PlannerError(
                ErrorCode.gateway_offline,
                "chat gateway history failed",
                {"detail": str(exc), "entity_id": entity_id},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "chat gateway history failed",
                {"detail": str(exc), "entity_id": entity_id},
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

    def send(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        on_session_key: Callable[[str], None] | None = None,
    ) -> ChatSendResult:
        try:
            child = self._child_or_spawn()
            _, stored = self._resume_or_create(
                child,
                session_key,
                CHAT_SOURCE,
                reuse_live_session=True,
            )
            if on_session_key is not None:
                on_session_key(stored)
            result = self._submit_human_and_drain(
                stored,
                entity_id,
                text,
            )
            resolved_stored = result.session_key or stored
            if on_session_key is not None and resolved_stored != stored:
                on_session_key(resolved_stored)
        except SharedGatewayBusy as exc:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": exc.session_key},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
            ) from exc
        return ChatSendResult(reply_text=result.text, session_key=resolved_stored)

    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_path: Path | None = None,
    ) -> Iterator[ChatStreamChunk]:
        try:
            child = self._child_or_spawn()
            _, stored = self._resume_or_create(
                child,
                session_key,
                CHAT_SOURCE,
                reuse_live_session=True,
            )
            if on_session_key is not None:
                on_session_key(stored)
            yield ChatStreamChunk(type="session", session_key=stored)
            if mode == "command":
                yield from self._stream_command(
                    stored,
                    entity_id,
                    text,
                    on_session_key=on_session_key,
                )
            else:
                yield from self._stream_human_consequence(
                    stored,
                    entity_id,
                    text,
                    "assistant",
                    image_path=image_path,
                    on_session_key=on_session_key,
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

    def run_command(
        self,
        session_key: str | None,
        entity_id: str,
        command: str,
        on_session_key: Callable[[str], None] | None = None,
    ) -> CommandRunResult:
        try:
            child = self._child_or_spawn()
            _, stored = self._resume_or_create(
                child,
                session_key,
                CHAT_SOURCE,
                reuse_live_session=True,
            )
            if on_session_key is not None:
                on_session_key(stored)
            pending, prepared, reply, kind, resolved_stored = (
                self._begin_human_command_operation(
                    stored,
                    entity_id,
                    command,
                )
            )
            if on_session_key is not None and resolved_stored != stored:
                on_session_key(resolved_stored)
            if pending is not None and prepared is not None:
                accepted = self._accept_pending_submission(
                    pending,
                    prepared,
                    resolved_stored,
                    entity_id,
                )
                reply = self._drain_accepted_submission(accepted, resolved_stored).text
            return CommandRunResult(
                reply_text=reply,
                session_key=resolved_stored,
                kind=kind,
            )
        except SharedGatewayBusy as exc:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": exc.session_key},
            ) from exc
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway command failed", {"detail": str(exc)}
            ) from exc

    def _stream_done(self, reply: str, stored: str, kind: str) -> Iterator[ChatStreamChunk]:
        if reply:
            yield ChatStreamChunk(type="token", text=reply)
        yield ChatStreamChunk(
            type="done", reply_text=reply, session_key=stored, kind=kind
        )

    def _stream_command(
        self,
        stored: str,
        entity_id: str,
        command: str,
        *,
        on_session_key: Callable[[str], None] | None,
    ) -> Iterator[ChatStreamChunk]:
        pending, prepared, reply, kind, resolved_stored = (
            self._begin_human_command_operation(
                stored,
                entity_id,
                command,
            )
        )
        if on_session_key is not None and resolved_stored != stored:
            on_session_key(resolved_stored)
        if pending is None or prepared is None:
            yield from self._stream_done(reply, resolved_stored, kind)
            return
        accepted = self._accept_pending_submission(
            pending,
            prepared,
            resolved_stored,
            entity_id,
        )
        yield from self._stream_accepted_submission(accepted, resolved_stored, kind)

    def _begin_human_command_operation(
        self,
        stored: str,
        entity_id: str,
        command: str,
    ) -> tuple[PendingSubmission | None, PreparedWorkerPrompt | None, str, str, str]:
        live_session = self._live_session_for_human_write(stored)
        try:
            result = self._begin_command_operation(
                live_session,
                stored,
                entity_id,
                command,
            )
        except LiveSessionDormant:
            live_session = self._resume_live_session_for_human_write(stored)
            result = self._begin_command_operation(
                live_session,
                stored,
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

    def _submit_human_consequence(
        self,
        stored_key: str,
        text: str,
        *,
        image_path: Path | None = None,
    ) -> tuple[AcceptedSubmission | TransportUnknown, str]:
        live_session = self._live_session_for_human_write(stored_key)
        try:
            accepted = live_session.submit_consequence(
                text,
                timeout=self._request_timeout,
                image_path=image_path,
            )
        except LiveSessionDormant:
            # The prior consumer released between reuse lookup and write admission.
            # No prompt was written, so one resume is safe; prompt outcomes are never retried.
            live_session = self._resume_live_session_for_human_write(stored_key)
            accepted = live_session.submit_consequence(
                text,
                timeout=self._request_timeout,
                image_path=image_path,
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

    def _submit_human_and_drain(
        self,
        stored_key: str,
        entity_id: str,
        text: str,
    ) -> RunResult:
        prepared = self._worker_context.prepare(entity_id, text)
        try:
            accepted, resolved_stored_key = self._submit_human_consequence(
                stored_key,
                prepared.model_text,
            )
        except GatewayRpcError as exc:
            if exc.code == BUSY_CODE:
                raise SharedGatewayBusy(stored_key) from exc
            raise
        if isinstance(accepted, TransportUnknown):
            raise GatewayError(accepted.detail)
        if prepared.receipts:
            self._worker_context.acknowledge(entity_id, prepared.receipts)
        return self._drain_accepted_submission(accepted, resolved_stored_key)

    def _drain_accepted_submission(
        self,
        accepted: AcceptedSubmission,
        stored_key: str,
    ) -> RunResult:
        try:
            while True:
                observation = accepted.consequence.next_observation()
                event_type = observation.event_type
                payload = observation.payload
                if event_type == "error":
                    return RunResult(
                        "errored",
                        "",
                        None,
                        stored_key,
                        str(payload.get("message") or "gateway error event"),
                    )
                if event_type != "message.complete":
                    continue
                text_out = str(payload.get("text") or "")
                usage_raw = payload.get("usage")
                usage = usage_raw if isinstance(usage_raw, dict) else None
                status = str(payload.get("status") or "complete")
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

    def _stream_human_consequence(
        self,
        stored_key: str,
        entity_id: str,
        text: str,
        kind: str,
        image_path: Path | None = None,
        on_session_key: Callable[[str], None] | None = None,
    ) -> Iterator[ChatStreamChunk]:
        prepared = self._worker_context.prepare(entity_id, text)
        try:
            accepted, resolved_stored_key = self._submit_human_consequence(
                stored_key,
                prepared.model_text,
                image_path=image_path,
            )
        except GatewayRpcError as exc:
            if exc.code == BUSY_CODE:
                raise SharedGatewayBusy(stored_key) from exc
            raise
        if isinstance(accepted, TransportUnknown):
            raise GatewayError(accepted.detail)
        if on_session_key is not None and resolved_stored_key != stored_key:
            on_session_key(resolved_stored_key)
        if prepared.receipts:
            self._worker_context.acknowledge(entity_id, prepared.receipts)
        yield from self._stream_accepted_submission(accepted, resolved_stored_key, kind)

    def _stream_accepted_submission(
        self,
        accepted: AcceptedSubmission,
        stored_key: str,
        kind: str,
    ) -> Iterator[ChatStreamChunk]:
        seen_delta = False
        try:
            while True:
                observation = accepted.consequence.next_observation()
                etype = observation.event_type
                payload = observation.payload
                if etype == "error":
                    raise GatewayError(str(payload.get("message") or "gateway error event"))
                activity_label = _activity_label_for_gateway_event(etype, payload)
                if activity_label is not None:
                    yield ChatStreamChunk(type="activity", text=activity_label)
                if etype == "message.delta":
                    delta = str(payload.get("text") or payload.get("delta") or "")
                    if delta:
                        seen_delta = True
                        yield ChatStreamChunk(type="token", text=delta)
                if etype == "message.complete":
                    text_out = str(payload.get("text") or "")
                    gw_status = str(payload.get("status") or "complete")
                    if gw_status in ("complete", "interrupted"):
                        if text_out and not seen_delta:
                            yield ChatStreamChunk(type="token", text=text_out)
                        yield ChatStreamChunk(
                            type="done",
                            reply_text=text_out,
                            session_key=stored_key,
                            kind=kind,
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
    def _normalize_history_messages(cls, raw_messages: Any) -> tuple[ChatMessage, ...]:
        if not isinstance(raw_messages, list):
            return ()
        messages: list[ChatMessage] = []
        for index, raw in enumerate(raw_messages, start=1):
            if not isinstance(raw, dict):
                continue
            text = cls._message_text(raw)
            if not text:
                continue
            role = str(raw.get("role") or raw.get("author") or raw.get("type") or "assistant")
            if role.lower() in ("user", "human"):
                text = visible_prompt_text(text)
            created_at = cls._message_created_at(raw, index)
            messages.append(ChatMessage(role=role, text=text, created_at=created_at))
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
