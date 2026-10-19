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

from planner.core.authority.logic import (
    ChainFacts,
)
from planner.core.clock import TestClock as _TestClock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data

_OUTCOME_A = Principal(PrincipalKind.sprint_item, "si_a")
_TICKET_A = Principal(PrincipalKind.ticket, "t_a")
_NO_FACTS = ChainFacts()
_IS_A_PRINCIPAL = ChainFacts(target_is_itself_a_principal=True)
# --- the rule, with no database -------------------------------------------------




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




















# --- the chain question creation asks -------------------------------------------
