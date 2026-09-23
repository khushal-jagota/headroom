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
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.authority import contracts as targets
from planner.core.authority.contracts import ANY_ID
from planner.core.authority.declarations import (
    DAY_MIDDAY_FIELD,
    DAY_MORNING_FIELDS,
    STANDS_ABOVE_BY_WORKER_TYPE,
)
from planner.core.authority.logic import (
    ChainFacts,
    is_below,
    is_self,
    shares_the_chain,
    stands_above,
    stands_above_or_is_self,
)
from planner.core.authority.service import (
    is_above,
    is_above_or_self,
    require_above,
    require_in_chain,
)
from planner.core.clock import TestClock as _TestClock
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry

_OUTCOME_A = Principal(PrincipalKind.sprint_item, "si_a")
_TICKET_A = Principal(PrincipalKind.ticket, "t_a")
_NO_FACTS = ChainFacts()
_IS_A_PRINCIPAL = ChainFacts(target_is_itself_a_principal=True)
# --- the rule, with no database -------------------------------------------------


def test_an_ordinary_ticket_stands_above_nothing_including_itself() -> None:
    own = targets.ticket("t_a")
    assert stands_above(_TICKET_A, own, _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.ticket("t_b"), _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.plan("day", "focus"), _NO_FACTS) is False
    assert stands_above(_TICKET_A, targets.owner_only("projects"), _NO_FACTS) is False
    # "Strictly below" is what refuses a worker its own proposal. No exception states it.
    assert is_self(_TICKET_A, own, _IS_A_PRINCIPAL) is True
    assert stands_above_or_is_self(_TICKET_A, own, _IS_A_PRINCIPAL) is True


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


def test_a_claimed_ticket_with_no_row_stands_above_nothing(db: Connection) -> None:
    assert not is_above(db, Principal(PrincipalKind.ticket, "t_missing"), targets.plan("day"))


def test_a_planning_day_ticket_stands_above_the_morning_fields_only(db: Connection) -> None:
    ticket_id = _ticket(db, item_id=None, worker_type="planning-day")
    caller = Principal(PrincipalKind.ticket, ticket_id)

    assert is_above(db, caller, targets.plan("day", *DAY_MORNING_FIELDS))
    assert not is_above(db, caller, targets.plan("day", "notes"))
    assert not is_above(db, caller, targets.plan("day", DAY_MIDDAY_FIELD))
    assert not is_above(db, caller, targets.owner_only("projects"))


def test_a_refusal_names_the_principal_and_the_target(db: Connection) -> None:
    ticket_id = _ticket(db, item_id=None)
    with pytest.raises(PlannerError) as raised:
        require_above(
            db, Principal(PrincipalKind.ticket, ticket_id), targets.owner_only("projects")
        )

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail["target_kind"] == "owner_only"
    assert raised.value.detail["principal_kind"] == "ticket"


def test_every_declared_worker_type_is_a_registered_one(db: Connection) -> None:
    """A declaration keyed on a name the database no longer uses reaches nothing."""
    registered = set(configured_worker_type_registry().registered_worker_types())
    assert registered, "the registry must be loaded from the opened database"
    assert set(STANDS_ABOVE_BY_WORKER_TYPE) <= registered


def test_nobody_can_be_a_target_that_is_not_a_live_principal() -> None:
    """A claim to be a Sprint Item that does not exist, or an ``other`` bucket, is a claim
    to be nobody. Without this the id match alone would admit it."""
    missing = targets.outcome("si_missing")
    assert is_self(_OUTCOME_A, targets.outcome("si_a"), _NO_FACTS) is False
    assert stands_above_or_is_self(_OUTCOME_A, missing, _NO_FACTS) is False
    assert stands_above_or_is_self(_TICKET_A, targets.ticket("t_a"), _NO_FACTS) is False
    # Khushal and the Chief still get through, so the handler answers with not_found
    # rather than authority leaking whether the thing exists.
    assert stands_above(OWNER_PRINCIPAL, missing, _NO_FACTS) is True


