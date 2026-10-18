"""Accepted staging source rewrites and navigation share one migration transaction.

All databases and runtime files are synthetic and remain under tmp_path.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from sqlalchemy import Engine

from planner.core.db import _alembic_config, _migration_engine
from planner.navigation import data
from planner.projects.data import seed_default_projects
from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types


def historical_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Inspect frozen schema 2 without admitting it through current public reads."""
    meta = conn.execute(
        "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
    ).fetchone()
    return {
        "scope": meta[0],
        "schema_version": meta[1],
        "epoch": meta[2],
        "revision": str(meta[3]),
        "records": [
            {"kind": kind, "id": identity, "value": json.loads(value)}
            for kind, identity, value in conn.execute(
                "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
            )
        ],
    }


def historical_database(path: Path) -> Engine:
    engine = _migration_engine(str(path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "navigation_delivery_proof")
        command.upgrade(_alembic_config(connection), "current_model_variants")
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        seed_default_projects(raw)
        seed_shipped_worker_types(raw)
        seed_chief_settings(raw)
        definition = json.loads(
            raw.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
            ).fetchone()[0]
        )
        definition["fields"].append({"id": "retirement", "label": "Retirement"})
        definition["stages"].append(
            {
                "id": "cancelled",
                "label": "Cancelled",
                "is_terminal": True,
                "gating_field": None,
                "ownership_mode": None,
            }
        )
        raw.execute(
            "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
            (json.dumps(definition),),
        )
        raw.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) "
            "VALUES('kept','Kept','coding','codex','needs_plan','done',?,1,1)",
            (json.dumps({"brief": "Keep", "retirement": "Old"}),),
        )
        data.finalize(raw)
    return engine


def test_source_rewrite_and_projection_rebuild_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = historical_database(tmp_path / "synthetic.db")
    with engine.begin() as connection:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        before = list(raw.iterdump())
        old = historical_snapshot(raw)
    rebuild = data.rebuild

    def fail_after_rebuild(conn: sqlite3.Connection) -> None:
        rebuild(conn)
        raise RuntimeError("synthetic reconciliation failure")

    # Alembic imports the function from this module at revision load time.
    with monkeypatch.context() as patch:
        patch.setattr(data, "rebuild", fail_after_rebuild)
        with pytest.raises(RuntimeError, match="synthetic reconciliation failure"):
            with engine.begin() as connection:
                command.upgrade(_alembic_config(connection), "head")
    with engine.begin() as connection:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        assert list(raw.iterdump()) == before
        assert historical_snapshot(raw) == old
        command.upgrade(_alembic_config(connection), "head")
        current = historical_snapshot(raw)
        assert current["scope"] == old["scope"]
        assert current["epoch"] != old["epoch"]
        assert current["revision"] == "0"
        assert json.loads(raw.execute("SELECT field_values FROM tickets").fetchone()[0]) == {
            "brief": "Keep"
        }
        definition = json.loads(
            raw.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
            ).fetchone()[0]
        )
        assert "retirement" not in {field["id"] for field in definition["fields"]}
        assert "cancelled" not in {stage["id"] for stage in definition["stages"]}
        assert raw.execute("SELECT COUNT(*) FROM navigation_dirty").fetchone()[0] == 0
        with pytest.raises(RuntimeError, match="current schema"):
            data.journal(raw, old["epoch"], old["revision"])
        data.rebuild(raw)
        assert historical_snapshot(raw)["records"] == current["records"]
    engine.dispose()


@pytest.mark.parametrize("invalid_source", ["cancelled", "custom-retirement-stage"])
def test_source_refusal_preserves_historical_projection(
    tmp_path: Path, invalid_source: str
) -> None:
    engine = historical_database(tmp_path / "cancelled.db")
    with engine.begin() as connection:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        if invalid_source == "cancelled":
            raw.execute("UPDATE tickets SET stage='cancelled',worker_step_claim='errored'")
        else:
            definition = json.loads(
                raw.execute(
                    "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
                ).fetchone()[0]
            )
            next(stage for stage in definition["stages"] if stage["id"] == "needs_plan")[
                "gating_field"
            ] = "retirement"
            raw.execute(
                "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
                (json.dumps(definition),),
            )
        data.finalize(raw)
        old = historical_snapshot(raw)
        ticket = next(record for record in old["records"] if record["kind"] == "ticket")
        assert ticket["value"]["ticket_status"] == "empty"
        before = list(raw.iterdump())
    refusal = "archive-cancelled-tickets" if invalid_source == "cancelled" else "replacement field"
    with pytest.raises(RuntimeError, match=refusal):
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "head")
    with engine.begin() as connection:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        assert list(raw.iterdump()) == before
        assert historical_snapshot(raw) == old
    engine.dispose()
