"""Explicit historical definitions, separate from every fresh-install registry."""

import json
import sqlite3
from pathlib import Path

from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.store import definition_from_json, write_definition


def historical_definition(worker_type: str) -> WorkerTypeDefinition:
    """Read one preserved definition without changing the default seed or registry."""
    for definition in json.loads(Path(__file__).with_name("retired_worker_types.json").read_text()):
        if definition["worker_type"] == worker_type:
            return definition_from_json(json.dumps(definition))
    raise KeyError(worker_type)


def seed_historical_worker_type(conn: sqlite3.Connection, worker_type: str) -> None:
    """Restore explicit historical fixture metadata without opening new-work admission."""
    definition = historical_definition(worker_type)
    name = definition.worker_profile.specialist_skill
    conn.execute(
        "INSERT OR IGNORE INTO managed_skills(skill_name,source_text,updated_at) VALUES(?,?,0)",
        (name, f"---\nname: {name}\ndescription: Historical fixture\n---\n\nHistorical source.\n"),
    )
    write_definition(conn, definition, now=0)
    conn.commit()
    load_worker_runtime_definitions(conn)
