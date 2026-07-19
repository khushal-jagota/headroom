"""S2b — the pool-owned new-conversation seam (plan §3, §7.3).

RED-first coverage:
- the additive `NewConversationRequest`/kind round-trips (vocabulary);
- servicing a `NewConversationRequest` in `NeutralDownstreamSession` runs the pool rebind
  OFF the event loop, closes the old live session then creates a fresh one, returns the
  new ids, and emits an EMPTY `HistorySnapshotEvent`;
- the pool's own close/create bypass the downstream denylist seam (no `session.create`/
  `session.close` envelope through `handle_downstream_message`);
- the rebind invokes the persistence callback with the new stored id;
- no `pool_provider` -> a neutral error, no native frame;
- a `/new` rebind delivers NO stray passthrough row to a SECOND subscribed pane (F4).
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.minds.fake import FakeGateway, Reply

E1 = "agent_panels_chief_of_staff"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


def _pool(loop, spawn, *, on_stored_session_bound=None):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        relay=relay,
        loop=loop,
        spawn=spawn,
        on_stored_session_bound=on_stored_session_bound,
    )
    holder["pool"] = pool
    return pool, relay


class _MultiChild:
    """Serve a fresh scripted child per spawn (a respawn gets the NEXT child), so a test can
    model generation N and a respawned generation N+1 as distinct transports."""

    def __init__(self, children):
        self._children = list(children)
        self._i = 0
        self._lock = threading.Lock()

    def spawn(self, argv, env):
        with self._lock:
            child = self._children[self._i]
            self._i += 1
        return child.spawn(argv, env)


# --- vocabulary round-trip ---------------------------------------------------


def test_new_conversation_request_round_trips() -> None:
    request = nv.NewConversationRequest(employee_entity_id=E1)
    wire = nv.to_wire(request)
    assert wire == {
        "neutral": "request",
        "kind": "new_conversation",
        "employee_entity_id": E1,
    }
    assert nv.from_wire(wire) == request
    assert nv.from_wire_text(nv.to_wire_text(request)) == request


# --- pool rebind -------------------------------------------------------------


def test_pool_rebind_closes_old_then_creates_and_returns_ids() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live1", "stored_session_id": "stored1"}),
                    Reply(result={"session_id": "live2", "stored_session_id": "stored2"}),
                ],
                "session.close": [Reply(result={"closed": True})],
            }
        )
        bound: list = []
        pool, _ = _pool(
            loop,
            fake.spawn,
            on_stored_session_bound=lambda e, new, old: bound.append((e, new, old)),
        )
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        # First bound was the initial create — no predecessor durable id (old = None).
        assert bound == [(E1, "stored1", None)]

        new_live, new_stored = await loop.run_in_executor(
            pool.init_executor, pool.rebind_fresh_session, E1, "live1"
        )
        assert (new_live, new_stored) == ("live2", "stored2")
        methods = fake.sent_methods()
        # The pool closed the OLD live session FIRST, then created the fresh one.
        assert methods.index("session.close") < methods.index("session.create", 1)
        # The persistence callback fired with (employee, NEW stored id, OLD stored id), before
        # returning — the widened 3-arg signature carrying the predecessor for the CAS (§3.2).
        assert bound == [(E1, "stored1", None), (E1, "stored2", "stored1")]
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_rebind_serializes_stale_second_is_noop() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live1", "stored_session_id": "stored1"}),
                    Reply(result={"session_id": "live2", "stored_session_id": "stored2"}),
                ],
                "session.close": [Reply(result={"closed": True})],
            }
        )
        pool, _ = _pool(loop, fake.spawn)
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        # First rebind: live1 -> live2.
        first = await loop.run_in_executor(
            pool.init_executor, pool.rebind_fresh_session, E1, "live1"
        )
        assert first == ("live2", "stored2")
        # A second rebind that still names the ALREADY-replaced old live id is a no-op:
        # it observes the current fresh binding and returns it without re-close/re-create.
        second = await loop.run_in_executor(
            pool.init_executor, pool.rebind_fresh_session, E1, "live1"
        )
        assert second == ("live2", "stored2")
        # Exactly one close + one extra create happened (the stale rebind did nothing).
        assert fake.sent_methods().count("session.close") == 1
        assert fake.sent_methods().count("session.create") == 2
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- fail-closed persistence (S2B-OWN-001) -----------------------------------


def test_first_create_persist_failure_leaves_no_live_owner() -> None:
    """A raising persistence callback on FIRST create must NOT leave a live registered child
    or a stored-id map entry — else a later spawn resumes the SAME durable session and forks a
    second owner. Fail-closed: the spawn raises and the binding is torn down."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "live1", "stored_session_id": "s1"})]}
        )

        def raising_persist(_employee, _new_stored, _old_stored):
            raise RuntimeError("db write failed")

        pool, relay = _pool(loop, fake.spawn, on_stored_session_bound=raising_persist)
        # The first spawn's create-fresh persist raises -> child_for_employee propagates.
        try:
            await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
            raised = False
        except Exception:
            raised = True
        assert raised, "a failed persist must fail the spawn (fail-closed)"
        # No live registered child owns the durable session, and the stored-id map is empty —
        # a later spawn would create fresh, NOT resume s1 (no fork).
        assert not relay.binding_alive(1)
        assert pool._stored_session_id_by_employee.get(E1) is None
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_rebind_persist_failure_tears_down_child_and_keeps_old_durable_key() -> None:
    """A raising persistence callback on REBIND must NOT leave the child alive with an
    unpersisted fresh session while the OLD live session is already CLOSED (S3 §3.2, Codex
    Finding 5 — the fix, not the inherited defect). Fail-closed: the pool TEARS THE CHILD DOWN
    (record dropped, relay binding unregistered, live-id map cleared) so the next demand
    respawns clean and RESUMEs the still-durable OLD stored key; the stored-id map is left on
    the OLD durable key, and the rebind raises."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live1", "stored_session_id": "s1"}),
                    Reply(result={"session_id": "live2", "stored_session_id": "s2"}),
                ],
                "session.close": [Reply(result={"closed": True})],
            }
        )
        calls: list = []

        def persist(_employee, new_stored, _old_stored):
            calls.append(new_stored)
            if new_stored == "s2":  # the fresh rebind binding fails to persist
                raise RuntimeError("db write failed")

        pool, relay = _pool(loop, fake.spawn, on_stored_session_bound=persist)
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        assert calls == ["s1"]  # the initial create persisted fine

        try:
            await loop.run_in_executor(pool.init_executor, pool.rebind_fresh_session, E1, "live1")
            raised = False
        except Exception:
            raised = True
        assert raised, "a failed rebind persist must raise (fail-closed)"
        # The child was torn down: no record, relay binding unregistered, live-id cleared.
        assert E1 not in pool._records
        assert not relay.binding_alive(1)
        assert pool._live_session_id_by_employee.get(E1) is None
        # The stored-id map is left on the OLD durable key so a respawn RESUMEs it (the old
        # live session was closed, but the durable stored session persists).
        assert pool._stored_session_id_by_employee[E1] == "s1"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_rebind_persist_failure_cleanup_spares_newer_generation() -> None:
    """Codex Finding 3 (peer round): the rebind-failure cleanup must NOT kill a healthy NEWER
    generation.

    `child_for_employee` respawns a dead child using only `_lock`/`_init_slots`, NEVER
    `rebind_lock` (`employee_child_pool.py:286-315`, publish at `:331`) — VERIFIED — so a NEWER
    generation N+1 can publish while rebind N blocks in its persist callback (up to SQLite's 5s
    busy timeout). The unfixed cleanup generation-guards ONLY `_records.pop` but UNCONDITIONALLY
    clears `_live_session_id_by_employee` AND shuts down `record.transport` re-read from the
    now-current N+1 record — killing N+1's transport and dropping its live-session mapping.

    Here the persist callback for the fresh rebind binding deterministically publishes N+1 (it
    kills N's child and respawns via `child_for_employee`, resuming the still-durable OLD key)
    BEFORE it raises. N's cleanup then runs. The fix guards the live-id clear + the transport
    shutdown on `record.child_generation == generation` and shuts down N's OWN transport (passed
    in), so N+1 is untouched. This test DIES on the unfixed cleanup (N+1's transport killed / its
    live-id cleared)."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        # Child 1 = generation N: initial create (s1), then the rebind's close + fresh create (s2).
        child_n = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live1", "stored_session_id": "s1"}),
                    Reply(result={"session_id": "live2", "stored_session_id": "s2"}),
                ],
                "session.close": [Reply(result={"closed": True})],
            }
        )
        # Child 2 = generation N+1 (the respawn): resumes the still-durable OLD key s1.
        child_n_plus_1 = FakeGateway(
            {"session.resume": [Reply(result={"session_id": "live-n1", "resumed": "s1"})]}
        )
        multi = _MultiChild([child_n, child_n_plus_1])

        published: dict = {}

        def persist(_employee, new_stored, _old_stored):
            if new_stored != "s2":
                return  # the initial create (s1) persists fine
            # The fresh rebind binding is about to fail — but FIRST model the race: N's child dies
            # and a step demand respawns N+1 without the rebind lock, publishing it. Do that
            # synchronously here (we are on the init-executor thread, mid-persist), THEN raise.
            record_n = pool._records[E1]  # noqa: SLF001 — generation N's record
            record_n.transport._child._die()  # type: ignore[attr-defined]  # N's child dies
            assert record_n.transport.dead_event.wait(2.0)
            # Respawn: publishes generation N+1 with a DISTINCT transport, live-id map -> live-n1.
            record_n_plus_1 = pool.child_for_employee(E1)
            published["record"] = record_n_plus_1
            published["transport"] = record_n_plus_1.transport
            raise RuntimeError("db write failed")

        pool, relay = _pool(loop, multi.spawn, on_stored_session_bound=persist)
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)

        try:
            await loop.run_in_executor(pool.init_executor, pool.rebind_fresh_session, E1, "live1")
            raised = False
        except Exception:
            raised = True
        assert raised, "a failed rebind persist must raise (fail-closed)"

        record_n_plus_1 = published["record"]
        transport_n_plus_1 = published["transport"]
        # N+1 is a DIFFERENT generation/transport than N (proving a real respawn happened).
        assert record_n_plus_1.child_generation == 2
        assert transport_n_plus_1 is not None
        # N's cleanup MUST have spared the newer generation:
        # 1) N+1's transport stays ALIVE (the cleanup did not shut down the current record's
        #    transport — that was the bug that re-read `_records` for the transport to kill).
        assert transport_n_plus_1.alive, "cleanup killed the newer generation's transport"
        # 2) N+1 is still the current record.
        assert pool._records.get(E1) is record_n_plus_1
        # 3) N+1's live-session mapping is INTACT (the cleanup did not clear it unconditionally).
        assert pool._live_session_id_by_employee.get(E1) == "live-n1", (
            "cleanup cleared the newer generation's live-session mapping"
        )
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- session servicing -------------------------------------------------------


