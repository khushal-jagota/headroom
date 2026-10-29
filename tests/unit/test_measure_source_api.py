"""HTTP/status resource parity, compact projections, and actual-outcome status."""

import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Any, cast

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from tests.support.measure_sources import configured_source
from tests.unit.test_scheduled_ticket_authority import _app

from planner.cli.main import main
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.measure_sources import actions
from planner.measure_sources.contracts import SourceScriptBody


def _guidance(*arguments: str) -> dict[str, Any]:
    result = CliRunner().invoke(main, [*arguments, "--json"])
    assert result.exit_code == 0, result.output
    return cast(dict[str, Any], json.loads(result.output))


def _schema_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


def _reference(document: dict[str, Any], reference: str) -> dict[str, Any]:
    assert reference.startswith("#/"), reference
    value = document
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _schema_shape(
    value: Any, document: dict[str, Any], active: frozenset[str] = frozenset()
) -> Any:
    """Compare semantics across CLI $defs and OpenAPI components, including cycles."""
    if isinstance(value, list):
        return [_schema_shape(child, document, active) for child in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        target = _reference(document, value["$ref"])
        name = target.get("title", value["$ref"].rsplit("/", 1)[-1])
        if name in active:
            return {"recursive": name}
        return _schema_shape(target, document, active | {name})
    return {
        key: _schema_shape(child, document, active)
        for key, child in value.items()
        if key not in {"$defs", "title", "description"}
    }


def test_cli_and_openapi_publish_closed_recursive_authoring_contracts(tmp_db: Connection) -> None:
    with TestClient(_app(tmp_db)) as client:
        document = client.get("/openapi.json").json()
    for node in _schema_nodes(document):
        if "$ref" in node:
            _reference(document, node["$ref"])
    contracts = {}
    for arguments, path, method in (
        (("goal", "schema", "create"), "/api/goals", "post"),
        (("goal", "schema", "edit"), "/api/goals/{goal_id}", "patch"),
        (("measure-source", "schema"), "/api/measure-sources", "post"),
        (("measure", "schema"), "/api/measures", "post"),
    ):
        published = _guidance(*arguments)
        schema = published["schema"]
        contracts[arguments[0] + method] = schema
        for node in _schema_nodes(schema):
            if "$ref" in node:
                _reference(schema, node["$ref"])
            if "properties" in node:
                assert node["additionalProperties"] is False, node
        media = document["paths"][path][method]["requestBody"]["content"]["application/json"]
        assert _schema_shape(media["schema"], document) == _schema_shape(schema, schema)
        assert {name: entry["value"] for name, entry in media["examples"].items()} == published[
            "examples"
        ]
    create_schema = _schema_shape(contracts["goalpost"], contracts["goalpost"])
    edit_schema = _schema_shape(contracts["goalpatch"], contracts["goalpatch"])
    assert set(create_schema["required"]) == {"title", "project_id"}
    assert "expected_revision" in create_schema["properties"]
    assert edit_schema["required"] == ["expected_revision"]
    for field in ("deadline", "primary_chart", "primary_checklist_block_id"):
        assert {"type": "null"} in create_schema["properties"][field]["anyOf"]
    measure_schema = _schema_shape(contracts["measurepost"], contracts["measurepost"])
    assert set(measure_schema["required"]) == {
        "project_id",
        "name",
        "unit",
        "origin",
        "kind",
        "timezone",
    }
    for field in ("period", "source_id", "formula"):
        assert {"type": "null"} in measure_schema["properties"][field]["anyOf"]
    source_schema = _schema_shape(contracts["measure-sourcepost"], contracts["measure-sourcepost"])
    assert set(source_schema["required"]) == {"name", "project_id", "account", "responsible"}
    assert set(source_schema["properties"]["responsible"]["required"]) == {"kind", "id"}

    # Author input omits only IDs and placement that the writer actually supplies.
    schema = contracts["goalpost"]
    for name in ("ChartBlock", "ChecklistItem", "TextBlock", "SegmentedView"):
        definition = next(node for node in _schema_nodes(schema) if node.get("title") == name)
        assert ("id" in definition["required"]) is (name == "SegmentedView")
        if name == "ChartBlock":
            assert "placement" not in definition["required"]
    chart = _guidance("goal", "schema", "primary-chart")["schema"]
    for node in _schema_nodes(chart):
        if "$ref" in node:
            _reference(chart, node["$ref"])
    assert _schema_shape(chart, chart) == next(
        item
        for item in create_schema["properties"]["primary_chart"]["anyOf"]
        if item.get("type") == "object"
    )


def test_published_examples_complete_writes_and_diagnose_recorded_failures(
    tmp_db: Connection,
) -> None:
    """The reference must be usable at the canonical writer, with safe repair guidance."""
    from tests.unit.test_goal_pages import create

    goal_body = _guidance("goal", "schema", "create")["examples"]["chief_goal"]
    source_body = _guidance("measure-source", "schema")["examples"]["goal_source"]
    measure_body = _guidance("measure", "schema")["examples"]["taken_daily_count"]
    chart_body = _guidance("goal", "schema", "edit")["examples"]["primary_line_chart"]
    foreign = create(tmp_db)
    foreign_headers = {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": foreign.id}
    sentinel = "private-provider-value-do-not-echo"

    def invalid(response: Any, *fragments: str) -> None:
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "validation"
        message = response.json()["error"]["message"]
        for fragment in fragments:
            assert fragment in message, message
        assert sentinel not in response.text

    with TestClient(_app(tmp_db)) as client:
        goal_body["project_id"] = "project_vylo"
        created = client.post("/api/goals", json=goal_body, headers={"X-Plan-Actor": "chief"})
        assert created.status_code == 200, created.text
        goal = created.json()
        goal_id = goal["id"]
        headers = {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal_id}
        source_body["project_id"] = "project_vylo"
        source_body["responsible"]["id"] = goal_id

        # Populated native failure: responsible.goal_id was guessed instead of id.
        guessed_source = deepcopy(source_body)
        guessed_source["responsible"] = {"kind": "goal", "goal_id": sentinel}
        invalid(
            client.post("/api/measure-sources", json=guessed_source, headers=headers),
            "$.responsible.goal_id",
            "unknown",
        )
        invalid(
            client.post(
                "/api/measure-sources",
                json={**source_body, "responsible": {"kind": "goal"}},
                headers=headers,
            ),
            "$.responsible.id",
            "required",
        )
        invalid(
            client.post(
                "/api/measure-sources",
                json={**source_body, "responsible": {"kind": sentinel, "id": sentinel}},
                headers=headers,
            ),
            "$.responsible.kind",
            "chief",
            "manager",
            "goal",
        )
        source_response = client.post("/api/measure-sources", json=source_body, headers=headers)
        assert source_response.status_code == 200, source_response.text
        assert source_response.json()["responsible"] == {"kind": "goal", "id": goal_id}
        source_id = source_response.json()["id"]
        measure_body.update(project_id="project_vylo", source_id=source_id)

        # Populated native failure had guessed fields, no origin/timezone, and kind:number.
        guessed_measure = {
            "name": "Captured exception events",
            "project_id": "project_vylo",
            "source_id": source_id,
            "unit": "events",
            "kind": "number",
            "value_type": "number",
            "description": sentinel,
        }
        invalid(client.post("/api/measures", json=guessed_measure, headers=headers), "unknown")
        for field, supported in (
            ("origin", ("taken", "assembled", "manual")),
            ("kind", ("reading", "period_count")),
            ("period", ("day", "week", "month")),
        ):
            invalid(
                client.post(
                    "/api/measures", json={**measure_body, field: sentinel}, headers=headers
                ),
                f"$.{field}",
                *supported,
            )
        missing_origin = {key: value for key, value in measure_body.items() if key != "origin"}
        invalid(
            client.post("/api/measures", json=missing_origin, headers=headers),
            "$.origin",
            "required",
        )
        measured = client.post("/api/measures", json=measure_body, headers=headers)
        assert measured.status_code == 200, measured.text
        measure = measured.json()
        assert (measure["origin"], measure["kind"], measure["timezone"], measure["period"]) == (
            "taken",
            "period_count",
            "UTC",
            "day",
        )
        assert measure["formula"] is None
        chart_body["expected_revision"] = goal["revision"]
        chart_body["primary_chart"]["measure_id"] = measure["id"]

        # Populated native chart failure; discriminator-only probes are checked separately.
        guessed_chart = {
            "id": "daily-exceptions",
            "type": "line",
            "title": sentinel,
            "measure_id": measure["id"],
            "unit": "events",
            "target": 5,
            "target_direction": "below",
        }
        invalid(
            client.patch(
                f"/api/goals/{goal_id}",
                headers=headers,
                json={**chart_body, "primary_chart": guessed_chart},
            ),
            "$.primary_chart.target_direction",
            "unknown",
        )
        invalid(
            client.patch(
                f"/api/goals/{goal_id}",
                headers=headers,
                json={**chart_body, "primary_chart": {"type": sentinel}},
            ),
            "$.primary_chart.type",
            "chart",
        )
        invalid(
            client.patch(
                f"/api/goals/{goal_id}",
                headers=headers,
                json={
                    **chart_body,
                    "primary_chart": {**chart_body["primary_chart"], "kind": sentinel},
                },
            ),
            "$.primary_chart.kind",
            "line",
            "bar",
            "gauge",
        )
        # Empty objects were deliberate setup probes, not failed valid product operations.
        for path in ("/api/measure-sources", "/api/measures"):
            invalid(client.post(path, json={}, headers=headers), "required")
        invalid(
            client.patch(
                f"/api/goals/{goal_id}",
                headers=headers,
                json={**chart_body, "primary_chart": {}},
            ),
            "$.primary_chart.type",
            "required",
        )
        # A field name containing unsafe text must not be reflected either.
        invalid(
            client.post(
                "/api/measure-sources",
                headers=headers,
                json={**source_body, sentinel + "\n": sentinel},
            ),
            "unknown",
        )

        # Runtime parsing order is unchanged: source/measure syntax precedes authority;
        # goal edits check ownership, then revision, before inspecting authored content.
        invalid(
            client.post("/api/measure-sources", json=guessed_source, headers=foreign_headers),
            "$.responsible.goal_id",
        )
        invalid(
            client.post("/api/measures", json=guessed_measure, headers=foreign_headers), "unknown"
        )
        assert (
            client.post("/api/measures", json=measure_body, headers=foreign_headers).json()[
                "error"
            ]["code"]
            == "agent_forbidden"
        )
        assert (
            client.patch(
                f"/api/goals/{goal_id}",
                headers=foreign_headers,
                json={"expected_revision": 0, "primary_chart": guessed_chart},
            ).json()["error"]["code"]
            == "agent_forbidden"
        )
        assert (
            client.patch(
                f"/api/goals/{goal_id}",
                headers=headers,
                json={"expected_revision": 0, "primary_chart": guessed_chart},
            ).json()["error"]["code"]
            == "stale_claim"
        )
        saved = client.patch(f"/api/goals/{goal_id}", json=chart_body, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["primary_chart"]["measure_id"] == measure["id"]
        assert saved.json()["primary_chart"]["kind"] == "line"

        # Nullable omitted wire fields still receive parser defaults, rather than becoming
        # required because a documentation TypeAdapter was attached to the route.
        reading_body = {
            key: value for key, value in measure_body.items() if key not in {"period", "formula"}
        }
        reading_body["kind"] = "reading"
        reading = client.post("/api/measures", json=reading_body, headers=headers)
        assert reading.status_code == 200, reading.text
        assert reading.json()["period"] is None and reading.json()["formula"] is None
        manual_body = {key: value for key, value in reading_body.items() if key != "source_id"}
        manual_body["origin"] = "manual"
        manual = client.post("/api/measures", json=manual_body)
        assert manual.status_code == 200, manual.text
        assert manual.json()["source_id"] is None
        minimal = client.post(
            "/api/goals",
            headers={"X-Plan-Actor": "chief"},
            json={"title": "Defaults", "project_id": "project_vylo", "expected_revision": 99},
        )
        assert minimal.status_code == 200, minimal.text
        assert minimal.json()["primary_chart"] is None
        assert minimal.json()["deadline"] is None
        assert minimal.json()["page"]["blocks"] == []


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
