"""Source work follows the responsible Chief, Manager, or goal."""

from __future__ import annotations

import sqlite3

from planner.commands import admit
from planner.commands.service import admit_source_assignment
from planner.core.contracts import Principal
from planner.measure_sources.contracts import MeasureSource


def require_owner_or_chief(conn: sqlite3.Connection, principal: Principal) -> None:
    admit(conn, principal, "measure_sources.require_owner_or_chief", "measure_sources")


def require_assignment(
    conn: sqlite3.Connection, principal: Principal, responsible: Principal
) -> None:
    admit_source_assignment(conn, principal, responsible)


def require_author(conn: sqlite3.Connection, principal: Principal, source: MeasureSource) -> None:
    require_assignment(conn, principal, source.responsible)
