"""The one rule: you may act on anything strictly below you.

The pure half takes its facts as arguments, so these assertions need no connection and no
mock. The service half runs against a real schema, because what it adds is two database
lookups and the exact conditions on them are the behaviour.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import CHIEF_PRINCIPAL, OWNER_PRINCIPAL

from planner.core.authority import contracts as targets
from planner.core.authority.contracts import ANY_ID, Target, TargetKind
from planner.core.authority.declarations import (
    DAY_MIDDAY_FIELD,
    DAY_MORNING_FIELDS,
    STANDS_ABOVE_BY_WORKER_TYPE,
)
from planner.core.authority.logic import (
    ChainFacts,
    is_self,
    stands_above,
    stands_above_or_is_self,
)
from planner.core.authority.service import is_above, is_above_or_self, require_above
from planner.core.clock import TestClock as _TestClock
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry

_OUTCOME_A = Principal(PrincipalKind.sprint_item, "si_a")
_OUTCOME_B = Principal(PrincipalKind.sprint_item, "si_b")
_TICKET_A = Principal(PrincipalKind.ticket, "t_a")
_TICKET_B = Principal(PrincipalKind.ticket, "t_b")

_NO_FACTS = ChainFacts()
_A_PARENTS_TICKET_A = ChainFacts(target_parent_outcome_id="si_a")


# --- the rule, with no database -------------------------------------------------


@pytest.mark.parametrize("caller", [OWNER_PRINCIPAL, CHIEF_PRINCIPAL])
@pytest.mark.parametrize(
    "target",
    [
        targets.ticket("t_a"),
        targets.outcome("si_a"),
        targets.plan("day", "focus"),
        targets.owner_only("projects"),
    ],
)
def test_khushal_and_the_chief_stand_above_every_kind_of_target(
    caller: Principal, target: Target
) -> None:
    assert stands_above(caller, target, _NO_FACTS) is True


def test_an_outcome_stands_above_its_own_ticket_and_no_other() -> None:
    target = targets.ticket("t_a")
    assert stands_above(_OUTCOME_A, target, _A_PARENTS_TICKET_A) is True
    assert stands_above(_OUTCOME_B, target, _A_PARENTS_TICKET_A) is False


def test_an_outcome_stands_above_nothing_but_tickets() -> None:
    assert stands_above(_OUTCOME_A, targets.outcome("si_other"), _NO_FACTS) is False
    assert stands_above(_OUTCOME_A, targets.plan("sprint"), _NO_FACTS) is False
    assert stands_above(_OUTCOME_A, targets.owner_only("projects"), _NO_FACTS) is False


def test_an_outcome_is_not_above_itself_but_is_itself() -> None:
    own = targets.outcome("si_a")
    assert is_self(_OUTCOME_A, own) is True
    assert stands_above(_OUTCOME_A, own, _NO_FACTS) is False
    assert stands_above_or_is_self(_OUTCOME_A, own, _NO_FACTS) is True


def test_an_ordinary_ticket_stands_above_nothing_including_itself() -> None:
    own = targets.ticket("t_a")
    assert stands_above(_TICKET_A, own, _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.ticket("t_b"), _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.plan("day", "focus"), _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.owner_only("projects"), _NO_FACTS) is False
    # "Strictly below" is what refuses a worker its own proposal. No exception states it.
    assert is_self(_TICKET_A, own) is True
    assert stands_above_or_is_self(_TICKET_A, own, _NO_FACTS) is True


def test_a_ticket_is_never_the_self_of_another_ticket() -> None:
    assert is_self(_TICKET_A, targets.ticket("t_b")) is False


def test_a_declared_plan_reach_admits_exactly_the_declared_fields() -> None:
    facts = ChainFacts(caller_declared_targets=(targets.plan("day", "focus", "watchout"),))
    assert stands_above(_TICKET_A, targets.plan("day", "focus"), facts) is True
    assert stands_above(_TICKET_A, targets.plan("day", "focus", "watchout"), facts) is True
    assert stands_above(_TICKET_A, targets.plan("day", "notes"), facts) is False
    # One field out of the declared set refuses the whole write, as the branch it replaces
    # did: a mixed request was never a planning request.
    assert stands_above(_TICKET_A, targets.plan("day", "focus", "notes"), facts) is False
    # A declaration for one plan object says nothing about another.
    assert stands_above(_TICKET_A, targets.plan("sprint", "focus"), facts) is False


def test_an_empty_field_declaration_reaches_the_whole_object() -> None:
    facts = ChainFacts(caller_declared_targets=(targets.plan("sprint"),))
    assert stands_above(_TICKET_A, targets.plan("sprint"), facts) is True
    assert stands_above(_TICKET_A, targets.plan("sprint", "name"), facts) is True


def test_any_id_reaches_every_target_of_that_kind() -> None:
    facts = ChainFacts(caller_declared_targets=(targets.outcome(ANY_ID),))
    assert stands_above(_TICKET_A, targets.outcome("si_a"), facts) is True
    assert stands_above(_TICKET_A, targets.outcome("si_b"), facts) is True
    assert stands_above(_TICKET_A, targets.ticket("t_b"), facts) is False


def test_a_target_refuses_a_blank_id_and_fields_it_cannot_carry() -> None:
    with pytest.raises(ValueError):
        Target(TargetKind.ticket, "")
    with pytest.raises(ValueError):
        Target(TargetKind.ticket, " t_a ")
    with pytest.raises(ValueError):
        Target(TargetKind.ticket, "t_a", frozenset({"focus"}))


# --- the two database lookups the rule needs ------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Connection:
    conn = connect(str(tmp_path / "authority.db"))
    create_schema(conn)
    return conn


def _item(conn: Connection, title: str) -> str:
    clock = _TestClock(datetime(2026, 9, 20, 12, 0, 0).astimezone())
    return sprints_data.create_item(conn, title=title, project_id="project_vylo", clock=clock).id


def _ticket(conn: Connection, *, item_id: str | None, worker_type: str = "coding") -> str:
    return tickets_data.create_ticket(
        conn,
        title=f"Ticket under {item_id}",
        principal=OWNER_PRINCIPAL,
        now=0,
        title_max_chars=200,
        worker_type=worker_type,
        sprint_item_id=item_id,
    ).id


def test_the_parent_lookup_reads_the_ticket_s_outcome_right_now(db: Connection) -> None:
    item_id = _item(db, "Owning outcome")
    other_id = _item(db, "Another outcome")
    ticket_id = _ticket(db, item_id=item_id)

    assert is_above(db, Principal(PrincipalKind.sprint_item, item_id), targets.ticket(ticket_id))
    assert not is_above(
        db, Principal(PrincipalKind.sprint_item, other_id), targets.ticket(ticket_id)
    )


def test_an_unparented_ticket_has_no_outcome_above_it(db: Connection) -> None:
    item_id = _item(db, "Outcome with no child")
    ticket_id = _ticket(db, item_id=None)

    assert not is_above(
        db, Principal(PrincipalKind.sprint_item, item_id), targets.ticket(ticket_id)
    )
    assert is_above(db, OWNER_PRINCIPAL, targets.ticket(ticket_id))


def test_a_claimed_ticket_with_no_row_stands_above_nothing(db: Connection) -> None:
    assert not is_above(db, Principal(PrincipalKind.ticket, "t_missing"), targets.plan("day"))


def test_a_planning_day_ticket_stands_above_the_morning_fields_only(db: Connection) -> None:
    ticket_id = _ticket(db, item_id=None, worker_type="planning-day")
    caller = Principal(PrincipalKind.ticket, ticket_id)

    assert is_above(db, caller, targets.plan("day", *DAY_MORNING_FIELDS))
    assert not is_above(db, caller, targets.plan("day", "notes"))
    assert not is_above(db, caller, targets.plan("day", DAY_MIDDAY_FIELD))
    assert not is_above(db, caller, targets.owner_only("projects"))


def test_an_ordinary_ticket_declares_nothing(db: Connection) -> None:
    ticket_id = _ticket(db, item_id=None, worker_type="coding")
    caller = Principal(PrincipalKind.ticket, ticket_id)

    assert not is_above(db, caller, targets.plan("day", *DAY_MORNING_FIELDS))
    assert is_above_or_self(db, caller, targets.ticket(ticket_id))


def test_a_refusal_names_the_principal_and_the_target(db: Connection) -> None:
    ticket_id = _ticket(db, item_id=None)
    with pytest.raises(PlannerError) as raised:
        require_above(
            db, Principal(PrincipalKind.ticket, ticket_id), targets.owner_only("projects"), "create"
        )

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail["target_kind"] == "owner_only"
    assert raised.value.detail["principal_kind"] == "ticket"


def test_every_declared_worker_type_is_a_registered_one(db: Connection) -> None:
    """A declaration keyed on a name the database no longer uses reaches nothing."""
    registered = set(configured_worker_type_registry().registered_worker_types())
    assert registered, "the registry must be loaded from the opened database"
    assert set(STANDS_ABOVE_BY_WORKER_TYPE) <= registered