class _FakePool:
    """A pool stand-in for the session-branch unit tests. Records the rebind call and
    returns scripted ids; its `init_executor` is a real bounded executor so the session's
    `run_in_executor` path is genuinely exercised. `gate` (optional) blocks the rebind
    until released, to prove the rebind runs OFF the event loop (F6)."""

    def __init__(self, *, new_live="live2", new_stored="stored2", gate=None):
        import concurrent.futures

        self._new_live = new_live
        self._new_stored = new_stored
        self._gate = gate
        self.rebind_calls: list = []
        self.init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def rebind_fresh_session(self, employee_entity_id, old_live_session_id):
        self.rebind_calls.append((employee_entity_id, old_live_session_id))
        if self._gate is not None:
            self._gate.wait()
        return self._new_live, self._new_stored

    def shutdown(self):
        self.init_executor.shutdown(wait=False)


def _attached_session(loop, *, pool_provider, sink, history_reply):
    """Build a NeutralDownstreamSession that has already bootstrapped a live session id
    (`live1`) against a live pool child, with a `session.history` reply scripted for the
    post-rebind snapshot."""
    fake = FakeGateway(
        {
            "session.create": [
                Reply(result={"session_id": "live1", "stored_session_id": "stored1"})
            ],
            "session.active_list": [Reply(result={"sessions": [{"id": "live1"}]})],
            "session.history": [Reply(result={"count": 0, "messages": []}), history_reply],
        }
    )
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    child_pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        relay=relay,
        loop=loop,
        spawn=fake.spawn,
    )
    holder["pool"] = child_pool
    conn = relay.register_downstream()
    session = NeutralDownstreamSession(
        relay=relay,
        conn=conn,
        send_neutral=sink,
        db_path="/tmp/neutral-test.db",
        pool_provider=pool_provider,
    )
    return session, relay, conn, child_pool, fake


