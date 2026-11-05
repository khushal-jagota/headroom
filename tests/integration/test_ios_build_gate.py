"""Exercise real app wiring without a runtime, provider, or browser process."""

from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_native_floor_gates_reads_writes_and_stream_before_endpoint(tmp_path: Path) -> None:
    database = str(tmp_path / "ios.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
    config = load_config(env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": database})
    app = create_app(config, build_clock(config), lambda: connect(database))
    client = TestClient(app)
    supported = {"X-Panels-iOS-Build": "1"}
    stale = {"X-Panels-iOS-Build": "0"}
    assert client.get("/api/ideas", headers=supported).status_code == 200
    # A supported operation proves the mutation route works in this same app.
    assert (
        client.post(
            "/api/ideas",
            headers=supported,
            json={"title": "Accepted native action", "body": "From supported build"},
        ).status_code
        == 200
    )
    before = client.get("/api/ideas", headers=supported).json()
    for method, path, kwargs in (
        ("GET", "/api/ideas", {}),
        ("POST", "/api/ideas", {"json": {"title": "Must not execute", "body": "Old build"}}),
        ("GET", "/api/changes", {}),
    ):
        response = client.request(method, path, headers=stale, **kwargs)
        assert response.status_code == 426
        assert response.json()["error"]["code"] == "ios_update_required"
    assert client.get("/api/ideas", headers=supported).json() == before
    assert client.get("/api/ideas").json() == before
