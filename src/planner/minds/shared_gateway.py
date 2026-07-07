"""Shared worker-role Hermes gateway child.

One process hosts many live Hermes sessions. Request responses are demuxed by
JSON-RPC id in GatewayChild; turn events are drained through per-session event
streams so concurrent sessions cannot consume each other's completion events.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from planner.chat.contracts import (
    ChatSendResult,
    CommandCatalog,
    CommandCategory,
    CommandRunResult,
    GatewayStatus,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.minds.config import hermes_src_root
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
from planner.minds.runner import OnEvent, RunResult

SESSION_COLS = 100
SESSION_SOURCE = "planner"
CHAT_SOURCE = "planner-chat"
BUSY_CODE = 4009
NOT_FOUND_CODE = 4007


class SharedGatewayBusy(Exception):
    """Hermes rejected a prompt or command because this session is already running."""

    def __init__(self, session_key: str | None = None) -> None:
        super().__init__("session busy")
        self.session_key = session_key


class SharedGateway:
    """Lifecycle owner for the planner's one shared worker gateway child."""

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
    ) -> None:
        self._python = Path(hermes_python).expanduser()
        self._home = Path(home).expanduser()
        self._worker_role = worker_role
        self._spawn = spawn
        self._base_env = dict(base_env if base_env is not None else os.environ)
        self._ready_timeout = ready_timeout
        self._request_timeout = request_timeout
        self._lock = threading.Lock()
        self._child: GatewayChild | None = None

    def start(self) -> None:
        self._child_or_spawn()

    def shutdown(self) -> None:
        with self._lock:
            child = self._child
            self._child = None
        if child is not None:
            child.shutdown()

    def status(self) -> GatewayStatus:
        if not self._python.exists():
            return GatewayStatus(
                available=False, detail=f"hermes interpreter not found: {self._python}"
            )
        child = self._child
        return GatewayStatus(available=child is None or child.alive)

    def run_ticket_step(
        self,
        session_key: str | None,
        prompt_text: str,
        on_event: OnEvent | None = None,
    ) -> RunResult:
        resolved_key = session_key
        try:
            child = self._child_or_spawn()
            live_sid, resolved_key = self._resume_or_create(child, session_key, SESSION_SOURCE)
            return self._submit_and_drain(child, live_sid, resolved_key, prompt_text, on_event)
        except SharedGatewayBusy:
            raise
        except GatewayRpcError as exc:
            return RunResult("errored", "", None, resolved_key, str(exc))
        except GatewayError as exc:
            return RunResult("errored", "", None, resolved_key, str(exc))

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        try:
            child = self._child_or_spawn()
            live_sid, stored = self._resume_or_create(child, session_key, CHAT_SOURCE)
            result = self._submit_and_drain(child, live_sid, stored, text, None)
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
        return ChatSendResult(reply_text=result.text, session_key=stored)

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
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult:
        try:
            child = self._child_or_spawn()
            live_sid, stored = self._resume_or_create(child, session_key, CHAT_SOURCE)
            name, arg = self._split_command(command)
            try:
                result = child.request(
                    "slash.exec",
                    {"session_id": live_sid, "command": command},
                    timeout=self._request_timeout,
                )
            except GatewayRpcError as exc:
                if exc.code == BUSY_CODE:
                    raise SharedGatewayBusy(stored) from exc
                if exc.code != 4018:
                    raise
                payload = child.request(
                    "command.dispatch",
                    {"session_id": live_sid, "name": name, "arg": arg},
                    timeout=self._request_timeout,
                )
                reply, kind = self._interpret(child, live_sid, stored, payload, arg)
                return CommandRunResult(reply_text=reply, session_key=stored, kind=kind)
            if result.get("type"):
                reply, kind = self._interpret(child, live_sid, stored, result, arg)
                return CommandRunResult(reply_text=reply, session_key=stored, kind=kind)
            output = str(result.get("output") or "")
            warning = str(result.get("warning") or "")
            reply = (output + "\n" + warning).strip() if warning else output
            return CommandRunResult(reply_text=reply, session_key=stored, kind="system")
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

    def _child_or_spawn(self) -> GatewayChild:
        with self._lock:
            if self._child is not None and self._child.alive:
                return self._child
            child = GatewayChild(str(self._python), self._env(), spawn=self._spawn)
            child.wait_ready(self._ready_timeout)
            self._child = child
            return child

    def _env(self) -> dict[str, str]:
        env = dict(self._base_env)
        env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(self._python))
        env["HERMES_HOME"] = str(self._home)
        env["HERMES_TUI_SKILLS"] = self._worker_role
        return env

    def _resume_or_create(
        self, child: GatewayChild, session_key: str | None, source: str
    ) -> tuple[str, str]:
        if session_key:
            try:
                resumed = child.request(
                    "session.resume",
                    {"session_id": session_key},
                    timeout=self._request_timeout,
                )
                return (
                    str(resumed.get("session_id") or ""),
                    str(resumed.get("resumed") or session_key),
                )
            except GatewayRpcError as exc:
                if exc.code == BUSY_CODE:
                    raise SharedGatewayBusy(session_key) from exc
                if exc.code != NOT_FOUND_CODE:
                    raise
        created = child.request(
            "session.create",
            {"source": source, "cols": SESSION_COLS},
            timeout=self._request_timeout,
        )
        live_sid = str(created.get("session_id") or "")
        stored = str(created.get("stored_session_id") or live_sid)
        return live_sid, stored

    def _submit_and_drain(
        self,
        child: GatewayChild,
        live_sid: str,
        stored_key: str,
        text: str,
        on_event: OnEvent | None,
    ) -> RunResult:
        with child.open_session_events(live_sid) as events:
            try:
                child.request(
                    "prompt.submit",
                    {"session_id": live_sid, "text": text},
                    timeout=self._request_timeout,
                )
            except GatewayRpcError as exc:
                if exc.code == BUSY_CODE:
                    raise SharedGatewayBusy(stored_key) from exc
                raise
            while True:
                event = events.next_event()
                if event is None:
                    return RunResult(
                        "errored",
                        "",
                        None,
                        stored_key,
                        f"gateway child died mid-run; stderr: {child.stderr_tail()!r}",
                    )
                self._notify(on_event, event)
                etype = str(event.get("type") or "")
                raw = event.get("payload")
                payload = raw if isinstance(raw, dict) else {}
                if etype == "error":
                    return RunResult(
                        "errored",
                        "",
                        None,
                        stored_key,
                        str(payload.get("message") or "gateway error event"),
                    )
                if etype == "message.complete":
                    text_out = str(payload.get("text") or "")
                    usage_raw = payload.get("usage")
                    usage = usage_raw if isinstance(usage_raw, dict) else None
                    gw_status = str(payload.get("status") or "complete")
                    if gw_status == "complete":
                        return RunResult("complete", text_out, usage, stored_key, None)
                    if gw_status == "interrupted":
                        return RunResult("interrupted", text_out, usage, stored_key, None)
                    return RunResult(
                        "errored",
                        text_out,
                        usage,
                        stored_key,
                        text_out or "run ended with status=error",
                    )

    def _interpret(
        self,
        child: GatewayChild,
        live_sid: str,
        stored: str,
        payload: dict[str, Any],
        arg: str,
        *,
        alias_ok: bool = True,
    ) -> tuple[str, str]:
        ptype = str(payload.get("type") or "")
        if ptype in ("skill", "send"):
            message = str(payload.get("message") or "")
            result = self._submit_and_drain(child, live_sid, stored, message, None)
            return result.text, "assistant"
        if ptype in ("exec", "plugin"):
            return str(payload.get("output") or ""), "system"
        if ptype == "alias" and alias_ok:
            target_name, target_arg = self._split_command(str(payload.get("target") or ""))
            combined = f"{target_arg} {arg}".strip() if target_arg and arg else (arg or target_arg)
            resolved = child.request(
                "command.dispatch",
                {"session_id": live_sid, "name": target_name, "arg": combined},
                timeout=self._request_timeout,
            )
            return self._interpret(child, live_sid, stored, resolved, combined, alias_ok=False)
        return str(payload.get("output") or payload.get("message") or ""), "system"

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
