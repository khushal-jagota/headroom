"""EmployeeChildRegistry in isolation (plan §11.1).

Drives the registry directly with a fake `ChildReader`, a recording `ChildFrameSubscriber`, a fake
`reader_factory`, and fake identity/persist/resolver — no pool, no relay. Validates the lifecycle
half the registry owns: spawn-on-demand + reuse, respawn-dead + resume-own-session, identity-env
application, adopt-vs-resolver precedence, session-RPC issuance, fail-closed persist (with the
correct on-loop vs sync retire), no-reap, and one-deadline shutdown-all.

The per-frame dispatch ORDER (sink1 → sink2 → sink3) is a property of the real reader and is
proven in test_raw_frame_request_reply.py; the fake reader here is a lifecycle stub.
"""

from __future__ import annotations

import threading
from time import monotonic as _monotonic

import pytest

from planner.minds.employee_child_registry import (
    ChildReaderError,
    ChildRegistryClosing,
    EmployeeChildRegistry,
)

E1 = "ticket_x"
E2 = "ticket_y"


class FakeChildReader:
    """A scriptable lifecycle stub satisfying the ChildReader surface the registry calls."""

    def __init__(self, script: dict[str, list[dict]]) -> None:
        self._script = {method: list(replies) for method, replies in script.items()}
        self.requests: list[tuple[str, dict]] = []
        self.dead_event = threading.Event()
        self._alive = True
        self.started = False
        self.ready_waited = False
        self.shutdowns = 0
        self.shutdown_deadline: float | None = None  # the absolute deadline the registry passed
        self._on_dead = None

    def register_frame_sinks(self, *, on_deliver, on_fold, on_dead) -> None:
        self._on_deliver = on_deliver
        self._on_fold = on_fold
        self._on_dead = on_dead

    def start_reading(self) -> None:
        self.started = True

    def wait_ready(self, timeout: float) -> None:
        self.ready_waited = True

    def request(self, method, params, *, timeout, on_request_id=None) -> dict:
        self.requests.append((method, dict(params)))
        if on_request_id is not None:
            on_request_id(1)
        replies = self._script.get(method)
        if not replies:
            raise ChildReaderError(f"unscripted {method}")
        return replies.pop(0)

    def enqueue_frame(self, frame) -> None:
        pass

    def shutdown(self, *, deadline: float) -> None:
        self.shutdowns += 1
        self.shutdown_deadline = deadline
        self.die()

    @property
    def alive(self) -> bool:
        return self._alive

    def die(self) -> None:
        self._alive = False
        self.dead_event.set()
        # Fire the registry's captured death sink so the no-reap property is exercised against a
        # real on_dead callback (a mutation that reaped from on_dead would then be observable).
        if self._on_dead is not None:
            self._on_dead()


class RecordingSubscriber:
    def __init__(self) -> None:
        self.attached: list[int] = []
        self.detached: list[int] = []       # on-loop retire (spawn except / rebind discard)
        self.detached_now: list[int] = []   # sync retire (closing-race / shutdown)

    def attach(self, *, generation, employee_entity_id, reader) -> None:
        self.attached.append(generation)

    def deliver(self, *, generation, employee_entity_id, frame) -> None:
        pass

    def fold(self, *, generation, employee_entity_id, frame) -> None:
        pass

    def on_dead(self, *, generation, employee_entity_id) -> None:
        pass

    def detach(self, *, generation) -> None:
        self.detached.append(generation)

    def detach_now(self, *, generation) -> None:
        self.detached_now.append(generation)


class RecordingFactory:
    def __init__(self, readers: list[FakeChildReader]) -> None:
        self._readers = list(readers)
        self._i = 0
        self.builds: list[tuple[int, str, dict]] = []

    def __call__(
        self, *, generation, employee_entity_id, env, on_deliver, on_fold, on_dead
    ) -> FakeChildReader:
        self.builds.append((generation, employee_entity_id, dict(env)))
        reader = self._readers[self._i]
        self._i += 1
        reader.register_frame_sinks(on_deliver=on_deliver, on_fold=on_fold, on_dead=on_dead)
        return reader


def _create_reply(live="live", stored="stored") -> dict:
    return {"session_id": live, "stored_session_id": stored}


def _resume_reply(live="live-r", resumed="stored") -> dict:
    return {"session_id": live, "resumed": resumed}


