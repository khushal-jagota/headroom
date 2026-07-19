"""Acceptance area 2 — frame -> neutral event translation (plan §8 area 2).

Pure-function tests over `native_frame_to_neutral_event`: each native `params.type` maps
to its exact neutral event; S0-inventoried metadata kinds are explicit passthrough rows;
unknown kinds fall through to passthrough (nothing dropped); every event carries employee
identity. The mixed-burst order test drives the full session outbound loop against a fake
gateway."""

from __future__ import annotations

import asyncio
import json
from time import monotonic as _monotonic

from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.hermes_frame_translation import native_frame_to_neutral_event
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import JsonDict

E1 = "ticket_e1"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


def _translate(frame: JsonDict) -> object:
    return native_frame_to_neutral_event(E1, frame)


def test_each_native_type_maps_to_its_neutral_event() -> None:
    assert _translate(ev("message.start", "sid")) == nv.TurnStartedEvent(employee_entity_id=E1)

    assert _translate(ev("message.delta", "sid", {"text": "hello"})) == (
        nv.AssistantTextDeltaEvent(employee_entity_id=E1, text="hello")
    )
    assert _translate(ev("thinking.delta", "sid", {"text": "hmm"})) == (
        nv.ThinkingDeltaEvent(employee_entity_id=E1, text="hmm")
    )
    assert _translate(ev("reasoning.delta", "sid", {"text": "why"})) == (
        nv.ThinkingDeltaEvent(employee_entity_id=E1, text="why")
    )
    assert _translate(ev("reasoning.available", "sid", {"text": "done thinking"})) == (
        nv.ThinkingDeltaEvent(employee_entity_id=E1, text="done thinking")
    )

    # Tool phases — all three explicit.
    assert _translate(
        ev("tool.start", "sid", {"tool_id": "t9", "name": "bash", "context": "ls -la"})
    ) == nv.ToolActivityEvent(
        employee_entity_id=E1, tool_id="t9", tool_name="bash", phase=nv.ToolPhase.started,
        preview="ls -la",
    )
    assert _translate(ev("tool.generating", "sid", {"name": "bash"})) == nv.ToolActivityEvent(
        employee_entity_id=E1, tool_id="", tool_name="bash", phase=nv.ToolPhase.progress,
        preview="",
    )
    assert _translate(
        ev("tool.complete", "sid", {"tool_id": "t9", "name": "bash", "summary": "42 files"})
    ) == nv.ToolActivityEvent(
        employee_entity_id=E1, tool_id="t9", tool_name="bash", phase=nv.ToolPhase.completed,
        preview="42 files",
    )

    assert _translate(
        ev("clarify.request", "sid", {"request_id": "r1", "question": "which?", "choices": ["a"]})
    ) == nv.AgentQuestionEvent(
        employee_entity_id=E1, request_id="r1", prompt_text="which?", choices=("a",)
    )

    assert _translate(
        ev("approval.request", "sid", {"request_id": "r2", "command": "rm -rf /"})
    ) == nv.ToolApprovalRequestEvent(employee_entity_id=E1, request_id="r2", summary="rm -rf /")

    assert _translate(
        ev("message.complete", "sid", {"text": "final", "status": "complete"})
    ) == nv.TurnCompletedEvent(employee_entity_id=E1, final_text="final")
    # status absent -> completed.
    assert _translate(ev("message.complete", "sid", {"text": "final"})) == (
        nv.TurnCompletedEvent(employee_entity_id=E1, final_text="final")
    )
    assert _translate(
        ev("message.complete", "sid", {"text": "partial", "status": "error"})
    ) == nv.TurnFailedEvent(
        employee_entity_id=E1, reason=nv.TurnFailureReason.agent_error, detail="partial"
    )
    assert _translate(
        ev("message.complete", "sid", {"text": "cut", "status": "interrupted"})
    ) == nv.TurnFailedEvent(
        employee_entity_id=E1, reason=nv.TurnFailureReason.interrupted, detail="cut"
    )

    assert _translate(ev("error", "sid", {"message": "kaboom"})) == nv.TurnFailedEvent(
        employee_entity_id=E1, reason=nv.TurnFailureReason.agent_error, detail="kaboom"
    )

    assert _translate(ev("session.title", "sid", {"title": "Ship it"})) == (
        nv.SessionTitledEvent(employee_entity_id=E1, title="Ship it")
    )

    # S1 synthesized child_reset frame.
    reset = {"relay": "event", "type": "child_reset", "employee_entity_id": E1}
    assert _translate(reset) == nv.ChildResetEvent(employee_entity_id=E1)


