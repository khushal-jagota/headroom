"""Declaring a Worker type over HTTP: the one door, and what it refuses.

Landing a Worker used to mean a Python file and a release. These tests are the proof that
it is now a write, and that the write is guarded where the import-time check used to be.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db_path = tmp_path / "worker-types.db"
    with connect(str(db_path)) as boot:
        create_schema(boot)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-07-10T12:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as test_client:
        yield test_client, db_path


def _arrival_record(coding: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(json.dumps(coding))
    record["worker_type"] = "arrival"
    record["label"] = "Arrival"
    record["profile"]["specialist_skill"] = "panels-worker-arrival"
    record["skill"] = {
        "description": "A worker declared without a release.",
        "markdown_body": "# Arrival\n\nDo the work.\n",
    }
    return record


def test_a_worker_type_and_its_skill_are_declared_in_one_write(
    client: tuple[TestClient, Path], tmp_path: Path
) -> None:
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()

    response = test_client.post("/api/worker-types", json=_arrival_record(coding))

    assert response.status_code == 200, response.text
    assert response.json()["worker_type"] == "arrival"
    listed = test_client.get("/api/worker-types").json()["worker_types"]
    assert "arrival" in [manifest["worker_type"] for manifest in listed]
    assert "arrival" in configured_worker_type_registry().registered_worker_types()
    # The skill is on disk too, because that is what an agent reads.
    written = tmp_path / "skills" / "panels-worker-arrival" / "SKILL.md"
    assert "Do the work." in written.read_text(encoding="utf-8")


def test_a_worker_type_naming_a_skill_that_does_not_exist_is_refused(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()
    record = _arrival_record(coding)
    record.pop("skill")

    response = test_client.post("/api/worker-types", json=record)

    assert response.status_code == 400, response.text
    assert "unknown skill" in response.text


def test_a_type_that_declares_no_consequences_is_refused_at_the_door(
    client: tuple[TestClient, Path],
) -> None:
    """A Worker type with no Closeout is not stored and does not become runnable.

    The gap used to surface only when a Ticket ran out of Stages with nothing to land
    its work. Now it surfaces here, when somebody saves the type.
    """
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()
    record = _arrival_record(coding)
    record["stages"] = [stage for stage in record["stages"] if stage["id"] != "needs_consequences"]
    record["fields"] = [field for field in record["fields"] if field["id"] != "consequences"]

    response = test_client.post("/api/worker-types", json=record)

    assert response.status_code == 400, response.text
    assert "every worker type must declare a consequences field" in response.text
    assert test_client.get("/api/worker-types/arrival").status_code == 404
    assert "arrival" not in configured_worker_type_registry().registered_worker_types()


def test_a_malformed_record_is_refused_and_changes_nothing(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client
    before = test_client.get("/api/worker-types/coding").json()
    broken = json.loads(json.dumps(before))
    broken["stages"][1]["ownership_mode"] = "chief"

    response = test_client.post("/api/worker-types", json=broken)

    assert response.status_code == 400, response.text
    assert test_client.get("/api/worker-types/coding").json() == before


def test_a_rewrite_that_would_strand_a_live_ticket_is_refused(
    client: tuple[TestClient, Path],
) -> None:
    test_client, db_path = client
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Mid flight",
            kickoff_note="Kickoff",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        )
        conn.execute("UPDATE tickets SET stage = 'needs_plan' WHERE id = ?", (ticket.id,))
    finally:
        conn.close()

    coding = test_client.get("/api/worker-types/coding").json()
    without_plan = json.loads(json.dumps(coding))
    without_plan["stages"] = [
        stage for stage in without_plan["stages"] if stage["id"] != "needs_plan"
    ]
    without_plan["fields"] = [field for field in without_plan["fields"] if field["id"] != "plan"]

    response = test_client.post("/api/worker-types", json=without_plan)

    assert response.status_code == 400, response.text
    assert ticket.id in response.text
    assert test_client.get("/api/worker-types/coding").json() == coding


def test_a_skill_a_worker_type_owns_is_not_editable_through_the_skills_route(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client

    response = test_client.patch(
        "/api/skills/panels-worker-coding", json={"description": "Edited the wrong way"}
    )

    assert response.status_code == 400, response.text
    assert "Worker type" in response.text
    # The skill every Worker shares is still editable there.
    shared = test_client.patch("/api/skills/panels-worker", json={"description": "Edited"})
    assert shared.status_code == 200, shared.text


def test_launch_defaults_are_the_type_and_take_effect_at_once(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client

    response = test_client.put(
        "/api/workers/coding/launch-defaults",
        json={
            "employee_backend": "claude",
            "employee_launch_model": "claude-opus-5",
            "employee_launch_reasoning_effort": None,
        },
    )

    assert response.status_code == 200, response.text
    record = test_client.get("/api/worker-types/coding").json()
    assert record["profile"]["default_backend"] == "claude"
    assert record["profile"]["default_model"] == "claude-opus-5"
    assert (
        configured_worker_type_registry().require("coding").worker_profile.default_model
        == "claude-opus-5"
    )


def test_a_worker_may_not_declare_a_worker_type(client: tuple[TestClient, Path]) -> None:
    """Declaring a Worker is the owner's act. An agent proposes; it does not self-declare."""
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()

    response = test_client.post(
        "/api/worker-types",
        json=_arrival_record(coding),
        headers={"X-Plan-Actor": "worker"},
    )

    assert response.status_code != 200, response.text
    assert "arrival" not in [
        manifest["worker_type"]
        for manifest in test_client.get("/api/worker-types").json()["worker_types"]
    ]
