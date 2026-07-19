"""S3 Wave 2 — per-ticket adoption + fail-closed persistence (plan §3, §9.2).

- adoption: the pool reads a ticket's persisted `employee_session_id` on demand via the
  composition-injected `stored_session_resolver` and RESUMEs it (NULL → creates fresh);
- persistence: a fresh ticket bind writes `tickets.employee_session_id` through the new
  call-only `bind_pool_employee_session_id` (the ownership CAS), fail-closed inside the
  spawn guard (a persist failure tears down the child — S2B-OWN-001 generalized);
- the CAS rejects a candidate session owned by ANOTHER ticket;
- the runner's own `claim_running_step_employee_session_id` is idempotent when its
  candidate equals the pool's prior bind (§3.3 — a confirm, not a rejected ownership change).

Scripted-child payload shapes captured from real source (see tests/unit/test_hermes_backend_pool.py
and src/planner/minds/fake.py); the ticket-session writer routes through the existing CAS
`write_employee_session_id_in_transaction` (src/planner/tickets/data.py:342-405).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from time import monotonic as _monotonic
from typing import Any

import pytest

from planner.core.clock import TestClock as _TestClock
from planner.core.config import Config
from planner.core.errors import PlannerError
from planner.days import data as days_data
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.minds.fake import FakeGateway, Reply
from planner.runtime import automatic_employee_step_eligibility
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    EmployeeSessionIdTransition,
    TicketStatus,
)

HERMES_PY = "/x/hermes-agent/venv/bin/python"
TICKET = "ticket_x"
_AUTOMATIC_PLANNING_DAY_ID = "day_2099-01-01"


def _create_reply(sid="live", key="stored"):
    return Reply(result={"session_id": sid, "stored_session_id": key})


def _resume_reply(sid="live-r", resumed="stored"):
    return Reply(result={"session_id": sid, "resumed": resumed})


def _pool(loop, spawn, *, resolver=None, on_bound=None):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        relay=relay,
        loop=loop,
        spawn=spawn,
        stored_session_resolver=resolver,
        on_stored_session_bound=on_bound,
    )
    holder["pool"] = pool
    return pool, relay


async def _spawn(pool, employee):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool.init_executor, pool.child_for_employee, employee)


# --- adoption ----------------------------------------------------------------


def test_ticket_session_adopted_on_first_spawn() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # A ticket with a persisted employee_session_id: the resolver returns it, so the pool
        # RESUMEs instead of creating fresh.
        fake = FakeGateway({"session.resume": [_resume_reply(resumed="stored-persisted")]})
        pool, _ = _pool(
            loop,
            fake.spawn,
            resolver=lambda emp: "stored-persisted" if emp == TICKET else None,
        )
        await _spawn(pool, TICKET)
        methods = fake.sent_methods()
        assert "session.resume" in methods
        assert "session.create" not in methods
        resumes = [f for f in fake.sent if f.get("method") == "session.resume"]
        assert resumes[0]["params"] == {"session_id": "stored-persisted"}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_never_run_ticket_creates_fresh_when_resolver_returns_none() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply(key="fresh-stored")]})
        pool, _ = _pool(loop, fake.spawn, resolver=lambda emp: None)
        await _spawn(pool, TICKET)
        methods = fake.sent_methods()
        assert "session.create" in methods
        assert "session.resume" not in methods
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_adopt_stored_session_takes_precedence_over_resolver() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # An eagerly-adopted key (S2b Chief path) is used without consulting the resolver.
        fake = FakeGateway({"session.resume": [_resume_reply(resumed="eager")]})
        resolver_calls: list = []
        pool, _ = _pool(
            loop,
            fake.spawn,
            resolver=lambda emp: resolver_calls.append(emp) or "from-resolver",
        )
        pool.adopt_stored_session(TICKET, "eager")
        await _spawn(pool, TICKET)
        resumes = [f for f in fake.sent if f.get("method") == "session.resume"]
        assert resumes[0]["params"] == {"session_id": "eager"}
        assert resolver_calls == []  # resolver not consulted when a key is already held
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- fresh binding persisted through the ownership writer ---------------------


def test_fresh_ticket_binding_persisted_through_ownership_writer() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply(key="fresh-stored")]})
        bound: list = []
        pool, _ = _pool(
            loop,
            fake.spawn,
            resolver=lambda emp: None,
            on_bound=lambda emp, new, old: bound.append((emp, new, old)),
        )
        await _spawn(pool, TICKET)
        # On a fresh create the callback fires with (employee, new_stored, old=None).
        assert bound == [(TICKET, "fresh-stored", None)]
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_fresh_ticket_binding_persist_failure_tears_down_child() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply(key="fresh-stored")]})

        def raising_persist(_emp, _new, _old):
            raise RuntimeError("db write failed")

        pool, relay = _pool(loop, fake.spawn, resolver=lambda emp: None, on_bound=raising_persist)
        try:
            await _spawn(pool, TICKET)
            raised = False
        except Exception:
            raised = True
        assert raised, "a failed persist must fail the spawn (fail-closed)"
        assert not relay.binding_alive(1)
        assert pool._stored_session_id_by_employee.get(TICKET) is None
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- the call-only ownership-CAS writer (real DB) ----------------------------


def _create_ticket(conn: Connection, clock: _TestClock, **kw: Any):
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title=kw.pop("title", "Test ticket"),
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        **kw,
    )
    return tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )


def test_bind_pool_employee_session_id_writes_and_is_idempotent(
    tmp_db: Connection, cfg: Config, fake_clock: _TestClock
) -> None:
    ticket = _create_ticket(tmp_db, fake_clock)
    now = fake_clock.now_unix()
    tickets_data.bind_pool_employee_session_id(
        tmp_db,
        ticket.id,
        expected_stored_session_id=None,
        candidate_stored_session_id="stored-A",
        now=now,
    )
    reread = tickets_data.read_ticket(tmp_db, ticket.id)
    assert reread.employee_session_id == "stored-A"
    # A second bind with the SAME candidate is idempotent (no raise).
    tickets_data.bind_pool_employee_session_id(
        tmp_db,
        ticket.id,
        expected_stored_session_id="stored-A",
        candidate_stored_session_id="stored-A",
        now=now,
    )
    assert tickets_data.read_ticket(tmp_db, ticket.id).employee_session_id == "stored-A"


def test_bind_pool_employee_session_id_post_write_equality_assert(
    tmp_db: Connection, cfg: Config, fake_clock: _TestClock
) -> None:
    ticket = _create_ticket(tmp_db, fake_clock)
    now = fake_clock.now_unix()
    # Bind stored-A first.
    tickets_data.bind_pool_employee_session_id(
        tmp_db,
        ticket.id,
        expected_stored_session_id=None,
        candidate_stored_session_id="stored-A",
        now=now,
    )
    # A rebind whose `expected` MISMATCHES the current binding: the CAS silently RETAINs
    # stored-A rather than writing stored-B; the writer's post-write equality assert turns
    # that silent-retain into a fail-closed raise.
    with pytest.raises(PlannerError):
        tickets_data.bind_pool_employee_session_id(
            tmp_db,
            ticket.id,
            expected_stored_session_id="wrong-expected",
            candidate_stored_session_id="stored-B",
            now=now,
        )
    # The durable binding is unchanged (still stored-A).
    assert tickets_data.read_ticket(tmp_db, ticket.id).employee_session_id == "stored-A"


def test_pool_bind_rejects_session_owned_by_another_ticket(
    tmp_db: Connection, cfg: Config, fake_clock: _TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket_a = _create_ticket(tmp_db, fake_clock, title="A")
    ticket_b = _create_ticket(tmp_db, fake_clock, title="B")
    tickets_data.bind_pool_employee_session_id(
        tmp_db,
        ticket_a.id,
        expected_stored_session_id=None,
        candidate_stored_session_id="shared-session",
        now=now,
    )
    # Ticket B tries to bind the SAME session already owned by ticket A → rejected.
    with pytest.raises(PlannerError):
        tickets_data.bind_pool_employee_session_id(
            tmp_db,
            ticket_b.id,
            expected_stored_session_id=None,
            candidate_stored_session_id="shared-session",
            now=now,
        )
    assert tickets_data.read_ticket(tmp_db, ticket_b.id).employee_session_id is None


def test_runner_session_claim_idempotent_against_pool_bind(
    tmp_db: Connection, cfg: Config, fake_clock: _TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = _create_ticket(tmp_db, fake_clock)
    # The pool binds the durable session first (outside a running step).
    tickets_data.bind_pool_employee_session_id(
        tmp_db,
        ticket.id,
        expected_stored_session_id=None,
        candidate_stored_session_id="pool-bound",
        now=now,
    )
    # Bring the ticket to agent_running_step (the runner's claim precondition).
    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, ticket.id, now)
    started = tickets_data.claim_automatic_employee_step(
        tmp_db,
        ticket.id,
        planning_day_id_resolver=lambda: _AUTOMATIC_PLANNING_DAY_ID,
        eligibility_check=(
            automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
        ),
        now=now,
    )
    assert started is not None
    assert started.ticket_status is TicketStatus.agent_running_step
    # The runner's on_session_key claim uses the SAME candidate the pool already bound → the
    # CAS is idempotent (current == candidate → accept), NOT a rejected ownership change.
    updated = tickets_data.claim_running_step_employee_session_id(
        tmp_db,
        ticket.id,
        transition=EmployeeSessionIdTransition(
            expected_employee_session_id="pool-bound",
            candidate_employee_session_id="pool-bound",
        ),
        now=now,
    )
    assert updated.employee_session_id == "pool-bound"
    assert updated.ticket_status is TicketStatus.agent_running_step
