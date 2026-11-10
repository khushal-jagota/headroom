"""New work uses the supported catalog; historical work keeps its definitions."""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.errors import PlannerError
from planner.tickets import actions, data


def test_retired_types_are_refused_by_all_creation_writers(
    tmp_db: Connection,
) -> None:
    worker_type = "planning-day"
    writers: tuple[Callable[[], object], ...] = (
        lambda: data.create_ticket(
            tmp_db,
            title="Retired direct writer",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: actions.create_ticket(
            tmp_db,
            title="Retired",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: data.resolve_ticket_creation_context(
            tmp_db,
            title="Retired",
            title_max_chars=200,
            worker_type=worker_type,
        ),
    )
    for index, create in enumerate(writers):
        message = "unknown worker type" if index < 2 else "Worker type is retired"
        with pytest.raises(PlannerError, match=message):
            create()
