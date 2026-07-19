"""S3 Wave 1 — the pool-backed step submitter (plan §1, §2, §9.1).

`PoolStepGateway` presents the EXACT interface `EmployeeStepRunner` calls
(`run_ticket_step`/`interrupt`/`status`), but submits the step prompt through the
ticket's pool child and settles by OWNING the terminal from the `prompt.submit` ACK
disposition (Collision #A, ruled A1 — settlement by disposition, no demux).

Scripted-child payload SHAPES are captured from real source WITH CITES (never authored
from memory):
- the `prompt.submit` ACK carries `result["status"]` in {"streaming","queued","steered"}
  — src/planner/minds/sessions/service.py:556 (`disposition = str(result.get("status")...)`),
  submitted as {"session_id","text"} — service.py:507-508.
- a native event frame is {"jsonrpc":"2.0","method":"event","params":{"type","session_id",
  "payload"}} — src/planner/minds/fake.py:17-26 (`ev`), and the sessions reader consumes
  event["session_id"]/["type"]/["payload"] — service.py:948-954.
- the `on_event` shape `observe_worker_gateway_event` consumes is {type,session_id,payload}
  — src/planner/minds/shared_gateway.py:868-873.
- a `message.complete` payload carries status in {complete,interrupted} else errored, and an
  `error` event → errored — src/planner/minds/shared_gateway.py:874-898.
- `session.interrupt` params are {"session_id": <live id>} — service.py:679-681.
- a busy prompt.submit is BUSY_CODE 4009 — src/planner/minds/gateway.py (BUSY_CODE).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from time import monotonic as _monotonic

import pytest

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.pool_step_gateway import PoolStepGateway
from planner.minds.contracts import RunResult
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGatewayBusy

HERMES_PY = "/x/hermes-agent/venv/bin/python"
TICKET = "ticket_x"
LIVE = "live-1"
STORED = "stored-1"

# BUSY_CODE captured from source (src/planner/minds/gateway.py) — the native busy code.
BUSY_CODE = 4009


def _create_reply(sid=LIVE, key=STORED):
    return Reply(result={"session_id": sid, "stored_session_id": key})


def _submit_reply(status: str, *, events_before=(), events_after=(), frames=(), die=False):
    """A prompt.submit ACK carrying the disposition status (service.py:556), optionally preceded
    by predecessor event frames (delivered BEFORE the ACK, i.e. pre-ACK) and/or followed by our
    turn's frames (post-ACK) — all on the SAME child stdout thread, in emission order."""
    return Reply(
        result={"status": status},
        events_before=tuple(events_before),
        events_after=tuple(events_after),
        frames=frames,
        die=die,
    )


def _pool(loop, spawn, *, base_env=None):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env=base_env or {},
        relay=relay,
        loop=loop,
        spawn=spawn,
    )
    holder["pool"] = pool
    return pool, relay


def _gateway(pool):
    return PoolStepGateway(pool=pool)


async def _spawn(pool, employee):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool.init_executor, pool.child_for_employee, employee)


