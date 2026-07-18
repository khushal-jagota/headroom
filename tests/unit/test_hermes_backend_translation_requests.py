"""Acceptance area 3 — neutral request -> native RPC sequence (plan §8 area 3).

Each neutral request produces exactly the mapped native RPC sequence with exact params.
Drives the full session->relay->pool->fake-child composition and inspects the frames the
fake child received (`fake.sent`), and the neutral events the session emitted."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from time import monotonic as _monotonic

from planner.core.db import connect, create_schema
from planner.files.logic.paths import prepare_chat_entity_files_directory
from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.minds.fake import FakeGateway, Reply

E1 = "ticket_e1"
HERMES_PY = "/x/hermes-agent/venv/bin/python"
_DUMMY_DB_PATH = "/tmp/neutral-req-test/planning.db"


class _Harness:
    def __init__(self, fake: FakeGateway) -> None:
        self.fake = fake
        self.emitted: list[object] = []
        self.pool: EmployeeChildPool | None = None

    async def sink(self, text: str) -> None:
        self.emitted.append(nv.from_wire_text(text))


def _base_script(**extra: object) -> dict[str, list[Reply]]:
    # Attach issues session.active_list + session.history only (no attach-time catalog fetch
    # under D-only-free-hermes-features).
    script: dict[str, list[Reply]] = {
        "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
        "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
        "session.history": [Reply(result={"count": 0, "messages": []})],
    }
    script.update(extra)  # type: ignore[arg-type]
    return script


async def _attached_session(
    harness: _Harness, *, db_path: str = _DUMMY_DB_PATH
) -> tuple[NeutralDownstreamSession, EmployeeChildRelay]:
    loop = asyncio.get_running_loop()
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        relay=relay,
        loop=loop,
        spawn=harness.fake.spawn,
    )
    holder["pool"] = pool
    harness.pool = pool
    await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
    conn = relay.register_downstream()
    session = NeutralDownstreamSession(
        relay=relay, conn=conn, send_neutral=harness.sink, db_path=db_path
    )
    await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
    harness.emitted.clear()
    return session, relay


def _managed_image(tmp_path: Path, entity_id: str, filename: str) -> str:
    """Create a real managed chat image and return its absolute filesystem path (a str db
    path is set up under tmp_path so resolve_chat_file can canonicalize it)."""
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    entity_dir = prepare_chat_entity_files_directory(str(db_path), entity_id)
    image_path = entity_dir / filename
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(db_path)


def _methods_since(harness: _Harness, marker: int) -> list[str]:
    return [str(f.get("method")) for f in harness.fake.sent[marker:]]


def test_each_request_produces_mapped_native_sequence(tmp_path: Path) -> None:
    async def body() -> None:
        # Two REAL managed chat images; their web-relative refs must resolve to child-openable
        # ABSOLUTE filesystem paths before image.attach (defect #7).
        db_path = _managed_image(tmp_path, E1, "a.png")
        _managed_image(tmp_path, E1, "b.png")  # same db dir
        from planner.files.logic.paths import resolve_chat_file

        abs_a = str(resolve_chat_file(db_path, E1, "a.png").absolute_path)
        abs_b = str(resolve_chat_file(db_path, E1, "b.png").absolute_path)
        harness = _Harness(
            FakeGateway(
                _base_script(
                    **{
                        "image.attach": [
                            Reply(result={"ok": True}),
                            Reply(result={"ok": True}),
                        ],
                        "prompt.submit": [Reply(result={"queued": True})],
                    }
                )
            )
        )
        session, _relay = await _attached_session(harness, db_path=db_path)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(
            nv.SendMessageRequest(
                employee_entity_id=E1,
                text="hello",
                image_refs=(f"/files/chats/{E1}/a.png", f"/files/chats/{E1}/b.png"),
            )
        )
        assert _methods_since(harness, marker) == [
            "image.attach",
            "image.attach",
            "prompt.submit",
        ]
        image_frames = [f for f in harness.fake.sent[marker:] if f["method"] == "image.attach"]
        # Each image.attach carries the RESOLVED absolute path, not the web-relative ref.
        assert image_frames[0]["params"] == {"session_id": "sid", "path": abs_a}
        assert image_frames[1]["params"] == {"session_id": "sid", "path": abs_b}
        submit = [f for f in harness.fake.sent[marker:] if f["method"] == "prompt.submit"][0]
        # EXACTLY {session_id, text} — no model/effort key (amended-contract negative assert).
        assert submit["params"] == {"session_id": "sid", "text": "hello"}
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_unresolvable_image_ref_rejects_whole_send_with_zero_child_frames(tmp_path: Path) -> None:
    async def body() -> None:
        # An unresolvable ref (wrong entity / non-managed shape) → neutral error and NOTHING
        # reaches the child — not even prompt.submit (defect #7 / contract lines 75-79).
        db_path = _managed_image(tmp_path, E1, "real.png")
        harness = _Harness(FakeGateway(_base_script()))
        session, _relay = await _attached_session(harness, db_path=db_path)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(
            nv.SendMessageRequest(
                employee_entity_id=E1,
                text="hello",
                image_refs=(f"/files/chats/{E1}/does-not-exist.png",),
            )
        )
        failed = [e for e in harness.emitted if isinstance(e, nv.TurnFailedEvent)]
        assert len(failed) == 1
        assert failed[0].reason == nv.TurnFailureReason.agent_error
        # ZERO native frames reached the child (no image.attach, no prompt.submit).
        assert harness.fake.sent[marker:] == []
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_answer_maps_to_clarify_respond_with_request_id_and_answer() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(_base_script(**{"clarify.respond": [Reply(result={"ok": True})]}))
        )
        session, _relay = await _attached_session(harness)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(
            nv.AnswerQuestionRequest(employee_entity_id=E1, request_id="r1", answer="yes")
        )
        frame = harness.fake.sent[marker]
        assert frame["method"] == "clarify.respond"
        assert frame["params"] == {"session_id": "sid", "request_id": "r1", "answer": "yes"}
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_approval_maps_to_approval_respond_with_choice_and_all() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(_base_script(**{"approval.respond": [Reply(result={"ok": True})]}))
        )
        session, _relay = await _attached_session(harness)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(
            nv.RespondToApprovalRequest(
                employee_entity_id=E1, request_id="r2", decision="approve", apply_to_all=True
            )
        )
        frame = harness.fake.sent[marker]
        assert frame["method"] == "approval.respond"
        assert frame["params"] == {
            "session_id": "sid",
            "request_id": "r2",
            "choice": "approve",
            "all": True,
        }
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_interrupt_maps_to_session_interrupt() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(_base_script(**{"session.interrupt": [Reply(result={"ok": True})]}))
        )
        session, _relay = await _attached_session(harness)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(nv.InterruptRequest(employee_entity_id=E1))
        frame = harness.fake.sent[marker]
        assert frame["method"] == "session.interrupt"
        assert frame["params"] == {"session_id": "sid"}
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_send_message_carries_no_model_field() -> None:
    field_names = {f.name for f in dataclasses.fields(nv.SendMessageRequest)}
    assert "model" not in field_names
    assert "effort" not in field_names
    assert field_names == {"employee_entity_id", "text", "image_refs"}


def test_compact_maps_to_session_compress() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(_base_script(**{"session.compress": [Reply(result={"ok": True})]}))
        )
        session, _relay = await _attached_session(harness)
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(nv.CompactRequest(employee_entity_id=E1))
        methods = _methods_since(harness, marker)
        assert methods == ["session.compress"]
        assert "command.dispatch" not in methods
        assert "slash.exec" not in methods
        frame = harness.fake.sent[marker]
        assert frame["params"] == {"session_id": "sid"}
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_compact_mid_turn_rejection_surfaces_as_failure() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(
                _base_script(
                    **{"session.compress": [Reply(error=(4009, "session busy — /interrupt"))]}
                )
            )
        )
        session, _relay = await _attached_session(harness)
        await session.handle_neutral_request(nv.CompactRequest(employee_entity_id=E1))
        failed = [e for e in harness.emitted if isinstance(e, nv.TurnFailedEvent)]
        assert len(failed) == 1
        assert failed[0].reason == nv.TurnFailureReason.busy_already_running
        assert failed[0].detail == "session busy — /interrupt"
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_mid_turn_send_is_legal_and_translates_as_is() -> None:
    async def body() -> None:
        # A running session does NOT change the emitted native sequence — no synthetic busy.
        harness = _Harness(
            FakeGateway(_base_script(**{"prompt.submit": [Reply(result={"queued": True})]}))
        )
        session, relay = await _attached_session(harness)
        # Simulate a live turn already running by fanning a message.start to the pane.
        from planner.minds.fake import ev

        generation = next(iter(relay._children))
        relay.deliver_child_frame(generation, ev("message.start", "sid"))
        await session.drain_pending_outbound()
        marker = len(harness.fake.sent)
        await session.handle_neutral_request(
            nv.SendMessageRequest(employee_entity_id=E1, text="mid", image_refs=())
        )
        assert _methods_since(harness, marker) == ["prompt.submit"]
        assert harness.fake.sent[marker]["params"] == {"session_id": "sid", "text": "mid"}
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())


def test_legacy_4009_on_prompt_maps_to_busy_already_running_completeness_case() -> None:
    async def body() -> None:
        harness = _Harness(
            FakeGateway(
                _base_script(
                    **{"prompt.submit": [Reply(error=(4009, "subagent still running"))]}
                )
            )
        )
        session, _relay = await _attached_session(harness)
        # prompt.submit is not a tracked RPC; its error returns correlated (id-bearing) on
        # conn.outbound, so the session translates it as a native error frame.
        await session.handle_neutral_request(
            nv.SendMessageRequest(employee_entity_id=E1, text="hi", image_refs=())
        )
        failed = [e for e in harness.emitted if isinstance(e, nv.TurnFailedEvent)]
        assert len(failed) == 1
        assert failed[0].reason == nv.TurnFailureReason.busy_already_running
        harness.pool.shutdown(deadline=_monotonic() + 2.0)  # type: ignore[union-attr]

    asyncio.run(body())