def _registry(
    factory: RecordingFactory,
    subscriber: RecordingSubscriber,
    *,
    identity_env=None,
    on_bound=None,
    resolver=None,
) -> EmployeeChildRegistry:
    return EmployeeChildRegistry(
        reader_factory=factory,
        subscriber=subscriber,
        identity_env_strategy=identity_env or (lambda emp: {"EMP": emp}),
        session_source="test-source",
        session_cols=42,
        on_stored_session_bound=on_bound,
        stored_session_resolver=resolver,
    )


def test_spawn_on_demand_reuses_single_child() -> None:
    reader = FakeChildReader({"session.create": [_create_reply()]})
    factory = RecordingFactory([reader])
    sub = RecordingSubscriber()
    reg = _registry(factory, sub)
    r1 = reg.get_or_spawn(E1)
    r2 = reg.get_or_spawn(E1)
    assert r1 is r2
    assert len(factory.builds) == 1
    assert reader.requests == [("session.create", {"source": "test-source", "cols": 42})]
    assert reader.started and reader.ready_waited
    assert sub.attached == [1]
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_identity_env_reaches_factory() -> None:
    reader = FakeChildReader({"session.create": [_create_reply()]})
    factory = RecordingFactory([reader])
    reg = _registry(factory, RecordingSubscriber(), identity_env=lambda emp: {"MARK": emp})
    reg.get_or_spawn(E1)
    assert factory.builds[0][1] == E1
    assert factory.builds[0][2] == {"MARK": E1}
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_respawn_dead_child_resumes_own_session() -> None:
    r1 = FakeChildReader({"session.create": [_create_reply(stored="stored-k")]})
    r2 = FakeChildReader({"session.resume": [_resume_reply(resumed="stored-k")]})
    factory = RecordingFactory([r1, r2])
    reg = _registry(factory, RecordingSubscriber())
    reg.get_or_spawn(E1)
    r1.die()
    reg.get_or_spawn(E1)
    assert len(factory.builds) == 2
    assert r2.requests == [("session.resume", {"session_id": "stored-k"})]
    assert r1.shutdowns >= 1  # the stale reader was torn down before the respawn
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_no_background_reap_of_dead_child() -> None:
    r1 = FakeChildReader({"session.create": [_create_reply(stored="stored-k")]})
    r2 = FakeChildReader({"session.resume": [_resume_reply(resumed="stored-k")]})
    factory = RecordingFactory([r1, r2])
    reg = _registry(factory, RecordingSubscriber())
    reg.get_or_spawn(E1)
    r1.die()  # fires the registry's captured on_dead (FakeChildReader.die)
    # The death sink must NOT trigger a background respawn: no demand yet, so builds stays 1.
    assert len(factory.builds) == 1
    reg.get_or_spawn(E1)  # next demand respawns
    assert len(factory.builds) == 2
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_adopt_beats_resolver() -> None:
    reader = FakeChildReader({"session.resume": [_resume_reply(resumed="eager")]})
    factory = RecordingFactory([reader])
    resolver_calls: list[str] = []
    reg = _registry(
        factory,
        RecordingSubscriber(),
        resolver=lambda emp: resolver_calls.append(emp) or "from-resolver",
    )
    reg.adopt_stored_session(E1, "eager")
    reg.get_or_spawn(E1)
    assert reader.requests == [("session.resume", {"session_id": "eager"})]
    assert resolver_calls == []  # resolver not consulted when a key is already held
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_resolver_resumes_when_no_key_held() -> None:
    reader = FakeChildReader({"session.resume": [_resume_reply(resumed="resolved-key")]})
    factory = RecordingFactory([reader])
    reg = _registry(factory, RecordingSubscriber(), resolver=lambda emp: "resolved-key")
    reg.get_or_spawn(E1)
    assert reader.requests == [("session.resume", {"session_id": "resolved-key"})]
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_interrupt_issues_session_interrupt_on_live_id() -> None:
    reader = FakeChildReader(
        {"session.create": [_create_reply(live="live-1")], "session.interrupt": [{}]}
    )
    reg = _registry(RecordingFactory([reader]), RecordingSubscriber())
    reg.get_or_spawn(E1)
    reg.interrupt_live_turn(E1, deadline=_monotonic() + 2.0)
    assert ("session.interrupt", {"session_id": "live-1"}) in reader.requests
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_rebind_closes_old_then_creates_and_persists() -> None:
    reader = FakeChildReader(
        {
            "session.create": [
                _create_reply(live="live1", stored="stored1"),
                _create_reply(live="live2", stored="stored2"),
            ],
            "session.close": [{}],
        }
    )
    bound: list[tuple[str, str, str | None]] = []
    reg = _registry(
        RecordingFactory([reader]),
        RecordingSubscriber(),
        on_bound=lambda e, new, old: bound.append((e, new, old)),
    )
    reg.get_or_spawn(E1)
    new_live, new_stored = reg.rebind_fresh_session(E1, "live1")
    assert (new_live, new_stored) == ("live2", "stored2")
    methods = [m for m, _ in reader.requests]
    assert methods.index("session.close") < methods.index("session.create", 1)
    assert bound == [(E1, "stored1", None), (E1, "stored2", "stored1")]
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_fail_closed_persist_on_first_create() -> None:
    reader = FakeChildReader({"session.create": [_create_reply(stored="s1")]})
    sub = RecordingSubscriber()

    def raising_persist(_emp, _new, _old):
        raise RuntimeError("db write failed")

    reg = _registry(RecordingFactory([reader]), sub, on_bound=raising_persist)
    with pytest.raises(RuntimeError):
        reg.get_or_spawn(E1)
    assert reader.shutdowns >= 1
    assert sub.detached == [1]  # on-loop retire (spawn except path, F8 site 475)
    assert reg._stored_session_id_by_employee.get(E1) is None
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_fail_closed_persist_on_rebind_keeps_old_key() -> None:
    reader = FakeChildReader(
        {
            "session.create": [
                _create_reply(live="live1", stored="s1"),
                _create_reply(live="live2", stored="s2"),
            ],
            "session.close": [{}],
        }
    )
    sub = RecordingSubscriber()

    def persist(_emp, new_stored, _old):
        if new_stored == "s2":
            raise RuntimeError("db write failed")

    reg = _registry(RecordingFactory([reader]), sub, on_bound=persist)
    reg.get_or_spawn(E1)
    with pytest.raises(RuntimeError):
        reg.rebind_fresh_session(E1, "live1")
    # Child torn down, retired on-loop (F8 site 646), stored map left on the OLD durable key.
    assert E1 not in reg._records
    assert 1 in sub.detached
    assert reg._stored_session_id_by_employee[E1] == "s1"
    assert reg._live_session_id_by_employee.get(E1) is None
    reg.shutdown(deadline=_monotonic() + 2.0)


