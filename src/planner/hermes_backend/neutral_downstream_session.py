"""NeutralDownstreamSession: the per-connection translator/adapter (plan §0, §3, §4).

Sits BETWEEN one neutral socket and the SAME S1 `EmployeeChildRelay`, over the relay's
existing public downstream seam. It does not modify the relay's raw path and adds no
`if neutral:` branch inside the relay.

- Inbound (pane->relay): parse a neutral request, translate to native frames, inject each
  as an S1 `{relay:"request", employee_entity_id, frame}` envelope via
  `relay.handle_downstream_message` — so the S1 denylist + id-namespacing apply untouched.
  Subscription is a synthesized `{relay:"subscribe"}` on attach.
- Outbound (relay->pane): drain `conn.outbound`, translate each native frame to a neutral
  event, and hand it to `send_neutral`. Correlated results to translator-issued native RPCs
  (matched by the downstream id it assigned, tracked in `_pending_translator_rpcs`) are
  consumed here and turned into the matching neutral event, never echoed raw.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from planner.files.logic.paths import resolve_chat_file
from planner.hermes_backend import hermes_frame_translation as tr
from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_relay import DownstreamConnection, EmployeeChildRelay

SendNeutral = Callable[[str], Awaitable[None]]

_MANAGED_CHAT_PREFIX = ("", "files", "chats")


class NeutralDownstreamSession:
    def __init__(
        self,
        *,
        relay: EmployeeChildRelay,
        conn: DownstreamConnection,
        send_neutral: SendNeutral,
        db_path: str,
        pool_provider: Callable[[], Any | None] | None = None,
    ) -> None:
        self._relay = relay
        self._conn = conn
        self._send_neutral = send_neutral
        self._db_path = db_path
        self._pool_provider = pool_provider
        self._employee_entity_id: str | None = None
        self._session_id: str | None = None
        # Downstream ids the translator assigns to its OWN native RPCs, negative to stay
        # clear of any pane-facing id space; the response returns correlated on
        # `conn.outbound` with the same id (the S1 relay restores the downstream id).
        self._rpc_id_counter = itertools.count(-1, -1)
        self._pending_translator_rpcs: dict[Any, str] = {}
        # One future per outstanding translator RPC, resolved when its correlated result is
        # drained. This is the single correlation point whether the drain runs in the route's
        # background writer loop (`handle_outbound_text`) or inline (`_await_rpc` self-drains
        # when no background pump owns the queue).
        self._rpc_results: dict[Any, asyncio.Future[None]] = {}
        self._background_pump_running = False

    # --- inbound: neutral request -> native frames --------------------------

    async def handle_neutral_wire_text(self, raw: str) -> None:
        """Parse one neutral request off the wire and dispatch it. A cut/unknown request
        kind fails `from_wire` (`ValueError`); we answer with a neutral error and emit NO
        native frame (plan §4 — the cut surface is closed)."""
        try:
            request = nv.from_wire_text(raw)
            is_request = isinstance(request, _REQUEST_TYPES)
        except (ValueError, KeyError):
            request = None
            is_request = False
        if request is None or not is_request:
            employee, kind = _envelope_employee_and_kind(raw)
            if employee is not None:
                self._employee_entity_id = employee
            await self.reject_unrecognized_request_kind(kind)
            return
        await self.handle_neutral_request(request)  # type: ignore[arg-type]

    async def handle_neutral_request(self, request: nv.NeutralRequest) -> None:
        self._employee_entity_id = request.employee_entity_id
        if isinstance(request, nv.AttachToEmployeeRequest):
            await self._attach(request.employee_entity_id)
            return
        if isinstance(request, nv.NewConversationRequest):
            await self._new_conversation(request.employee_entity_id)
            return
        if isinstance(request, nv.SendMessageRequest) and request.image_refs:
            # Resolve each managed web-relative image ref to a child-openable ABSOLUTE path
            # BEFORE building the native plan (translator stays pure). On ANY failure the
            # WHOLE send is rejected with a neutral error — nothing (not even prompt.submit)
            # reaches the child (defect #7 / contract lines 75-79).
            resolved = await self._resolve_image_refs(
                request.employee_entity_id, request.image_refs
            )
            if resolved is None:
                return
            request = dataclasses.replace(request, image_refs=resolved)
        session_id = self._require_session_id()
        plans = tr.neutral_request_to_native_frames(request, session_id=session_id)
        for plan in plans:
            downstream_id = await self._inject(plan)
            if plan.kind:
                # A tracked RPC (send/answer/approval/interrupt ACK, compact ACK, list
                # catalog): its correlated result is consumed here — an ACK error surfaces as
                # a TurnFailedEvent, a catalog result becomes a payload-as-is CatalogResultEvent.
                await self._await_rpc(downstream_id)

    async def _resolve_image_refs(
        self, employee_entity_id: str, image_refs: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        """Resolve each managed chat image ref to a child-openable absolute path. Returns the
        resolved tuple, or None after emitting a `TurnFailedEvent` on the first failure (the
        caller then sends nothing). Mirrors the legacy `_resolve_turn_image` parse/canonicalize
        (chat/service.py) using the PUBLIC framework-free `resolve_chat_file` seam — chat/ stays
        call-only and its private resolver is never imported."""
        resolved: list[str] = []
        for ref in image_refs:
            absolute = self._resolve_one_image_ref(employee_entity_id, ref)
            if absolute is None:
                await self._emit(
                    nv.TurnFailedEvent(
                        employee_entity_id=employee_entity_id,
                        reason=nv.TurnFailureReason.agent_error,
                        detail=f"unresolvable image reference: {ref}",
                    )
                )
                return None
            resolved.append(absolute)
        return tuple(resolved)

    def _resolve_one_image_ref(self, entity_id: str, ref: str) -> str | None:
        parsed = urlsplit(ref)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        parts = parsed.path.split("/")
        if len(parts) < 5 or parts[:4] != [*_MANAGED_CHAT_PREFIX, entity_id]:
            return None
        relative_path = unquote("/".join(parts[4:]))
        try:
            chat_file = resolve_chat_file(self._db_path, entity_id, relative_path)
        except ValueError:
            return None
        canonical_reference = (
            f"/files/chats/{quote(chat_file.entity_id, safe='')}/"
            f"{quote(chat_file.relative_path, safe='/')}"
        )
        if ref != canonical_reference:
            return None
        return str(chat_file.absolute_path)

    async def reject_unrecognized_request_kind(self, kind: str) -> None:
        """A downstream sent an envelope naming a cut/unknown request kind (`from_wire`
        rejected it). Answer with a neutral error; emit NO native frame to the child (plan
        §4 — the cut surface is closed)."""
        await self._emit(
            nv.TurnFailedEvent(
                employee_entity_id=self._employee_entity_id or "",
                reason=nv.TurnFailureReason.agent_error,
                detail=f"unrecognized request kind: {kind}",
            )
        )

    async def _attach(self, employee_entity_id: str) -> None:
        # (1) Bootstrap the live session_id via session.active_list FIRST — BEFORE
        # subscribing. For a COLD child, this `_inject` triggers the pool's spawn; the pool's
        # own session.create/session.resume response would fan out to a downstream that was
        # already subscribed and become an empty-type PassthroughEvent (defect #1). Bootstrap
        # first drains that pool session RPC (it is correlated to THIS conn, not fanned out)
        # while we are not yet a fan-out subscriber, so it never leaks to the pane. Block
        # until the correlated active_list result lands (the child answers off-loop).
        active_id = await self._inject(
            tr.NativeRequestPlan(tr.NATIVE_SESSION_ACTIVE_LIST, {}, tr.RpcKind.ACTIVE_LIST)
        )
        await self._await_rpc(active_id)
        # (2) NOW subscribe — through a synthesized {relay:"subscribe"} envelope via
        # handle_downstream_message (plan §0/§3 require the S1 subscribe seam, not a direct
        # relay.subscribe call).
        subscribe_envelope = json.dumps(
            {"relay": "subscribe", "employee_entity_ids": [employee_entity_id]}
        )
        forward = self._relay.handle_downstream_message(self._conn, subscribe_envelope)
        if forward is not None:
            await forward
        # (3) Issue the durable-session history snapshot. (No attach-time catalog fetch: the
        # vet cache it seeded is cut under D-only-free-hermes-features; the catalog is now a
        # single on-demand `list catalog` read.)
        history_id = await self._inject(
            tr.NativeRequestPlan(
                tr.NATIVE_SESSION_HISTORY,
                {"session_id": self._require_session_id()},
                tr.RpcKind.HISTORY,
            )
        )
        await self._await_rpc(history_id)

    async def _new_conversation(self, employee_entity_id: str) -> None:
        # New-conversation is an EMPLOYEE LIFECYCLE op the POOL services (its own
        # session.create is on the downstream denylist). The pool CLOSES the old live
        # session and binds a fresh one, RETURNS the new ids; the session bootstraps the
        # returned live id directly and issues session.history -> an EMPTY snapshot.
        pool = self._pool_provider() if self._pool_provider is not None else None
        if pool is None:
            await self._emit(
                nv.TurnFailedEvent(
                    employee_entity_id=employee_entity_id,
                    reason=nv.TurnFailureReason.agent_error,
                    detail="new-conversation unavailable",
                )
            )
            return
        if self._session_id is None:
            await self._emit(
                nv.TurnFailedEvent(
                    employee_entity_id=employee_entity_id,
                    reason=nv.TurnFailureReason.agent_error,
                    detail="new-conversation requires a prior attach",
                )
            )
            return
        # Run the blocking rebind OFF the WS event loop, passing the OLD live id we hold.
        loop = asyncio.get_running_loop()
        new_live_id, _new_stored = await loop.run_in_executor(
            pool.init_executor,
            pool.rebind_fresh_session,
            employee_entity_id,
            self._require_session_id(),
        )
        # Bootstrap the RETURNED live id directly (not an ambiguous active_list).
        self._session_id = new_live_id
        history_id = await self._inject(
            tr.NativeRequestPlan(
                tr.NATIVE_SESSION_HISTORY,
                {"session_id": new_live_id},
                tr.RpcKind.HISTORY,
            )
        )
        await self._await_rpc(history_id)

    async def _inject(self, plan: tr.NativeRequestPlan) -> int:
        """Inject one native frame through the S1 relay seam; return the downstream id the
        translator assigned (correlates the response when the plan is a tracked RPC)."""
        employee_entity_id = self._require_employee()
        downstream_id = next(self._rpc_id_counter)
        if plan.kind:
            self._pending_translator_rpcs[downstream_id] = plan.kind
            self._rpc_results[downstream_id] = asyncio.get_running_loop().create_future()
        envelope = json.dumps(
            {
                "relay": "request",
                "employee_entity_id": employee_entity_id,
                "frame": {
                    "jsonrpc": "2.0",
                    "id": downstream_id,
                    "method": plan.method,
                    "params": plan.params,
                },
            }
        )
        forward = self._relay.handle_downstream_message(self._conn, envelope)
        if forward is not None:
            await forward
        return downstream_id

    async def _await_rpc(self, downstream_id: int) -> None:
        """Await the correlated result for a tracked RPC. When the route owns a background
        writer/drain loop, that loop consumes `conn.outbound` and resolves the future, so we
        only await it. When no background pump is running (inline/test use), we self-drain
        `conn.outbound` until the future resolves — every other frame drained on the way is
        translated in order (a child event interleaved before the reply still reaches the
        pane)."""
        future = self._rpc_results.get(downstream_id)
        if future is None:
            return
        if self._background_pump_running:
            await future
            return
        while not future.done():
            text = await self._conn.outbound.get()
            await self._handle_outbound_text(text)

    def enable_background_pump(self) -> None:
        """Declare that a background writer/drain loop (the route's) owns `conn.outbound`, so
        `_await_rpc` awaits futures instead of self-draining (avoids two consumers racing)."""
        self._background_pump_running = True

    async def handle_outbound_text(self, text: str) -> None:
        """Public single-frame outbound handler for the route's writer loop."""
        await self._handle_outbound_text(text)

    # --- outbound: native frames -> neutral events --------------------------

    async def drain_pending_outbound(self) -> None:
        """Drain every currently-queued `conn.outbound` frame, translating each. Non-blocking
        past the currently-buffered frames (used inline/in tests to flush buffered frames)."""
        while True:
            try:
                text = self._conn.outbound.get_nowait()
            except asyncio.QueueEmpty:
                return
            await self._handle_outbound_text(text)

    async def _handle_outbound_text(self, text: str) -> None:
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(frame, dict):
            return
        frame_id = frame.get("id")
        has_body = "result" in frame or "error" in frame
        if has_body and frame_id in self._pending_translator_rpcs:
            kind = self._pending_translator_rpcs.pop(frame_id)
            await self._consume_correlated_rpc(kind, frame)
            future = self._rpc_results.pop(frame_id, None)
            if future is not None and not future.done():
                future.set_result(None)
            return
        if has_body:
            # A body-bearing frame (result/error) that is NOT one of THIS session's tracked
            # translator RPCs is an uncorrelated RPC response the relay fanned out to every
            # subscriber — the pool's OWN session.close/session.create during a `/new` rebind
            # (it issues those on the child transport with an int id but registers no
            # downstream PendingForward, so deliver_child_frame fans the response out).
            # These are lifecycle plumbing, not a child event; DROP them so a `/new` never
            # renders a stray passthrough row on another attached pane. Real child events all
            # carry `params.type` and have no result/error body, so nothing legitimate is lost.
            return
        event = tr.native_frame_to_neutral_event(self._require_employee(), frame)
        if isinstance(event, nv.ChildResetEvent):
            # A respawn invalidates the cached live session id; the next attach
            # re-bootstraps (plan §3 live-session-id note).
            self._session_id = None
        await self._emit(event)

    async def _consume_correlated_rpc(self, kind: str, frame: dict[str, Any]) -> None:
        employee_entity_id = self._require_employee()
        error = frame.get("error")
        if isinstance(error, dict):
            await self._emit(
                tr.native_error_to_turn_failed(
                    employee_entity_id,
                    code=int(error.get("code", 0)),
                    message=str(error.get("message", "")),
                )
            )
            return
        result = frame.get("result")
        result = result if isinstance(result, dict) else {}
        if kind == tr.RpcKind.ACTIVE_LIST:
            self._session_id = _sole_session_id(result)
            return
        if kind == tr.RpcKind.HISTORY:
            await self._emit(_history_snapshot(employee_entity_id, result))
            return
        if kind == tr.RpcKind.CATALOG:
            # The commands.catalog result is delivered PAYLOAD AS-IS (no normalization) —
            # a distinct CatalogResultEvent carrying the native result serialized verbatim.
            await self._emit(
                nv.CatalogResultEvent(
                    employee_entity_id=employee_entity_id,
                    payload_json=json.dumps(result),
                )
            )
            return
        if kind == tr.RpcKind.ACK:
            # A successful send/answer/approval/interrupt/compact carries nothing the pane
            # needs (the real turn output flows back as event frames). A native error already
            # took the error branch above and surfaced as a TurnFailedEvent (e.g. compact's
            # 4009 -> busy_already_running).
            return

    async def _emit(self, event: nv.NeutralEvent) -> None:
        await self._send_neutral(nv.to_wire_text(event))

    # --- helpers ------------------------------------------------------------

    def _require_employee(self) -> str:
        if self._employee_entity_id is None:
            raise RuntimeError("neutral session used before attach")
        return self._employee_entity_id

    def _require_session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError("neutral session id not bootstrapped")
        return self._session_id


# The concrete request dataclasses (a runtime isinstance tuple — `nv.NeutralRequest` is a
# union type, not usable with isinstance directly).
_REQUEST_TYPES = (
    nv.AttachToEmployeeRequest,
    nv.SendMessageRequest,
    nv.AnswerQuestionRequest,
    nv.RespondToApprovalRequest,
    nv.InterruptRequest,
    nv.CompactRequest,
    nv.ListCatalogRequest,
    nv.NewConversationRequest,
)


def _envelope_employee_and_kind(raw: str) -> tuple[str | None, str]:
    """Best-effort extract of `employee_entity_id` + `kind` from a rejected wire envelope,
    for the neutral error detail. Returns (employee_or_None, kind_or_"<unparseable>")."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "<unparseable>"
    if not isinstance(parsed, dict):
        return None, "<unparseable>"
    employee = parsed.get("employee_entity_id")
    kind = parsed.get("kind")
    return (
        employee if isinstance(employee, str) else None,
        str(kind) if kind is not None else "<missing>",
    )


def _sole_session_id(result: dict[str, Any]) -> str | None:
    sessions = result.get("sessions")
    if isinstance(sessions, list) and sessions:
        first = sessions[0]
        if isinstance(first, dict):
            session_id = first.get("id")
            if isinstance(session_id, str) and session_id:
                return session_id
    return None


def _history_snapshot(
    employee_entity_id: str, result: dict[str, Any]
) -> nv.HistorySnapshotEvent:
    messages: list[nv.NeutralHistoryMessage] = []
    rows = result.get("messages")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                messages.append(_history_row_to_message(row))
    return nv.HistorySnapshotEvent(
        employee_entity_id=employee_entity_id, messages=tuple(messages)
    )


def _history_row_to_message(row: dict[str, Any]) -> nv.NeutralHistoryMessage:
    """Translate one native `_history_to_messages` row (server.py). A tool row is
    `{"role":"tool","name":...,"context":...}` (server.py:4930) — its text is `context`
    and its tool name is `name`; every other role carries `content` (text rows)."""
    role = str(row.get("role", ""))
    if role == "tool":
        name = row.get("name")
        return nv.NeutralHistoryMessage(
            role=role,
            text=str(row.get("context", "")),
            tool_name=str(name) if isinstance(name, str) else None,
        )
    tool_name = row.get("tool_name")
    return nv.NeutralHistoryMessage(
        role=role,
        text=str(row.get("content", row.get("text", ""))),
        tool_name=str(tool_name) if isinstance(tool_name, str) else None,
    )
