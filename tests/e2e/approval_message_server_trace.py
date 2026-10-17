"""Passive child trace for one synthetic owner-read browser proof, never production."""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from itertools import count
from pathlib import Path
from typing import Any


def install() -> None:
    """Retain call boundaries without changing arguments, results, or exception handling."""
    from fastapi import FastAPI

    from planner.conversation.storage import ConversationStore
    from planner.core import change_signal
    from planner.core.db import ChangeSignallingConnection

    path = Path(os.environ["PANELS_E2E_OWNER_READ_TRACE"])
    scope_path = path.with_name("owner-read-scope")
    trace_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "owner_read_trace_scope", default=None
    )
    request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "owner_read_trace_request", default=None
    )
    active = threading.local()
    request_counter = count(1)

    def current_scope() -> str | None:
        try:
            value = scope_path.read_text(encoding="utf-8")
            valid = len(value) == 32 and all(c in "0123456789abcdef" for c in value)
            return value if valid else None
        except (OSError, UnicodeError):
            return None

    def record(phase: str, **fields: Any) -> None:
        try:
            entry = {"wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                     "pid": os.getpid(), "thread": threading.get_ident(), "phase": phase, **fields}
            destination = (
                path.with_name("owner-read-installation.jsonl") if phase == "installed" else path
            )
            with destination.open("a", encoding="utf-8") as output:
                output.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # A collector must never change the operation it observes.

    original_call = FastAPI.__call__

    async def traced_call(self: Any, scope: Any, receive: Any, send: Any) -> None:
        route = scope.get("path", "")
        owner_read = (route.startswith("/api/conversation/conversations/dot-")
                      and route.endswith("/owner-read"))
        if not owner_read and route not in ("/api/board", "/api/changes"):
            await original_call(self, scope, receive, send)
            return
        selected_scope = current_scope()
        if selected_scope is None:
            await original_call(self, scope, receive, send)
            return
        identity = f"{os.getpid()}:{next(request_counter)}"
        token = request_id.set(identity)
        scope_token = trace_scope.set(selected_scope)
        detail = {"request_id": identity, "scope": selected_scope}
        record("ingress", **detail, path=route, method=scope.get("method"))
        body = bytearray()

        async def traced_receive() -> Any:
            message = await receive()
            if owner_read and message.get("type") == "http.request":
                chunk = message.get("body", b"")
                if len(body) + len(chunk) <= 128:
                    body.extend(chunk)
                sequence = None
                if not message.get("more_body", False):
                    try:
                        candidate = json.loads(body).get("through_sequence")
                        if type(candidate) is int:
                            sequence = candidate
                    except Exception:
                        pass
                record("request-body", **detail, byte_count=len(chunk),
                       more_body=bool(message.get("more_body", False)), through_sequence=sequence)
            return message

        async def traced_send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                record("response", **detail, status=message.get("status"))
            if owner_read and message.get("type") == "http.response.body":
                accepted = None
                try:
                    candidate = json.loads(message.get("body", b"")).get(
                        "owner_read_through_sequence"
                    )
                    if type(candidate) is int:
                        accepted = candidate
                except Exception:
                    pass
                record("read-response-body", **detail, accepted=accepted)
            await send(message)

        try:
            await original_call(self, scope, traced_receive, traced_send)
        finally:
            record("request-return", **detail)
            request_id.reset(token)
            trace_scope.reset(scope_token)

    FastAPI.__call__ = traced_call  # type: ignore[method-assign]
    original_read = ConversationStore._advance_owner_read_through_sequence_sync

    def traced_read(self: Any, conversation_id: str, through_sequence: int) -> Any:
        if not conversation_id.startswith("dot-"):
            return original_read(self, conversation_id, through_sequence)
        selected_scope = trace_scope.get() or current_scope()
        if selected_scope is None:
            return original_read(self, conversation_id, through_sequence)
        previous = getattr(active, "read", None)
        active.read = {"request_id": request_id.get(), "scope": selected_scope,
                       "conversation_id": conversation_id,
                       "through_sequence": through_sequence}
        record("storage-entry", **active.read)
        try:
            result = original_read(self, conversation_id, through_sequence)
            record("storage-return", **active.read,
                   accepted=None if result is None else result.owner_read_through_sequence)
            return result
        except BaseException as error:
            record("storage-error", **active.read, exception_type=type(error).__name__,
                   sqlite_errorcode=getattr(error, "sqlite_errorcode", None))
            raise
        finally:
            active.read = previous

    ConversationStore._advance_owner_read_through_sequence_sync = traced_read  # type: ignore[method-assign]
    original_connect = ConversationStore._connect

    def traced_connect(self: Any) -> Any:
        detail = getattr(active, "read", None)
        if detail:
            record("connect-entry", **detail)
        result = original_connect(self)
        if detail:
            record("connect-return", **detail, connection_id=id(result))
        return result

    ConversationStore._connect = traced_connect  # type: ignore[method-assign]
    original_execute = ChangeSignallingConnection.execute

    def traced_execute(self: Any, sql: str, parameters: Any = (), /) -> Any:
        detail = getattr(active, "read", None)
        if not detail:
            return original_execute(self, sql, parameters)
        words = sql.lstrip().upper().split()
        operation = "BEGIN IMMEDIATE" if words[:2] == ["BEGIN", "IMMEDIATE"] else (
            words[0] if words else "EMPTY"
        )
        if operation == "UPDATE" and "OWNER_READ_THROUGH_SEQUENCE" in sql.upper():
            operation = "UPDATE_OWNER_READ"
        detail = {**detail, "connection_id": id(self), "operation": operation}
        record("sqlite-entry", **detail, in_transaction=self.in_transaction)
        try:
            result = original_execute(self, sql, parameters)
            record("sqlite-return", **detail, in_transaction=self.in_transaction,
                   total_changes=self.total_changes)
            return result
        except BaseException as error:
            record("sqlite-error", **detail, exception_type=type(error).__name__,
                   sqlite_errorcode=getattr(error, "sqlite_errorcode", None))
            raise

    ChangeSignallingConnection.execute = traced_execute  # type: ignore[method-assign]
    original_emit = change_signal.emit

    def traced_emit() -> None:
        detail = getattr(active, "read", None)
        if detail:
            record("change-signal-entry", **detail)
        original_emit()
        if detail:
            record("change-signal-return", **detail)

    change_signal.emit = traced_emit
    record("installed")
