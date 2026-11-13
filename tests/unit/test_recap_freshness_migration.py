"""The real upgrade must not invent ages for historical recap text.

Ordinary writer coverage starts at the current schema. This previous-head fixture
checks retained text, null timestamps, and the fresh stage-change flag on upgrade.
"""

import json
from contextlib import closing
from pathlib import Path

from tests.support.migration_head import shipped_migration_head

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.worker_types.shipped import shipped_worker_types
from planner.worker_types.store import definition_from_json, workflow_from_preset, workflow_to_json


def test_historical_recaps_keep_unknown_age_after_upgrade(tmp_path: Path) -> None:
    path = str(tmp_path / "historical-recaps.db")
    coding = next(value for value in shipped_worker_types() if value["worker_type"] == "coding")
    workflow = workflow_to_json(workflow_from_preset(definition_from_json(json.dumps(coding))))
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "ticket_decision_messages")
    with closing(connect(path)) as conn:
        with conn:
            conn.execute(
                "INSERT INTO projects(id,name,created_at,updated_at) VALUES('p','Project',1,2)"
            )
            conn.execute(
                "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
                "created_at,updated_at,recap) "
                "VALUES('m','Manager','p','codex','gpt-6.1-sol',1,999,'Old manager recap')"
            )
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,workflow_json,created_at,updated_at,recap) "
                "VALUES('t','Ticket',NULL,'codex','needs_brief','needs_brief',"
                "'{}',?,1,999,'Old ticket recap')",
                (workflow,),
            )
        # The deployed previous build uses the installed v10 navigation projection.
        with conn:
            conn.execute("UPDATE navigation_meta SET schema_version=10")
        create_schema(conn)
        assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 11
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        ticket = conn.execute(
            "SELECT recap,recap_updated_at,recap_stage_changed FROM tickets WHERE id='t'"
        ).fetchone()
        manager = conn.execute(
            "SELECT recap,recap_updated_at FROM managers WHERE id='m'"
        ).fetchone()
        assert tuple(ticket) == ("Old ticket recap", None, 0)
        assert tuple(manager) == ("Old manager recap", None)
