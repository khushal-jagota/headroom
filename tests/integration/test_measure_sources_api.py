"""Backend API -> stored script -> real HTTP -> persisted values, without a browser.

This replaces the browser source proof. Runtime-only tests do not connect credential
writes, scheduled admission, source failure/recovery, and the retained data API.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, Never

import pytest
from fastapi.testclient import TestClient
from tests.support.model_catalogue import seed_test_model_catalogue

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ConversationBackendKey, ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.snapshot import SubprocessBackendProbeEnvironment
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.measure_sources.runtime import Runtime

FIRST_TOKEN = "fixture-first-key"
SECOND_TOKEN = "fixture-replacement-key"


class Provider:
    def __init__(self) -> None:
        self.token = FIRST_TOKEN
        self.value = 42
        self.accepted = 0
        self.refused = 0
        self.post_queries = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert body["query"]["kind"] == "HogQLQuery"
                assert "count()" in body["query"]["query"]
                owner.post_queries += 1
                self.do_GET()

            def do_GET(self) -> None:
                authorized = self.headers.get("Authorization") == "Bearer " + owner.token
                if authorized:
                    owner.accepted += 1
                else:
                    owner.refused += 1
                body = {"results": [[25]]} if self.command == "POST" else {"value": owner.value}
                raw = json.dumps(body if authorized else {"error": "unauthorized"}).encode()
                self.send_response(200 if authorized else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        # Port zero reserves an available local port atomically.
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class NoInstalledBackends(SubprocessBackendProbeEnvironment):
    def executable_path(self, executable_name: str) -> str | None:
        return None

    def configured_executable_path(self, executable_name: str) -> str | None:
        return None


def refuse_backend(
    *,
    resolved_start: ResolvedConversationStart,
    event_sink: BackendEventSink,
    message_files: ConversationMessageFiles,
) -> Never:
    raise AssertionError("source collection must not start an agent")


@pytest.fixture
def source_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Provider, Path]]:
    for key in tuple(os.environ):
        if key.startswith(("PLAN_", "PANELS_REAL_")) or key in (
            "HERMES_HOME",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
        ):
            monkeypatch.delenv(key)
    monkeypatch.setattr(
        "planner.conversation.snapshot.SubprocessBackendProbeEnvironment",
        NoInstalledBackends,
    )
    database = tmp_path / "planning.db"
    with closing(connect(str(database))) as conn:
        create_schema(conn)
        seed_test_model_catalogue(conn)
    config = load_config(
        None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(database),
            "PLAN_FAKE_NOW": "2026-10-27T12:00:00+00:00",
            "PLAN_TICK_SECONDS": "1",
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "secrets" / "key"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "server.sock"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(database)),
        source_runtime_for_test=Runtime(),
        backend_child_factories_for_test={key: refuse_backend for key in ConversationBackendKey},
    )
    provider = Provider()
    try:
        with TestClient(app) as client:
            yield client, provider, database
    finally:
        provider.stop()
    assert not any(thread.name.startswith("measure-source") for thread in threading.enumerate())


def test_scheduled_source_api_credential_recovery_and_second_source(
    source_client: tuple[TestClient, Provider, Path],
) -> None:
    client, provider, database = source_client

    def request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = client.request(method, path, json=body)
        assert response.is_success, (path, response.status_code, response.text)
        return dict(response.json())

    def wait_run(source_id: str, run_id: str | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while True:
            if run_id is None:
                rows = request("GET", f"/api/measure-sources/{source_id}/runs")["runs"]
                if rows:
                    run_id = rows[0]["id"]
            if run_id is not None:
                result = request("GET", f"/api/measure-source-runs/{run_id}")["run"]
                if result["status"] not in ("pending", "running"):
                    return dict(result)
            assert time.monotonic() < deadline, "source run did not complete"
            time.sleep(0.01)

    project = request("POST", "/api/projects", {"name": "Measure proof", "summary": "Fixture"})
    credential = request(
        "POST",
        "/api/credentials",
        {
            "project_id": project["id"],
            "account": "Fixture account",
            "scheme": "local",
            "name": "provider-production",
            "auth_kind": "bearer",
            "secret": {"token": FIRST_TOKEN},
        },
    )
    public_keys = request("GET", "/api/credentials")
    assert FIRST_TOKEN not in json.dumps(public_keys)
    assert public_keys["credentials"][0]["reference"]["name"] == "provider-production"

    def configure(*, second: bool = False) -> tuple[dict[str, Any], str]:
        source = request(
            "POST",
            "/api/measure-sources",
            {
                "name": "Provider query count" if second else "Daily subscriptions",
                "project_id": project["id"],
                "account": "Fixture account",
                "responsible": {"kind": "chief", "id": "chief"},
            },
        )
        measure = request(
            "POST",
            "/api/measures",
            {
                "project_id": project["id"],
                "name": "Query events" if second else "Subscriptions",
                "unit": "events" if second else "subscriptions",
                "origin": "taken",
                "kind": "reading",
                "timezone": "UTC",
                "source_id": source["id"],
            },
        )
        route = "/api/projects/fixture/query" if second else "/api/subscriptions"
        extra = (
            ",method='POST',json_body={'query':{'kind':'HogQLQuery',"
            "'query':'select count() from events'},'refresh':'blocking'}"
            if second
            else ""
        )
        field = "['results'][0][0]" if second else "['value']"
        script = (
            f"response=request({provider.origin + route!r},"
            f"credential_ref='provider-production'{extra})\n"
            "response.raise_for_status()\noutput=context['required_outputs'][0]\n"
            f"record(output['measure_id'],output['required_date'],response.json(){field})"
        )
        source = request("GET", f"/api/measure-sources/{source['id']}")
        source = request(
            "PUT",
            f"/api/measure-sources/{source['id']}/script",
            {
                "expected_revision": source["source_revision"],
                "text": script,
            },
        )
        return source, str(measure["id"])

    def collect(source: dict[str, Any]) -> dict[str, Any]:
        run = request(
            "POST",
            f"/api/measure-sources/{source['id']}/runs",
            {
                "mode": "collection",
                "history": None,
                "expected_script_revision": source["active_script"]["revision"],
            },
        )
        return wait_run(source["id"], run["id"])

    def value(measure_id: str) -> float:
        result = request(
            "POST",
            "/api/measures/read",
            {
                "measure_ids": [measure_id],
                "first_date": "2026-10-27",
                "last_date": "2026-10-27",
            },
        )
        assert result["values"][0]["date"] == "2026-10-27"
        return float(result["values"][0]["value"])

    source, measure_id = configure()
    source = request(
        "PUT",
        f"/api/measure-sources/{source['id']}/schedule",
        {
            "expected_revision": source["source_revision"],
            "enabled": True,
            "local_time": "12:00",
        },
    )
    scheduled = wait_run(source["id"])
    assert scheduled["status"] == "succeeded" and scheduled["trigger"] == "scheduled"
    assert scheduled["schedule_date"] == "2026-10-27" and scheduled["schedule_time"] == "12:00"
    assert value(measure_id) == 42

    provider.token, provider.value = SECOND_TOKEN, 43
    failed = collect(source)
    assert failed["status"] == "failed" and failed["failure"]["http_status"] == 401
    assert value(measure_id) == 42
    status = request("GET", f"/api/measure-sources/{source['id']}/status")
    assert status["health"] == "failed" and status["collection_failure"]["code"] == "http_error"
    request(
        "PUT",
        f"/api/credentials/{credential['id']}/secret",
        {
            "expected_revision": credential["revision"],
            "secret": {"token": SECOND_TOKEN},
        },
    )
    recovered = collect(source)
    assert recovered["status"] == "succeeded" and value(measure_id) == 43
    status = request("GET", f"/api/measure-sources/{source['id']}/status")
    assert status["health"] == "healthy" and status["collection_failure"] is None
    assert status["latest_run"]["id"] == recovered["id"]

    second, second_measure = configure(second=True)
    assert collect(second)["status"] == "succeeded" and value(second_measure) == 25
    assert provider.accepted == 3 and provider.refused == 1 and provider.post_queries == 1
    assert len(request("GET", "/api/measure-sources")["sources"]) == 2
    with closing(connect(str(database))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM measure_source_runs WHERE trigger='scheduled'"
            ).fetchone()[0]
            == 1
        )
        stored = json.dumps(
            [tuple(row) for row in conn.execute("SELECT active_script FROM measure_sources")]
        )
        outcomes = json.dumps(
            [tuple(row) for row in conn.execute("SELECT failure,metrics FROM measure_source_runs")]
        )
        assert FIRST_TOKEN not in stored + outcomes and SECOND_TOKEN not in stored + outcomes
