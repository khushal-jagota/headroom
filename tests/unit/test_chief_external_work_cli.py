from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main as cli_main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_chief_external_work_cli_group_is_absent() -> None:
    result = CliRunner().invoke(cli_main, ["chief", "--help"])
    assert result.exit_code == 2
    assert "No such command 'chief'" in result.output


def test_chief_external_work_routes_are_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "removed-chief-routes.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)}
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), conn_factory)
    with TestClient(app) as client:
        create_response = client.post("/api/chief/tickets/from-external-work", json={})
        reconcile_response = client.post(
            "/api/chief/tickets/t_missing/reconcile-from-external-work", json={}
        )

    assert create_response.status_code == 404
    assert reconcile_response.status_code == 404
