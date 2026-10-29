"""Temporary owner authority for one exact destination-private catalog comparison."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution.contracts import (
    CatalogExpectedContext,
    CatalogRequest,
    CatalogWorkspaceBinding,
    ModelSelection,
    SelectionKind,
)
from planner.conversation.model_resolution.diagnostics import (
    DiagnosticSink,
    unavailable_record,
    validate_record,
)
from planner.executor.contracts import (
    CatalogDiagnosticArm,
    CatalogDiagnosticResult,
    ExecutorIdentity,
    FrameWriter,
    JsonObject,
)
from planner.executor.wire import parse_identity
from planner.tickets.contracts import TicketLocale

DIAGNOSTIC_MAX_BYTES = 4096
DIAGNOSTIC_LIFETIME_SECONDS = 60.0
DIAGNOSTIC_TICKET_ID = "t_mw0fvv1n"
DIAGNOSTIC_WORKSPACE = Path("/Users/khushaljagota/panels-dogfood-t_0471dgaa")


def bounded_payload(payload: JsonObject) -> None:
    if len(json.dumps(payload, ensure_ascii=True).encode()) > DIAGNOSTIC_MAX_BYTES:
        raise ValueError("catalog diagnostic payload exceeds limit")


def arm_request(payload: JsonObject) -> tuple[str, CatalogRequest]:
    bounded_payload(payload)
    arm = TypeAdapter(CatalogDiagnosticArm).validate_json(json.dumps(payload), strict=True)
    UUID(arm.request_id)
    # Decode via JSON because the existing catalog contract contains native Paths/enums.
    request = TypeAdapter(CatalogRequest).validate_json(
        json.dumps(
            {
                "backend_key": arm.backend_key,
                "expected_context": arm.expected_context,
            }
        ),
        strict=True,
    )
    if set(arm.expected_context) != {"executor_identity", "workspace"}:
        raise ValueError("invalid catalog diagnostic context fields")
    workspace_value = arm.expected_context.get("workspace")
    if not isinstance(workspace_value, dict) or set(workspace_value) != {
        "workspace_folder",
        "workspace_path_policy",
        "workspace_home_relative",
    }:
        raise ValueError("invalid catalog diagnostic workspace fields")
    parse_identity(arm.expected_context.get("executor_identity"))
    if request.backend_key is not ConversationBackendKey.claude:
        raise ValueError("catalog diagnostic backend mismatch")
    require_workspace(request.expected_context.workspace)
    return arm.request_id, request


def require_workspace(workspace: CatalogWorkspaceBinding) -> None:
    if (
        workspace.workspace_folder != DIAGNOSTIC_WORKSPACE
        or workspace.workspace_path_policy is not WorkspacePathPolicy.native_absolute
        or workspace.workspace_home_relative is not None
    ):
        raise ValueError("catalog diagnostic workspace mismatch")


def require_selection(
    ticket_id: str, selection: ModelSelection, workspace: CatalogWorkspaceBinding
) -> None:
    require_workspace(workspace)
    if (
        ticket_id != DIAGNOSTIC_TICKET_ID
        or selection.destination is not TicketLocale.macos
        or selection.backend_key is not ConversationBackendKey.claude
        or selection.submitted_token != "opus"
        or selection.selection_kind is not SelectionKind.legacy_unclassified
        or selection.reasoning_effort is not None
    ):
        raise ValueError("catalog diagnostic selection mismatch")


@dataclass(slots=True, repr=False)
class DiagnosticReservation:
    token: str
    request_id: str
    selection: ModelSelection
    workspace: CatalogWorkspaceBinding
    identity: ExecutorIdentity
    writer: FrameWriter
    expires: float
    consumed: bool = False
    dispatched: bool = False

    @classmethod
    def create(
        cls,
        ticket_id: str,
        selection: ModelSelection,
        workspace: CatalogWorkspaceBinding,
        endpoint: Any,
    ) -> DiagnosticReservation:
        require_selection(ticket_id, selection, workspace)
        if endpoint is None:
            raise ValueError("catalog diagnostic destination is unavailable")
        identity, writer = endpoint.identity, endpoint.writer
        if identity is None or writer is None or not endpoint.ready or writer.is_closing():
            raise ValueError("catalog diagnostic destination is unavailable")
        parse_identity(TypeAdapter(ExecutorIdentity).dump_python(identity, mode="json"))
        return cls(
            str(uuid4()),
            str(uuid4()),
            selection,
            workspace,
            identity,
            writer,
            time.monotonic() + DIAGNOSTIC_LIFETIME_SECONDS,
        )

    def check(
        self, selection: ModelSelection, workspace: CatalogWorkspaceBinding, endpoint: Any
    ) -> None:
        if (
            time.monotonic() >= self.expires
            or self.selection != selection
            or self.workspace != workspace
            or endpoint.identity != self.identity
            or endpoint.writer is not self.writer
            or not endpoint.ready
            or self.writer.is_closing()
        ):
            raise ValueError("catalog diagnostic reservation changed or expired")

    def operational_result(self) -> JsonObject:
        context = CatalogExpectedContext(self.identity, self.workspace)
        return {
            "reservation": self.token,
            "arm": {
                "request_id": self.request_id,
                "backend_key": "claude",
                "expected_context": TypeAdapter(CatalogExpectedContext).dump_python(
                    context,
                    mode="json",
                ),
            },
        }


_current_reservation: ContextVar[DiagnosticReservation | None] = ContextVar(
    "catalog_diagnostic_reservation",
    default=None,
)


def current_reservation() -> DiagnosticReservation | None:
    return _current_reservation.get()


@contextmanager
def reservation_scope(reservation: DiagnosticReservation) -> Iterator[None]:
    token = _current_reservation.set(reservation)
    try:
        yield
    finally:
        _current_reservation.reset(token)


class NativeCatalogDiagnostics:
    """One arm lifecycle per process incarnation, with one finite private receipt."""

    def __init__(self) -> None:
        self.state = "unused"
        self.request_id: str | None = None
        self.request: CatalogRequest | None = None
        self.sink: DiagnosticSink | None = None
        self.writer: FrameWriter | None = None
        self.receipt: JsonObject | None = None
        self.expires = 0.0
        self._timer: asyncio.TimerHandle | None = None
        self.invalidated = False

    def _expire(self) -> None:
        if time.monotonic() < self.expires:
            return
        if self.state == "armed":
            self.invalidate()
        elif self.state == "settled":
            self.receipt = None
            self.request = None
            self.request_id = None
            self.state = "consumed"

    def _deadline(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self.expires = time.monotonic() + DIAGNOSTIC_LIFETIME_SECONDS
        self._timer = asyncio.get_running_loop().call_later(
            DIAGNOSTIC_LIFETIME_SECONDS,
            self._expire,
        )

    def arm(
        self,
        payload: JsonObject,
        identity: ExecutorIdentity,
        writer: FrameWriter,
    ) -> JsonObject:
        self._expire()
        request_id, request = arm_request(payload)
        if self.state != "unused":
            raise ValueError("catalog diagnostic lifecycle is already used")
        if request.expected_context.executor_identity != identity:
            raise ValueError("catalog diagnostic identity mismatch")
        self.request_id, self.request = request_id, request
        self.writer = writer
        self.state = "armed"
        self._deadline()
        return {"armed": True}

    def begin(
        self,
        request_id: str,
        request: CatalogRequest,
        writer: FrameWriter,
    ) -> DiagnosticSink | None:
        self._expire()
        if (
            self.state != "armed"
            or self.request_id != request_id
            or self.request != request
            or self.writer is not writer
        ):
            return None
        self.state = "active"
        if self._timer is not None:
            self._timer.cancel()
        try:
            self.sink = DiagnosticSink()
        except Exception:
            # An admitted diagnostic never changes ordinary provider behavior.
            self.sink = None
        return self.sink

    def cancel(self, request_id: str, request: CatalogRequest, writer: FrameWriter) -> None:
        if self.request_id == request_id and self.request == request and self.writer is writer:
            self.invalidate()

    def invalidate(self) -> None:
        if self.state == "armed":
            self.state = "settled"
            self.request = None
            self.writer = None
            self.receipt = unavailable_record()
            self._deadline()
        elif self.state == "active":
            self.invalidated = True
        # Active references belong to the physical provider cleanup's finally.

    def finish(self, request_id: str) -> None:
        if self.state != "active" or request_id != self.request_id:
            return
        sink, self.sink = self.sink, None
        try:
            record = sink.finish() if sink is not None else unavailable_record()
            self.receipt = unavailable_record() if self.invalidated else validate_record(record)
            bounded_payload(self.receipt)
        except Exception:
            self.receipt = unavailable_record()
        finally:
            self.request = None
            self.writer = None
            self.state = "settled"
            self._deadline()

    def result(self, payload: JsonObject) -> JsonObject:
        bounded_payload(payload)
        value = TypeAdapter(CatalogDiagnosticResult).validate_json(json.dumps(payload), strict=True)
        UUID(value.request_id)
        self._expire()
        if value.request_id != self.request_id:
            raise ValueError("catalog diagnostic request mismatch")
        if self.state != "settled" or self.receipt is None:
            raise ValueError("catalog diagnostic receipt is unavailable")
        receipt, self.receipt = self.receipt, None
        self.state = "consumed"
        self.request_id = None
        if self._timer is not None:
            self._timer.cancel()
        return validate_record(receipt)
