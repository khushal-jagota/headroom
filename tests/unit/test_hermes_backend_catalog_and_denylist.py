"""Acceptance area 4 (folded into contract area 3) — catalog listing payload-as-is,
cut-request rejection, and the standing S1 denylist (plan §8 area 4, §4).

Under D-only-free-hermes-features: generic mediated command execution is CUT. What remains
is a single READ (`list catalog`, payload as-is), the rejection of cut/unknown request
kinds, and the unchanged S1 slash.exec/cli.exec denylist."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import (
    RELAY_LIFECYCLE_DENIED_CODE,
    EmployeeChildRelay,
)
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.minds.fake import FakeGateway, Reply

E1 = "ticket_e1"
HERMES_PY = "/x/hermes-agent/venv/bin/python"

_CATALOG_RESULT = {
    "categories": [{"name": "Configuration", "pairs": [["/compact", "Compress context"]]}],
    "skill_count": 0,
}


def _base_script(**extra: object) -> dict[str, list[Reply]]:
    script: dict[str, list[Reply]] = {
        "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
        "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
        "session.history": [Reply(result={"count": 0, "messages": []})],
    }
    script.update(extra)  # type: ignore[arg-type]
    return script


async def _attach(
    fake: FakeGateway,
) -> tuple[NeutralDownstreamSession, EmployeeChildRelay, EmployeeChildPool, list]:
    loop = asyncio.get_running_loop()
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
    emitted.clear()
    return session, relay, pool, emitted


def test_list_catalog_delivers_payload_as_is() -> None:
    async def body() -> None:
        fake = FakeGateway(_base_script(**{"commands.catalog": [Reply(result=_CATALOG_RESULT)]}))
        session, _relay, pool, emitted = await _attach(fake)
        marker = len(fake.sent)
        await session.handle_neutral_request(nv.ListCatalogRequest(employee_entity_id=E1))
        # The translator issued commands.catalog for the session.
        assert fake.sent[marker]["method"] == "commands.catalog"
        assert fake.sent[marker]["params"] == {"session_id": "sid"}
        # EXACTLY a CatalogResultEvent carrying the native result serialized verbatim.
        catalog_events = [e for e in emitted if isinstance(e, nv.CatalogResultEvent)]
        assert len(catalog_events) == 1
        assert catalog_events[0] == nv.CatalogResultEvent(
            employee_entity_id=E1, payload_json=json.dumps(_CATALOG_RESULT)
        )
        # Payload as-is: the round-tripped payload equals the native result exactly.
        assert json.loads(catalog_events[0].payload_json) == _CATALOG_RESULT
        # NOT a PassthroughEvent, and the raw JSON-RPC result frame was not forwarded.
        assert not any(isinstance(e, nv.PassthroughEvent) for e in emitted)
        assert emitted == catalog_events  # nothing else emitted
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_cut_request_kind_is_rejected_and_never_reaches_child() -> None:
    async def body() -> None:
        fake = FakeGateway(_base_script())
        session, _relay, pool, emitted = await _attach(fake)
        marker = len(fake.sent)

        # A CUT kind (set_model-shaped) and a RUN_COMMAND-shaped kind and an unknown kind.
        cut_envelopes = [
            json.dumps(
                {
                    "neutral": "request",
                    "kind": "set_model",
                    "employee_entity_id": E1,
                    "model": "gpt-5.5",
                }
            ),
            json.dumps(
                {
                    "neutral": "request",
                    "kind": "run_command",
                    "employee_entity_id": E1,
                    "name": "/compact",
                    "args": "",
                }
            ),
            json.dumps(
                {"neutral": "request", "kind": "totally_unknown", "employee_entity_id": E1}
            ),
        ]
        for raw in cut_envelopes:
            await session.handle_neutral_wire_text(raw)

        # Each yields a neutral TurnFailedEvent naming the unrecognized kind.
        failed = [e for e in emitted if isinstance(e, nv.TurnFailedEvent)]
        assert len(failed) == 3
        details = {e.detail for e in failed}
        assert details == {
            "unrecognized request kind: set_model",
            "unrecognized request kind: run_command",
            "unrecognized request kind: totally_unknown",
        }
        assert all(e.reason == nv.TurnFailureReason.agent_error for e in failed)
        # NO native frame was emitted to the child for any of them.
        assert fake.sent[marker:] == []
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_raw_slash_exec_and_cli_exec_still_denied_by_s1_denylist() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])

        for method in ("slash.exec", "cli.exec"):
            forward = relay.handle_downstream_message(
                conn,
                json.dumps(
                    {
                        "relay": "request",
                        "employee_entity_id": E1,
                        "frame": {"jsonrpc": "2.0", "id": 7, "method": method},
                    }
                ),
            )
            # Denied synchronously — no forward coroutine, an error response enqueued.
            assert forward is None
            out = [json.loads(conn.outbound.get_nowait()) for _ in range(conn.outbound.qsize())]
            denied = [
                f for f in out if f.get("error", {}).get("code") == RELAY_LIFECYCLE_DENIED_CODE
            ]
            assert len(denied) == 1, (method, out)
            assert denied[0]["id"] == 7

    asyncio.run(body())