def test_new_conversation_bootstraps_returned_live_and_emits_empty_history() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        emitted: list = []

        async def sink(text):
            emitted.append(nv.from_wire_text(text))

        fake_pool = _FakePool(new_live="live2", new_stored="stored2")
        session, _relay, _conn, child_pool, fake = _attached_session(
            loop,
            pool_provider=lambda: fake_pool,
            sink=sink,
            history_reply=Reply(result={"count": 0, "messages": []}),
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        emitted.clear()

        await session.handle_neutral_request(nv.NewConversationRequest(employee_entity_id=E1))
        # The session passed the OLD live id it held (live1) to the pool rebind.
        assert fake_pool.rebind_calls == [(E1, "live1")]
        # It issued session.history against the RETURNED live id (live2), not active_list.
        history_frames = [f for f in fake.sent if f.get("method") == "session.history"]
        assert history_frames[-1]["params"]["session_id"] == "live2"
        # The pane got exactly one EMPTY history snapshot after the rebind.
        snaps = [e for e in emitted if isinstance(e, nv.HistorySnapshotEvent)]
        assert len(snaps) == 1
        assert snaps[0].messages == ()
        fake_pool.shutdown()
        child_pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_new_conversation_injects_no_denylist_envelope() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()

        async def sink(text):
            pass

        fake_pool = _FakePool()
        session, relay, conn, child_pool, _fake = _attached_session(
            loop,
            pool_provider=lambda: fake_pool,
            sink=sink,
            history_reply=Reply(result={"count": 0, "messages": []}),
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))

        injected: list = []
        real_handle = relay.handle_downstream_message

        def spy(conn_arg, raw):
            injected.append(raw)
            return real_handle(conn_arg, raw)

        relay.handle_downstream_message = spy  # type: ignore[assignment]
        await session.handle_neutral_request(nv.NewConversationRequest(employee_entity_id=E1))
        # No native session.create/session.close envelope crossed the downstream seam —
        # the pool issued them on its own transport (denylist untouched).
        for raw in injected:
            obj = json.loads(raw)
            if obj.get("relay") == "request":
                method = obj.get("frame", {}).get("method")
                assert method not in ("session.create", "session.close")
        fake_pool.shutdown()
        child_pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_new_conversation_runs_off_event_loop() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()

        async def sink(text):
            pass

        gate = threading.Event()
        fake_pool = _FakePool(gate=gate)
        session, _relay, _conn, child_pool, _fake = _attached_session(
            loop,
            pool_provider=lambda: fake_pool,
            sink=sink,
            history_reply=Reply(result={"count": 0, "messages": []}),
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))

        service = asyncio.ensure_future(
            session.handle_neutral_request(nv.NewConversationRequest(employee_entity_id=E1))
        )
        # While the rebind is HELD, the event loop is still responsive — a sleep resolves
        # even though the rebind has not returned yet.
        await asyncio.sleep(0)
        assert not service.done()
        released = {"ok": False}

        async def other_work():
            await asyncio.sleep(0.01)
            released["ok"] = True

        await other_work()
        assert released["ok"] is True
        gate.set()
        await service
        fake_pool.shutdown()
        child_pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_new_conversation_no_stray_passthrough_to_second_pane() -> None:
    """FLAGGED EDGE (F4): a `/new` rebind issues session.close/session.create on the child
    transport; those responses have an int id but NO downstream PendingForward, so the relay
    fans them out to EVERY subscriber. A SECOND pane subscribed to the same employee must NOT
    receive a stray passthrough row from pane-1's rebind. The session drops uncorrelated
    body-bearing frames (the pool's lifecycle plumbing), so nothing leaks."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live1", "stored_session_id": "stored1"}),
                    Reply(result={"session_id": "live2", "stored_session_id": "stored2"}),
                ],
                "session.close": [Reply(result={"closed": True})],
                "session.active_list": [
                    Reply(result={"sessions": [{"id": "live1"}]}),
                    Reply(result={"sessions": [{"id": "live1"}]}),
                ],
                "session.history": [
                    Reply(result={"count": 0, "messages": []}),  # pane1 attach
                    Reply(result={"count": 0, "messages": []}),  # pane2 attach
                    Reply(result={"count": 0, "messages": []}),  # pane1 /new
                ],
            }
        )
        pool, relay = _pool(loop, fake.spawn)

        emitted1: list = []
        emitted2: list = []

        async def sink1(text):
            emitted1.append(nv.from_wire_text(text))

        async def sink2(text):
            emitted2.append(nv.from_wire_text(text))

        conn1 = relay.register_downstream()
        conn2 = relay.register_downstream()
        session1 = NeutralDownstreamSession(
            relay=relay, conn=conn1, send_neutral=sink1,
            db_path="/tmp/neutral-test.db", pool_provider=lambda: pool,
        )
        session2 = NeutralDownstreamSession(
            relay=relay, conn=conn2, send_neutral=sink2,
            db_path="/tmp/neutral-test.db", pool_provider=lambda: pool,
        )

        # Both panes attach (spawns the one shared child; both subscribe to the employee).
        await session1.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        await session2.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        emitted2.clear()

        # Pane-1 rebinds. The pool closes live1 + creates live2 on the child transport; those
        # responses fan out to pane-2 (subscribed). Drain pane-2 and assert no passthrough.
        await session1.handle_neutral_request(nv.NewConversationRequest(employee_entity_id=E1))
        # Let the loop deliver every queued fan-out frame to pane-2's outbound, then drain.
        for _ in range(4):
            await asyncio.sleep(0)
        await session2.drain_pending_outbound()

        passthroughs = [e for e in emitted2 if isinstance(e, nv.PassthroughEvent)]
        assert passthroughs == [], f"stray passthrough leaked to second pane: {passthroughs}"

        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_new_conversation_without_pool_answers_neutral_error() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        emitted: list = []

        async def sink(text):
            emitted.append(nv.from_wire_text(text))

        session, _relay, _conn, child_pool, fake = _attached_session(
            loop,
            pool_provider=lambda: None,
            sink=sink,
            history_reply=Reply(result={"count": 0, "messages": []}),
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        emitted.clear()
        methods_before = list(fake.sent_methods())

        await session.handle_neutral_request(nv.NewConversationRequest(employee_entity_id=E1))
        failures = [e for e in emitted if isinstance(e, nv.TurnFailedEvent)]
        assert len(failures) == 1
        assert failures[0].reason == nv.TurnFailureReason.agent_error
        # No native frame was issued for the failed new-conversation.
        assert fake.sent_methods() == methods_before
        child_pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