async def _run_step(gateway, **kwargs):
    """Run the step OFF the loop thread (the runner blocks its own worker thread on the
    settlement Event); the loop must stay responsive to deliver child frames."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: gateway.run_ticket_step(**kwargs))


# --- settlement on completed -------------------------------------------------


def test_pool_step_gateway_run_submits_and_settles_on_completed() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.start", LIVE, {}),
                            ev("message.delta", LIVE, {"text": "hel"}),
                            ev("message.delta", LIVE, {"text": "lo"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "hello"}),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)

        seen: list = []
        keys: list = []
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="do the step",
            on_event=seen.append,
            on_session_key=keys.append,
            require_existing_session=True,
        )
        assert isinstance(result, RunResult)
        assert result.status == "complete"
        assert result.text == "hello"
        assert result.session_key == STORED
        # on_session_key fired BEFORE submit (with the resolved stored key).
        assert keys == [STORED]
        # Each streamed frame reached on_event in the {type,session_id,payload} shape.
        types = [e["type"] for e in seen]
        assert types == ["message.start", "message.delta", "message.delta", "message.complete"]
        for e in seen:
            assert set(e.keys()) == {"type", "session_id", "payload"}
            assert e["session_id"] == LIVE
        assert seen[1]["payload"] == {"text": "hel"}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_settles_on_failed() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.start", LIVE, {}),
                            ev("error", LIVE, {"message": "model exploded"}),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="do the step",
            require_existing_session=True,
        )
        assert result.status == "errored"
        assert result.error == "model exploded"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_settles_on_child_reset() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                # The ACK arrives, one frame streams, then the child dies mid-turn (EOF).
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(ev("message.start", LIVE, {}),),
                        die=True,
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="do the step",
            require_existing_session=True,
        )
        assert result.status == "errored"
        assert result.error is not None
        assert "child reset" in result.error
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_busy_raises_shared_gateway_busy() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [Reply(error=(BUSY_CODE, "busy"))],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        with pytest.raises(SharedGatewayBusy) as excinfo:
            await _run_step(
                gateway,
                session_key=STORED,
                entity_id=TICKET,
                prompt_text="do the step",
                require_existing_session=True,
            )
        assert excinfo.value.session_key == STORED
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_on_event_frame_shape() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # Captured real native event frame shape (fake.ev / service.py:948-954): the shaped
        # on_event dict MUST be exactly {type, session_id, payload}.
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.delta", LIVE, {"text": "x"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "x"}),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        seen: list = []
        await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            on_event=seen.append,
            require_existing_session=True,
        )
        assert seen[0] == {
            "type": "message.delta",
            "session_id": LIVE,
            "payload": {"text": "x"},
        }
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_turn_observer_unregistered_on_settle() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.complete", LIVE, {"status": "complete", "text": "a"}),
                        ),
                    ),
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.complete", LIVE, {"status": "complete", "text": "b"}),
                        ),
                    ),
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        r1 = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p1",
            require_existing_session=True,
        )
        assert r1.text == "a"
        # No dangling submission handle after settlement.
        assert pool._turn_submissions.get(TICKET) is None
        r2 = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p2",
            require_existing_session=True,
        )
        assert r2.text == "b"
        assert pool._turn_submissions.get(TICKET) is None
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- the three ACK dispositions (Collision #A, ruled A1) ---------------------


def test_pool_step_gateway_streaming_disposition_owns_current_terminal() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        events_after=(
                            ev("message.complete", LIVE, {"status": "complete", "text": "mine"}),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            require_existing_session=True,
        )
        # streaming → own the CURRENT execution's next terminal.
        assert result.status == "complete"
        assert result.text == "mine"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_queued_disposition_skips_predecessor_terminal() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # A human turn is already running: our step ACKs `queued`. The NEXT terminal is the
        # interrupted PREDECESSOR's — the step must SKIP it and own the one AFTER it.
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "queued",
                        events_after=(
                            # predecessor's terminal (interrupted) — NOT ours:
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "interrupted", "text": "predecessor"},
                            ),
                            # our execution's terminal:
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "complete", "text": "ours"},
                            ),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            require_existing_session=True,
        )
        # The step owns the SECOND terminal ("ours"), not the predecessor's ("predecessor").
        assert result.status == "complete"
        assert result.text == "ours"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_steered_disposition_errors_no_terminal() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # steered → immediate errored, watching NO terminal (mirrors shared_gateway.py:852-861).
        # Even if a terminal arrives, the step must have already settled errored on the ACK.
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "steered",
                        events_after=(
                            ev("message.complete", LIVE, {"status": "complete", "text": "late"}),
                        ),
                    )
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            require_existing_session=True,
        )
        assert result.status == "errored"
        assert result.error is not None
        assert "steering" in result.error
        assert result.text != "late"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_interleaved_human_send_during_running_step() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # A HUMAN turn is genuinely RUNNING on the SAME child when the step is submitted, so the
        # step's `prompt.submit` ACKs `queued` (native queue semantics, D-native-turn-concurrency):
        # Hermes interrupts the running human turn and queues the step behind it. The stdout stream
        # then carries, AFTER the queued ACK, the interrupted HUMAN turn's terminal FIRST (its final
        # streamed delta + its `message.complete{interrupted}` carrying the human's text), THEN the
        # step's own turn. Settlement under `queued` (skip=1) must:
        #   - SKIP the interrupted human turn's terminal (the predecessor) and own the NEXT one —
        #     if the queued/skip correlation were removed the step would wrongly settle on the
        #     HUMAN turn's terminal ("HUMAN") instead of its own ("step");
        #   - never forward the skipped predecessor's frames to on_event (no "HUMAN" text leaks into
        #     the worker transcript).
        # This is DISTINCT from the pre-ACK-filtering tests: here the interleaving is a real queued
        # second submission, exercising the native-queue correlation the contract requires (§45-47).
        # Frame shapes: native event {"jsonrpc","method":"event","params":{type,session_id,payload}}
        # (fake.ev / service.py:948-954); prompt.submit ACK result carries status (service.py:556).
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "queued",
                        events_after=(
                            # The interrupted HUMAN turn's terminal (the predecessor, skip=1):
                            ev("message.delta", LIVE, {"text": "HUMAN interjection"}),
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "interrupted", "text": "HUMAN"},
                            ),
                            # The step's OWN turn, streamed after the predecessor drains (owned):
                            ev("message.start", LIVE, {}),
                            ev("message.delta", LIVE, {"text": "step body"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "step"}),
                        ),
                    ),
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        seen: list = []
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="step prompt",
            on_event=seen.append,
            require_existing_session=True,
        )
        # The step settled on ITS OWN terminal, not the interrupted human turn's completion.
        assert result.status == "complete"
        assert result.text == "step"
        # The on_event stream is UNCORRUPTED: the skipped predecessor's frames never reach it, so
        # the "HUMAN" text is absent and only the step's own turn is forwarded. (Remove the queued
        # skip correlation and the step settles on the HUMAN terminal — this test dies.)
        texts = [e["payload"].get("text", "") for e in seen]
        assert all("HUMAN" not in t for t in texts), seen
        assert [e["type"] for e in seen] == [
            "message.start",
            "message.delta",
            "message.complete",
        ], seen
        assert "step body" in texts
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_pre_ack_predecessor_terminal_is_dropped() -> None:
    """Codex Finding 1 (settlement arm-on-ACK race): a PREDECESSOR turn's terminal can land on
    the child stdout thread BEFORE our prompt.submit ACK. Because the ACK is an id-correlated RPC
    result Hermes returns BEFORE it streams OUR accepted turn's frames, any frame observed BEFORE
    the ACK belongs to a predecessor and MUST be dropped — never owned, never settled on.

    Here a predecessor `message.complete` (status complete) is emitted via events_before (BEFORE
    the ACK), the ACK then declares disposition `streaming` (skip 0), and OUR terminal streams
    after. A submission that armed at registration (the pre-fix behavior) would settle on the
    pre-ACK predecessor terminal ("predecessor"); the fixed post-ACK arming drops it and settles
    on OUR terminal ("mine"). This test DIES if settlement mis-owns the pre-ACK terminal.

    Frame shapes cited from real source: prompt.submit ACK result.status — service.py:556; native
    event frame {type,session_id,payload} — fake.ev / service.py:948-954."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "streaming",
                        # A predecessor's terminal, emitted BEFORE the ACK — NOT ours.
                        events_before=(
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "complete", "text": "predecessor"},
                            ),
                        ),
                        # OUR turn, emitted AFTER the ACK.
                        events_after=(
                            ev("message.delta", LIVE, {"text": "mine"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "mine"}),
                        ),
                    ),
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        seen: list = []
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            on_event=seen.append,
            require_existing_session=True,
        )
        # Settled on OUR terminal, not the pre-ACK predecessor's.
        assert result.status == "complete"
        assert result.text == "mine"
        # The pre-ACK predecessor terminal never reached on_event either.
        texts = [e["payload"].get("text", "") for e in seen]
        assert all("predecessor" not in t for t in texts), seen
        assert [e["type"] for e in seen] == ["message.delta", "message.complete"], seen
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_queued_predecessor_deltas_not_forwarded() -> None:
    """Codex Finding 2 (predecessor frames corrupting the owned transcript): on a `queued` ACK
    the interrupted predecessor's frames stream AFTER our ACK but BEFORE its terminal — including
    its own deltas. Those deltas must NOT be forwarded to on_event: the owned event stream carries
    ONLY our turn's frames, never the predecessor's `message.delta` text.

    The predecessor here streams a distinctive "PREDECESSOR" delta before its interrupted terminal;
    the step (queued, skip 1) drops both, then owns our turn's frames. A submission that enqueued
    every armed frame (the pre-fix behavior) would leak "PREDECESSOR" into on_event. This test DIES
    if the skipped predecessor's frames are forwarded.

    Frame shapes cited from real source: prompt.submit ACK result.status — service.py:556; native
    event frame {type,session_id,payload} — fake.ev / service.py:948-954."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "queued",
                        events_after=(
                            # The interrupted predecessor's frames (skip 1): a delta THEN its
                            # terminal — all dropped, never forwarded.
                            ev("message.delta", LIVE, {"text": "PREDECESSOR draft"}),
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "interrupted", "text": "PREDECESSOR"},
                            ),
                            # OUR turn's frames, after the skipped predecessor terminal.
                            ev("message.delta", LIVE, {"text": "ours"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "ours"}),
                        ),
                    ),
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        seen: list = []
        result = await _run_step(
            gateway,
            session_key=STORED,
            entity_id=TICKET,
            prompt_text="p",
            on_event=seen.append,
            require_existing_session=True,
        )
        # Owned the SECOND terminal ("ours"), skipped the predecessor's interrupted terminal.
        assert result.status == "complete"
        assert result.text == "ours"
        # NOT ONE predecessor frame reached on_event — only our turn's delta + terminal.
        texts = [e["payload"].get("text", "") for e in seen]
        assert all("PREDECESSOR" not in t for t in texts), seen
        assert [e["type"] for e in seen] == ["message.delta", "message.complete"], seen
        assert texts == ["ours", "ours"]
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_queued_with_pre_ack_predecessor_terminal_settles() -> None:
    """Codex Finding 1 (peer round): a QUEUED ACK's interrupted predecessor terminal can be
    emitted on the child stdout thread BEFORE the queued ACK is written.

    Hermes proof: the queued ACK is CONSTRUCTED under history_lock (tui_gateway/server.py:5091,
    5098) but WRITTEN LATER by the entry loop (tui_gateway/entry.py:371-373) AFTER the lock
    releases; in that window the interrupted predecessor thread reacquires history_lock and emits
    its `message.complete` FIRST (tui_gateway/server.py:9145-9147). So stdout can legally carry
    `predecessor message.complete -> queued ACK -> our turn`.

    A submission that DROPS pre-ACK terminals (never counting them) then arms queued/skip=1 will
    eat the STEP'S OWN terminal -> no terminal ever settles -> the runner hangs (there is NO
    whole-step timeout). The fix BUFFERS the pre-ACK predecessor terminal and, on the queued arm,
    consumes it against the skip so OUR own terminal settles the step.

    Frame shapes cited: prompt.submit ACK result.status — service.py:556; native event frame
    {type,session_id,payload} — fake.ev / service.py:948-954. `events_before` are emitted BEFORE
    the ACK; `events_after` AFTER it (fake.py:102-114)."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "prompt.submit": [
                    _submit_reply(
                        "queued",
                        # The interrupted predecessor's terminal, emitted BEFORE the queued ACK
                        # (the exact pre-ACK ordering the entry-loop deferred write allows).
                        events_before=(
                            ev(
                                "message.complete",
                                LIVE,
                                {"status": "interrupted", "text": "predecessor"},
                            ),
                        ),
                        # OUR turn, streamed AFTER the queued ACK — this must be what settles.
                        events_after=(
                            ev("message.delta", LIVE, {"text": "ours"}),
                            ev("message.complete", LIVE, {"status": "complete", "text": "ours"}),
                        ),
                    ),
                ],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        seen: list = []
        # Drive the step on a DAEMON thread with a BOUNDED join so a settlement HANG (the current,
        # unfixed behavior — the queued skip eats our own terminal) surfaces as a test FAILURE
        # rather than hanging the pytest process. A daemon runner thread that never settles does
        # NOT block interpreter exit; a non-daemon executor worker stuck in `_settle` would.
        box: dict = {}

        def run() -> None:
            box["result"] = gateway.run_ticket_step(
                session_key=STORED,
                entity_id=TICKET,
                prompt_text="p",
                on_event=seen.append,
                require_existing_session=True,
            )

        runner = threading.Thread(target=run, name="finding1-red", daemon=True)
        runner.start()
        await loop.run_in_executor(None, runner.join, 3.0)
        assert not runner.is_alive(), (
            "run_ticket_step HUNG: the queued skip ate the step's own terminal because the "
            "pre-ACK predecessor terminal was dropped instead of counted"
        )
        result = box["result"]
        # The step settled on ITS OWN terminal, not the pre-ACK predecessor's, and did NOT hang.
        assert result.status == "complete"
        assert result.text == "ours"
        # The pre-ACK predecessor terminal never reached on_event; only our turn's frames did.
        texts = [e["payload"].get("text", "") for e in seen]
        assert all("predecessor" not in t for t in texts), seen
        assert [e["type"] for e in seen] == ["message.delta", "message.complete"], seen
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- interrupt resolves the LIVE id (§1.6) -----------------------------------


def test_pool_step_gateway_interrupt_uses_live_id() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [_create_reply()],
                "session.interrupt": [Reply(result={"interrupted": True})],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await _spawn(pool, TICKET)
        gateway = _gateway(pool)
        # A live turn exists (live id is populated at create); interrupt targets that live id.
        await loop.run_in_executor(
            None,
            lambda: gateway.interrupt(STORED, TICKET, deadline=_monotonic() + 2.0),
        )
        interrupts = [f for f in fake.sent if f.get("method") == "session.interrupt"]
        assert len(interrupts) == 1
        assert interrupts[0]["params"] == {"session_id": LIVE}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_interrupt_no_live_id_is_noop() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(loop, fake.spawn)
        gateway = _gateway(pool)
        # No child spawned → no live id known → interrupt is a no-op (no frame sent, no raise).
        await loop.run_in_executor(
            None,
            lambda: gateway.interrupt("unknown", "no_such_ticket", deadline=_monotonic() + 2.0),
        )
        assert [f for f in fake.sent if f.get("method") == "session.interrupt"] == []
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_step_gateway_status_available() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(loop, fake.spawn)
        gateway = _gateway(pool)
        assert gateway.status().available is True
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())
