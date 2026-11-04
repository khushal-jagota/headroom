"""HTTP/CLI parity and retained source collection across real existing boundaries."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.attention.configuration import DAILY_METRICS
from planner.cli import http
from planner.cli.main import main
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.measure_sources import data as sources
from planner.measure_sources import run_actions
from planner.measure_sources.admission import prepare_admission
from planner.measure_sources.contracts import RunMode, RunStatus
from planner.measure_sources.runtime import Runtime
from planner.measures.contracts import HistoryWindow

NOW = int(datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp())
DAY = "2026-11-04"


@pytest.fixture
def attention_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Connection, TestClient, Path]]:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)
    database = tmp_path / "attention.db"
    conn = connect(str(database))
    create_schema(conn)
    # Existing history before activation remains unknown. This fixture activates
    # prospective capture at a fixed instant before the tested dates.
    conn.execute(
        "UPDATE attention_history_metadata SET started_at=? WHERE singleton=1", (NOW - 86400 * 20,)
    )
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES('attention-proof','codex','/fixture','full',?)",
        (NOW - 86400 * 20,),
    )
    conn.execute(
        "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) "
        "VALUES('attention-proof',1,'prompt',?,?)",
        (
            json.dumps(
                {
                    "content": [],
                    "sender": {"kind": "owner", "id": "owner"},
                    "recipient": {"kind": "chief", "id": "chief"},
                    "sender_message_id": "owner-proof",
                    "sender_label": "Khushal",
                    "mode": "steer",
                }
            ),
            NOW - 60,
        ),
    )
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(database),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        },
    )
    app = create_app(
        config,
        FakeClock(datetime.fromtimestamp(NOW, UTC)),
        lambda: connect(str(database)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    client = TestClient(app)

    def send(method: str, path: str, **kwargs: Any) -> Any:
        response = client.request(
            method, path, params=kwargs.get("params"), json=kwargs.get("json_body")
        )
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(http, "send", send)
    yield conn, client, database
    client.close()
    conn.close()


def test_summary_cli_matches_http_and_publishes_historical_contract(
    attention_app: tuple[Connection, TestClient, Path],
) -> None:
    _, client, _ = attention_app
    params = {"first_date": DAY, "last_date": DAY, "group_by": "project"}
    response = client.get("/api/attention/summary", params=params)
    assert response.status_code == 200
    expected = response.json()
    assert expected["totals"]["current"]["owner_messages"]["value"] == 1
    result = CliRunner().invoke(
        main,
        [
            "attention",
            "summary",
            "--first-date",
            DAY,
            "--last-date",
            DAY,
            "--group-by",
            "project",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == expected
    selected = CliRunner().invoke(
        main, ["attention", "summary", "--chief", "--project", "project_vylo", "--json"]
    )
    assert selected.exit_code != 0
    assert "only one" in selected.output
    parameters = client.get("/openapi.json").json()["paths"]["/api/attention/summary"]["get"][
        "parameters"
    ]
    assert {item["name"] for item in parameters} >= {
        "first_date",
        "last_date",
        "scope_kind",
        "scope_id",
        "group_by",
    }
    assert client.get("/api/attention/demands").status_code == 404


def test_cli_setup_real_runtime_and_retained_dataset_reads(
    attention_app: tuple[Connection, TestClient, Path],
    tmp_path: Path,
) -> None:
    conn, client, database = attention_app
    args = [
        "attention",
        "setup-collection",
        "--project",
        "project_vylo",
        "--database-path",
        str(database),
        "--json",
    ]
    first = CliRunner().invoke(main, args)
    assert first.exit_code == 0, first.output
    configured = json.loads(first.output)
    again = CliRunner().invoke(main, args)
    assert again.exit_code == 0, again.output
    assert json.loads(again.output) == configured
    source = sources.read_source(conn, configured["source_id"])
    assert source.active_script is not None
    run = run_actions.request_run(
        conn,
        source.id,
        mode=RunMode.collection,
        history=HistoryWindow(DAY, DAY),
        expected_script_revision=source.active_script.revision,
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    claimed = run_actions.claim_run(conn, run.id, now=NOW)
    assert claimed is not None
    admission, credentials = prepare_admission(conn, claimed, key_path=tmp_path / "unused.key")
    result = Runtime().execute(admission, credentials)
    assert result.failure is None, result.failure
    completed = run_actions.complete_run(conn, run.id, result, now=NOW)
    assert completed.run.status is RunStatus.succeeded
    values = client.post(
        "/api/datasets/read",
        json={
            "dataset_ids": list(configured["dataset_ids"].values()),
            "first_date": DAY,
            "last_date": DAY,
        },
    )
    assert values.status_code == 200, values.text
    retained = values.json()
    assert len(retained["results"]) == len(DAILY_METRICS)
    message_result = next(
        item
        for item in retained["results"]
        if item["dataset_id"] == configured["dataset_ids"]["owner_messages"]
    )
    assert sum(row["values"]["value"] for row in message_result["rows"]) == 1
    assert message_result["query_end_at"] == NOW
    assert message_result["query_start_at"] < NOW
    assert message_result["provenance"]["run_id"] == run.id
    assert message_result["limitation"] is not None  # today remains partial
    assert source.enabled

    # A backfill before prospective approval history must retain an unavailable
    # result, rather than publish a plausible zero in a normal numeric dataset.
    old_day = "2026-10-14"
    old_run = run_actions.request_run(
        conn,
        source.id,
        mode=RunMode.collection,
        history=HistoryWindow(old_day, old_day),
        expected_script_revision=source.active_script.revision,
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    claimed_old = run_actions.claim_run(conn, old_run.id, now=NOW)
    assert claimed_old is not None
    admission_old, credentials_old = prepare_admission(
        conn, claimed_old, key_path=tmp_path / "unused.key"
    )
    result_old = Runtime().execute(admission_old, credentials_old)
    assert result_old.failure is None, result_old.failure
    assert (
        run_actions.complete_run(conn, old_run.id, result_old, now=NOW).run.status
        is RunStatus.succeeded
    )
    old_values = client.post(
        "/api/datasets/read",
        json={
            "dataset_ids": [configured["dataset_ids"]["owner_approvals"]],
            "first_date": old_day,
            "last_date": old_day,
        },
    ).json()["results"]
    assert len(old_values) == 1
    assert old_values[0]["rows"] == []
    assert old_values[0]["unavailable_reason"] == "provider_has_no_history"