def test_every_sprint_item_is_its_own_principal(db: Connection) -> None:
    item_id = _item(db, "Outcome")
    claimed = Principal(PrincipalKind.sprint_item, item_id)

    assert is_above_or_self(db, claimed, targets.outcome(item_id)) is True
    assert is_above_or_self(db, OWNER_PRINCIPAL, targets.outcome(item_id)) is True


def test_a_claimed_outcome_that_does_not_exist_is_nobody(db: Connection) -> None:
    claimed = Principal(PrincipalKind.sprint_item, "si_nope")
    assert is_above_or_self(db, claimed, targets.outcome("si_nope")) is False


def test_a_declaration_over_named_fields_does_not_reach_the_whole_object() -> None:
    """The boundary that keeps sprint planning out of deleting an Outcome.

    Shaping an Outcome's brief names the fields it writes. Deleting one names no field,
    because it is not a write to a field — it is the whole object. A declaration limited
    to fields must not be read as covering that, or "may edit four fields" quietly
    becomes "may delete it".
    """
    declared = targets.outcome(ANY_ID, "title", "body")

    assert declared.covers(targets.outcome("si_a", "title"))
    assert declared.covers(targets.outcome("si_a", "title", "body"))
    assert not declared.covers(targets.outcome("si_a", "title", "project_id"))
    assert not declared.covers(targets.outcome("si_a"))
    # A declaration that names nothing does reach the whole object, which is what
    # plan("sprint") and plan("day_tickets") mean.
    assert targets.plan("day_tickets").covers(targets.plan("day_tickets"))


# --- the chain question creation asks -------------------------------------------


def test_only_a_ticket_is_below_anything_and_only_its_own_outcome() -> None:
    """The mirror of the rule. It is not authority, and it reaches exactly one thing."""
    under_a = ChainFacts(target_is_itself_a_principal=True, caller_parent_outcome_id="si_a")

    assert is_below(_TICKET_A, targets.outcome("si_a"), under_a) is True
    assert is_below(_TICKET_A, targets.outcome("si_b"), under_a) is False
    # Not a route to somebody else's Ticket, or to the plan, or to what Khushal owns.
    assert is_below(_TICKET_A, targets.ticket("t_b"), under_a) is False
    assert is_below(_TICKET_A, targets.plan("day"), under_a) is False
    # An Outcome is below nothing, and neither is Khushal.
    assert is_below(_OUTCOME_A, targets.outcome("si_a"), under_a) is False
    assert is_below(OWNER_PRINCIPAL, targets.outcome("si_a"), under_a) is False


def test_being_in_a_chain_does_not_make_you_above_it() -> None:
    """A Ticket may put work under its own Outcome without gaining anything over it."""
    under_a = ChainFacts(target_is_itself_a_principal=True, caller_parent_outcome_id="si_a")
    own_outcome = targets.outcome("si_a")

    assert shares_the_chain(_TICKET_A, own_outcome, under_a) is True
    assert stands_above_or_is_self(_TICKET_A, own_outcome, under_a) is False


def test_the_chain_lookup_reads_the_caller_s_own_outcome(db: Connection) -> None:
    item_id = _item(db, "Owning outcome")
    other_id = _item(db, "Another outcome")
    caller = Principal(PrincipalKind.ticket, _ticket(db, item_id=item_id))

    require_in_chain(db, caller, targets.outcome(item_id))
    with pytest.raises(PlannerError) as raised:
        require_in_chain(db, caller, targets.outcome(other_id))

    assert raised.value.code is ErrorCode.agent_forbidden


def test_a_ticket_under_no_outcome_is_in_nobody_s_chain(db: Connection) -> None:
    item_id = _item(db, "Owning outcome")
    caller = Principal(PrincipalKind.ticket, _ticket(db, item_id=None))

    with pytest.raises(PlannerError):
        require_in_chain(db, caller, targets.outcome(item_id))
