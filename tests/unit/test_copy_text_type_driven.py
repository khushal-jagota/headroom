"""Copyable authored Ticket state follows its own registered field order."""

from __future__ import annotations

from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry(tmp_db: Connection) -> Iterator[WorkerTypeDefinition]:
    seed_probe_worker_type(tmp_db)
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()
