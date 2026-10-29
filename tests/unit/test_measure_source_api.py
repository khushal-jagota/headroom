"""HTTP/status resource parity, compact projections, and actual-outcome status."""

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


def test_source_http_resources_and_actual_outcome_status(
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
        assert "next_status_change_at" not in before["body"]
        clock.set(datetime.fromtimestamp(now + 86400, UTC))
        after = client.get("/api/resource-body", params={"key": key}).json()
        assert after["revision"] == before["revision"]
        assert after["body"]["health"] == "never_collected"
        assert after["body"]["latest_values"][0]["observation"] is None
        assert client.get("/api/measure-sources", params={"limit": 101}).status_code == 400
        pending = actions.submit_script(
            tmp_db,
            source.id,
            SourceScriptBody(
                expected_revision=source.source_revision,
                text="pass # saved",
            ),
            principal=OWNER_PRINCIPAL,
            now=now,
        )
        assert pending.active_script is not None
        response = client.post(
            f"/api/measure-sources/{source.id}/runs",
            json={
                "mode": "test",
                "expected_script_revision": pending.active_script.revision,
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
    from planner.credentials.contracts import CredentialResolver
    from planner.measure_sources.contracts import RunAdmission, RunMetrics, RunResult
    from planner.measures.contracts import CandidateObservation

    class FixtureRuntime:
        def __init__(self) -> None:
            self.completed = threading.Event()

        def cancel(self, run_id: str) -> None:
            pass

        def execute(self, admission: RunAdmission, credentials: CredentialResolver) -> RunResult:
            self.completed.set()
            return RunResult(
                tuple(
                    CandidateObservation(output.measure_id, output.required_date, 7)
                    for output in admission.run.required_outputs
                ),
                None,
                RunMetrics(0, 0, 0, 1, 0),
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


def test_goal_source_authority_covers_own_definitions_scripts_runs_and_schedule(
    tmp_db: Connection,
) -> None:
    from tests.unit.test_goal_pages import create

    from planner.core.contracts import Principal, PrincipalKind
    from planner.datasets.data import read_dataset
    from planner.measure_sources import data

    own, other = create(tmp_db), create(tmp_db)
    actor = Principal(PrincipalKind.goal, own.id)
    headers = {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": own.id}
    other_headers = {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": other.id}
    managed, _ = configured_source(tmp_db)
    with TestClient(_app(tmp_db)) as client:
        body = {
            "name": "Goal source",
            "project_id": "project_vylo",
            "account": "fixture",
            "responsible": {"kind": "goal", "id": own.id},
        }
        created = client.post("/api/measure-sources", json=body, headers=headers)
        assert created.status_code == 200, created.text
        source_id = created.json()["id"]
        assert created.json()["responsible"] == {"kind": "goal", "id": own.id}
        assert data.read_source(tmp_db, source_id).responsible == actor
        assert (
            client.post("/api/measure-sources", json=body, headers=other_headers).json()["error"][
                "code"
            ]
            == "agent_forbidden"
        )
        table = client.post(
            "/api/datasets",
            headers=headers,
            json={
                "project_id": "project_vylo",
                "source_id": source_id,
                "name": "Own categories",
                "timezone": "UTC",
                "dimensions": [{"name": "region", "label": "Region"}],
                "fields": [
                    {"name": "events", "label": "Events", "unit": "events", "kind": "count"}
                ],
            },
        )
        assert table.status_code == 200, table.text
        assert read_dataset(tmp_db, table.json()["id"]).source_id == source_id
        # A dataset alone prevents source deletion before any scalar definition exists.
        assert (
            client.delete(f"/api/measure-sources/{source_id}", headers=headers).status_code == 400
        )
        measure = client.post(
            "/api/measures",
            headers=headers,
            json={
                "project_id": "project_vylo",
                "source_id": source_id,
                "name": "Own reading",
                "unit": "events",
                "origin": "taken",
                "kind": "reading",
                "timezone": "UTC",
            },
        )
        assert measure.status_code == 200, measure.text
        assert (
            client.patch(
                f"/api/datasets/{table.json()['id']}",
                headers=other_headers,
                json={"expected_revision": 1, "name": "Foreign"},
            ).json()["error"]["code"]
            == "agent_forbidden"
        )
        source = data.read_source(tmp_db, source_id)
        submitted = client.put(
            f"/api/measure-sources/{source_id}/script",
            headers=headers,
            json={"expected_revision": source.source_revision, "text": "pass"},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["active_script"]["authored_by"] == {"kind": "goal", "id": own.id}
        source = data.read_source(tmp_db, source_id)
        scheduled = client.put(
            f"/api/measure-sources/{source_id}/schedule",
            headers=headers,
            json={
                "expected_revision": source.source_revision,
                "enabled": True,
                "local_time": "23:59",
            },
        )
        assert scheduled.status_code == 200, scheduled.text
        own_script = submitted.json()["active_script"]["revision"]
        requested = client.post(
            f"/api/measure-sources/{source_id}/runs",
            headers=headers,
            json={"mode": "test", "expected_script_revision": own_script},
        )
        assert requested.status_code == 200, requested.text
        assert requested.json()["dataset_outputs"][0]["dataset_id"] == table.json()["id"]
        for target in (source_id, managed.id):
            foreign = other_headers if target == source_id else headers
            current = data.read_source(tmp_db, target)
            assert (
                client.put(
                    f"/api/measure-sources/{target}/script",
                    headers=foreign,
                    json={"expected_revision": current.source_revision, "text": "pass # foreign"},
                ).json()["error"]["code"]
                == "agent_forbidden"
            )
            assert (
                client.put(
                    f"/api/measure-sources/{target}/schedule",
                    headers=foreign,
                    json={
                        "expected_revision": current.source_revision,
                        "enabled": False,
                        "local_time": None,
                    },
                ).json()["error"]["code"]
                == "agent_forbidden"
            )
            assert current.active_script is not None
            assert (
                client.post(
                    f"/api/measure-sources/{target}/runs",
                    headers=foreign,
                    json={
                        "mode": "collection",
                        "expected_script_revision": current.active_script.revision,
                    },
                ).json()["error"]["code"]
                == "agent_forbidden"
            )
        assert (
            client.put(
                f"/api/measure-sources/{source_id}/responsible",
                headers=headers,
                json={
                    "expected_responsibility_revision": 1,
                    "responsible": {"kind": "goal", "id": other.id},
                },
            ).json()["error"]["code"]
            == "agent_forbidden"
        )
        assert (
            client.delete(f"/api/measure-sources/{source_id}", headers=headers).status_code == 400
        )
        assert (
            client.post(
                "/api/measure-sources",
                json={**body, "responsible": {"kind": "goal", "id": "missing"}},
            ).status_code
            == 400
        )
