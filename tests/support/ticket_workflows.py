"""Explicit saved workflow values for fixtures that deliberately write Ticket rows."""

import sqlite3

from planner.worker_types.store import read_definition, workflow_from_preset, workflow_to_json


def preset_workflow_json(conn: sqlite3.Connection, worker_type: str = "coding") -> str:
    return workflow_to_json(workflow_from_preset(read_definition(conn, worker_type)))
