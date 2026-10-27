"""HTTP/status resource parity, compact projections, and idle clock invalidation."""

from datetime import UTC, datetime
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.measure_sources import configured_source
from tests.unit.test_scheduled_ticket_authority import _app

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.measure_sources import actions
from planner.measure_sources.contracts import SourceScriptBody


def test_source_http_resources_and_clock_revision(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    app = _app(tmp_db)
    now = int(datetime(2026, 10, 27, 11, 59, tzinfo=UTC).timestamp())
    clock = FakeClock(datetime.fromtimestamp(now, UTC))
    app.state.clock = clock
    source, measure = configured_source(tmp_db, now=now, local_time="12:00")
    with TestClient(app) as client:
        # All normal and versioned reads share the same bounded public shape.
        for key in (
            "/api/measure-sources",
            f"/api/measure-sources/{source.id}/status",
            f"/api/measure-sources/{source.id}/runs",
            "/api/measures",
            "/api/credentials",
        ):
            direct = client.get(key)
            assert direct.status_code == 200, direct.text
            versioned = client.get("/api/resource-body", params={"key": key})
            assert versioned.status_code == 200, versioned.text
            assert versioned.json()["body"] == direct.json()
        body = client.get("/api/measure-sources").json()
        assert body["sources"][0]["source"]["id"] == source.id
        assert "active_script" not in body["sources"][0]["source"]
        assert body["sources"][0]["latest_values"][0]["name"] == measure.name
        key = f"/api/measure-sources/{source.id}/status"
        before = client.get("/api/resource-body", params={"key": key}).json()
        boundary = before["body"]["next_status_change_at"]
        assert boundary is not None
        clock.set(datetime.fromtimestamp(boundary, UTC))
        after = client.get("/api/resource-body", params={"key": key}).json()
        assert after["revision"] != before["revision"]
        assert after["body"]["health"] == "stale"
        assert after["body"]["latest_values"][0]["observation"] is None
        assert client.get("/api/measure-sources", params={"limit": 101}).status_code == 400
        pending = actions.submit_script(
            tmp_db,
            source.id,
            SourceScriptBody(
                expected_revision=source.source_revision,
                expected_pending_revision=None,
                text="pass # draft",
            ),
            principal=OWNER_PRINCIPAL,
            now=now,
        )
        assert pending.pending_script is not None
        response = client.post(
            f"/api/measure-sources/{source.id}/runs",
            json={
                "mode": "test",
                "expected_script_revision": pending.pending_script.revision,
                "history": None,
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["id"]
        detail = client.get(f"/api/measure-source-runs/{run_id}").json()
        assert detail["run"]["status"] == "pending" and detail["test_candidates"] is None


def test_explicit_runtime_constructor_executes_source_only_lifespan(tmp_db: Connection) -> None:
    import threading
    import time

    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app
    from planner.credentials.contracts import ResolvedCredential
    from planner.measure_sources.contracts import RunAdmission, RunMetrics, RunResult
    from planner.measures.contracts import CandidateObservation

    class FixtureRuntime:
        def __init__(self) -> None:
            self.completed = threading.Event()

        def cancel(self, run_id: str) -> None:
            pass

        def execute(
            self, admission: RunAdmission, credentials: tuple[ResolvedCredential, ...]
        ) -> RunResult:
            self.completed.set()
            return RunResult(
                tuple(
                    CandidateObservation(output.measure_id, output.required_date, 7)
                    for output in admission.run.required_outputs
                ),
                None,
                RunMetrics(0, 0, 0, 1, 0, 0),
            )

    database = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": database})
    now = int(datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp())
    clock = FakeClock(datetime.fromtimestamp(now, UTC))
    source, _ = configured_source(tmp_db, now=now, local_time="13:00")
    assert source.active_script is not None
    runtime = FixtureRuntime()
    app = create_app(cfg, clock, lambda: connect(database), source_runtime_for_test=runtime)
    ticket_count = tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    with TestClient(app) as client:
        response = client.post(
            f"/api/measure-sources/{source.id}/runs",
            json={
                "mode": "collection",
                "expected_script_revision": source.active_script.revision,
            },
        )
        assert response.status_code == 200, response.text
        assert runtime.completed.wait(3)
        deadline = time.monotonic() + 3
        run_id = response.json()["id"]
        while True:
            row = client.get(f"/api/measure-source-runs/{run_id}").json()
            if row["run"]["status"] == "succeeded":
                break
            assert time.monotonic() < deadline, row
            time.sleep(0.01)
        assert tmp_db.execute("SELECT value FROM measure_observations").fetchone()[0] == 7
    assert tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == ticket_count
    assert not any(thread.name.startswith("measure-source") for thread in threading.enumerate())
