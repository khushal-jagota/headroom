"""Acceptance area 5 — history snapshot on attach (plan §8 area 5).

Attach yields the durable session's translated messages via `session.history` (NOT the
denylisted `session.resume`), sourced from the pool child's session (never Panels DB)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.minds.fake import FakeGateway, Reply

E1 = "ticket_e1"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


def test_attach_yields_translated_durable_session_messages() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
                "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
                "session.history": [
                    Reply(
                        result={
                            "count": 3,
                            "messages": [
                                {"role": "user", "content": "q"},
                                # A native tool row (server.py:4930 shape) carries name/context,
                                # NOT content/tool_name — the translator must read those keys.
                                {"role": "tool", "name": "bash", "context": "ran ls"},
                                {"role": "assistant", "content": "a"},
                            ],
                        }
                    )
                ],
            }
        )
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=fake.spawn,
        )
        holder["pool"] = pool
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        conn = relay.register_downstream()
        emitted: list = []

        async def sink(text: str) -> None:
            emitted.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink, db_path="/tmp/neutral-test.db"
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))

        # The translator issued session.history and did NOT issue the denylisted session.resume.
        methods = fake.sent_methods()
        assert "session.history" in methods
        assert "session.resume" not in methods

        snapshots = [e for e in emitted if isinstance(e, nv.HistorySnapshotEvent)]
        assert len(snapshots) == 1
        assert snapshots[0] == nv.HistorySnapshotEvent(
            employee_entity_id=E1,
            messages=(
                nv.NeutralHistoryMessage(role="user", text="q", tool_name=None),
                nv.NeutralHistoryMessage(role="tool", text="ran ls", tool_name="bash"),
                nv.NeutralHistoryMessage(role="assistant", text="a", tool_name=None),
            ),
        )
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_cold_attach_does_not_leak_pool_session_rpc_and_subscribes_via_envelope() -> None:
    # Defect #1: for a COLD child (NOT pre-spawned) the attach must bootstrap
    # session.active_list BEFORE subscribing, so the pool's own session.create response
    # (correlated to this conn) is drained while we are not yet a fan-out subscriber and
    # never becomes an empty-type PassthroughEvent. And the subscribe must ride the S1
    # {relay:"subscribe"} envelope, not a direct relay.subscribe call.
    async def body() -> None:
        loop = asyncio.get_running_loop()
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
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=fake.spawn,
        )
        holder["pool"] = pool
        # COLD: do NOT call child_for_employee — the attach itself triggers the spawn.
        conn = relay.register_downstream()
        emitted: list = []

        async def sink(text: str) -> None:
            emitted.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink, db_path="/tmp/neutral-test.db"
        )
        # Attach is not subscribed yet at construction time.
        assert conn.subscribed_employee_entity_ids == set()
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        # The pool's session.create RPC did NOT leak as an (empty-type) PassthroughEvent.
        assert not any(isinstance(e, nv.PassthroughEvent) for e in emitted)
        # The subscribe rode the S1 envelope: the conn is now subscribed to E1.
        assert conn.subscribed_employee_entity_ids == {E1}
        # And the attach still produced the history snapshot.
        assert any(isinstance(e, nv.HistorySnapshotEvent) for e in emitted)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())