def test_s0_inventory_kinds_have_explicit_rows() -> None:
    session_info = ev("session.info", "sid", {"cwd": "/repo"})
    assert _translate(session_info) == nv.PassthroughEvent(
        employee_entity_id=E1,
        native_type="session.info",
        payload_json=json.dumps({"cwd": "/repo"}),
    )
    review = ev("review.summary", "sid", {"text": "..."})
    assert _translate(review) == nv.PassthroughEvent(
        employee_entity_id=E1,
        native_type="review.summary",
        payload_json=json.dumps({"text": "..."}),
    )


def test_status_update_becomes_status_event() -> None:
    ordinary = ev("status.update", "sid", {"kind": "status", "text": "Pondering"})
    assert _translate(ordinary) == nv.StatusEvent(
        employee_entity_id=E1, status_kind="status", text="Pondering"
    )
    compacting = ev("status.update", "sid", {"kind": "compacting", "text": "Summarizing…"})
    assert _translate(compacting) == nv.StatusEvent(
        employee_entity_id=E1, status_kind="compacting", text="Summarizing…"
    )


def test_unknown_native_type_becomes_passthrough() -> None:
    result = _translate(ev("some.future.kind", "sid", {"x": 1}))
    assert result == nv.PassthroughEvent(
        employee_entity_id=E1, native_type="some.future.kind", payload_json='{"x": 1}'
    )


def test_every_event_carries_employee_identity() -> None:
    for frame in (
        ev("message.start", "sid"),  # payload-less
        ev("message.delta", "sid", {"text": "x"}),
        ev("some.unknown.kind", "sid", {"a": 2}),
    ):
        result = _translate(frame)
        assert result.employee_entity_id == E1


def test_mixed_burst_preserves_order() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        events_stream = (
            ev("message.start", "sid"),
            ev("thinking.delta", "sid", {"text": "t"}),
            ev("message.delta", "sid", {"text": "d"}),
            ev("tool.start", "sid", {"tool_id": "z", "name": "bash", "context": "c"}),
            ev("message.complete", "sid", {"text": "done", "status": "complete"}),
        )
        fake = FakeGateway(
            {
                "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
                "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
                "session.history": [Reply(result={"count": 0, "messages": []})],
            }
        )
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        pool = EmployeeChildPool(
            hermes_python=__import__("pathlib").Path(HERMES_PY),
            planner_home=__import__("pathlib").Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=fake.spawn,
        )
        holder["pool"] = pool
        # Spawn the child so a live generation exists to fan the burst from.
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        conn = relay.register_downstream()
        collected: list[object] = []

        async def sink(text: str) -> None:
            collected.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink, db_path="/tmp/neutral-test.db"
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        collected.clear()  # drop the attach-time history snapshot / catalog
        # Fan the burst through the relay to the subscribed conn, then drain the session.
        generation = next(iter(relay._children))
        for frame in events_stream:
            relay.deliver_child_frame(generation, frame)
        await session.drain_pending_outbound()
        kinds = [type(e).__name__ for e in collected]
        assert kinds == [
            "TurnStartedEvent",
            "ThinkingDeltaEvent",
            "AssistantTextDeltaEvent",
            "ToolActivityEvent",
            "TurnCompletedEvent",
        ], kinds
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())