def test_closing_race_after_publish_retires_synchronously() -> None:
    """get_or_spawn closing-race (F8 sync site): if `_closing` flips True AFTER the child's slot is
    published but before the post-spawn re-check, the just-built child is discarded via the
    SYNCHRONOUS `detach_now`, not on-loop `detach`. A mutation of that site to `detach` fails."""
    reader = FakeChildReader({"session.create": [_create_reply()]})
    sub = RecordingSubscriber()
    reg = _registry(RecordingFactory([reader]), sub)

    # wait_ready runs after the slot publishes (past _spawn_and_bind's own R3-B check), so flipping
    # `_closing` there makes the post-publish re-check take the synchronous discard branch.
    def wait_ready_then_close(timeout):
        reader.ready_waited = True
        reg._closing = True

    reader.wait_ready = wait_ready_then_close  # type: ignore[method-assign]

    with pytest.raises(ChildRegistryClosing):
        reg.get_or_spawn(E1)
    assert sub.detached_now == [1]  # synchronous retire; a mutation to on-loop `detach` fails here
    assert sub.detached == []
    assert reader.shutdowns >= 1


def test_shutdown_all_within_one_deadline() -> None:
    r1 = FakeChildReader({"session.create": [_create_reply(stored="k1")]})
    r2 = FakeChildReader({"session.create": [_create_reply(stored="k2")]})
    reg = _registry(RecordingFactory([r1, r2]), RecordingSubscriber())
    reg.get_or_spawn(E1)
    reg.get_or_spawn(E2)
    deadline = _monotonic() + 2.0
    reg.shutdown(deadline=deadline)
    assert r1.shutdowns >= 1
    assert r2.shutdowns >= 1
    # One SHARED deadline reaches every child (not a fresh per-child budget): both readers were
    # shut down against the exact absolute deadline passed. A per-child deadline mutation fails.
    assert r1.shutdown_deadline == deadline
    assert r2.shutdown_deadline == deadline
    assert reg._closing is True
