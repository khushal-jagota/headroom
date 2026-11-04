"""Revision/body coherence and the absence of GET-to-change feedback."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fastapi import FastAPI, Request

from planner.core import change_signal
from planner.core.db import connect
from planner.resource_versions import api
from planner.resource_versions.contracts import ResourceSnapshot
from planner.resource_versions.data import install_source_triggers, source_revision
from planner.resource_versions.registry import SOURCE_TRIGGERS
from planner.worker_settings.resource_versions import ChiefResources
from planner.worker_types.resource_versions import WorkerTypeResources


@pytest.fixture
def resource_db(tmp_db: sqlite3.Connection) -> sqlite3.Connection:
    if (
        tmp_db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='resource_source_versions'"
        ).fetchone()
        is None
    ):
        tmp_db.execute(
            "CREATE TABLE resource_source_versions(source_kind TEXT,source_id TEXT,"
            "revision INTEGER NOT NULL,PRIMARY KEY(source_kind,source_id))"
        )
        install_source_triggers(tmp_db, SOURCE_TRIGGERS)
    return tmp_db


def request() -> Request:
    app = FastAPI()
    app.state.resource_version_epoch = "test-epoch"
    return Request({"type": "http", "app": app})


def test_source_counters_are_atomic_narrow_and_reads_emit_no_change(
    resource_db: sqlite3.Connection,
) -> None:
    conn = resource_db
    key = "/api/workers/chief-of-staff/settings"
    provider = ChiefResources()
    signals: list[None] = []
    unsubscribe = change_signal.subscribe(lambda: signals.append(None))
    try:
        before = source_revision(conn, "chief-settings", "chief_of_staff")
        conn.execute("BEGIN")
        conn.execute("UPDATE chief_settings SET recap='a'")
        assert source_revision(conn, "chief-settings", "chief_of_staff") != before
        conn.rollback()
        assert source_revision(conn, "chief-settings", "chief_of_staff") == before
        assert signals == []
        conn.execute("UPDATE chief_settings SET recap='a'")
        updated = source_revision(conn, "chief-settings", "chief_of_staff")
        conn.execute("UPDATE chief_settings SET updated_at=updated_at+1")
        assert source_revision(conn, "chief-settings", "chief_of_staff") == updated
        snapshot = provider.capture(conn, request(), key)
        assert snapshot is not None
        signals.clear()
        for _ in range(3):
            result = asyncio.run(api.resource_versions(json.dumps([key]), request(), conn))
            body = asyncio.run(api.resource_body(key, request(), conn))
            assert result["resources"][0]["revision"] == body["revision"] == snapshot.revision
            assert body["body"]["recap"] == "a"
        assert signals == []
        conn.execute("UPDATE chief_settings SET recap='b'")
        assert source_revision(conn, "chief-settings", "chief_of_staff") != updated
    finally:
        unsubscribe()


def test_body_uses_the_same_snapshot_as_its_version(
    resource_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = resource_db
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    other = connect(path)
    source = ChiefResources()
    key = "/api/workers/chief-of-staff/settings"
    conn.execute("UPDATE chief_settings SET recap='before'")

    class ConcurrentCommit:
        def capture(
            self, connection: sqlite3.Connection, req: Request, key: str
        ) -> ResourceSnapshot | None:
            snapshot = source.capture(connection, req, key)
            assert snapshot is not None
            other.execute("UPDATE chief_settings SET recap='after'")
            return snapshot

    try:
        previous = source.capture(conn, request(), key)
        assert previous is not None
        monkeypatch.setattr(api, "PROVIDERS", (ConcurrentCommit(),))
        body = asyncio.run(api.resource_body(key, request(), conn))
        assert body["revision"] == previous.revision
        assert body["body"]["recap"] == "before"
        current = source.capture(conn, request(), key)
        assert current is not None and current.revision != previous.revision
    finally:
        other.close()


def test_chief_recap_does_not_invalidate_launch_but_skill_and_selected_model_do(
    resource_db: sqlite3.Connection,
) -> None:
    conn = resource_db
    provider = ChiefResources()
    req = request()
    launch_key = "/api/chief/conversation/start-values"
    settings_key = "/api/workers/chief-of-staff/settings"
    launch = provider.capture(conn, req, launch_key)
    settings = provider.capture(conn, req, settings_key)
    assert launch and settings
    conn.execute("UPDATE chief_settings SET recap=recap || ' changed'")
    same_launch = provider.capture(conn, req, launch_key)
    assert same_launch and same_launch.revision == launch.revision
    updated_settings = provider.capture(conn, req, settings_key)
    assert updated_settings and updated_settings.revision != settings.revision
    conn.execute(
        "UPDATE managed_skills SET source_text=source_text || '\n' "
        "WHERE skill_name='panels-chief-of-staff'"
    )
    skill = provider.capture(conn, req, settings_key)
    assert skill and skill.revision != updated_settings.revision
    row = conn.execute(
        "SELECT employee_backend,employee_launch_model FROM chief_settings"
    ).fetchone()
    conn.execute(
        "INSERT INTO backend_model_enablement(backend_key,model_id,enabled) VALUES (?,?,0) "
        "ON CONFLICT(backend_key,model_id) DO UPDATE SET enabled=0",
        tuple(row),
    )
    changed = provider.capture(conn, req, launch_key)
    assert changed and changed.revision != launch.revision


def test_one_worker_type_is_versioned_in_the_shape_of_the_whole_list(
    resource_db: sqlite3.Connection,
) -> None:
    """A page drawing one Ticket's stages read all fourteen types to find one.

    The single-type key has to be a real versioned resource, not an unregistered one: an
    unregistered key answers with revision ``None``, which the browser reads as "changed"
    and refetches on every change signal.
    """
    provider = WorkerTypeResources()
    whole = provider.capture(resource_db, request(), "/api/worker-types")
    one = provider.capture(resource_db, request(), "/api/worker-types/coding/manifest")
    assert whole is not None and one is not None

    whole_body = whole.read()
    assert isinstance(whole_body, dict)
    worker_types = whole_body["worker_types"]
    assert isinstance(worker_types, list)
    assert all(isinstance(entry, dict) for entry in worker_types)
    coding = next(entry for entry in worker_types if entry["worker_type"] == "coding")
    assert one.read() == {"worker_types": [coding]}
    assert one.revision != whole.revision
    assert len(json.dumps(one.read())) < len(json.dumps(whole.read()))

    # An unknown type stays unversioned, so the endpoint answers it with the canonical
    # not-found rather than this provider inventing a revision for nothing.
    assert provider.capture(resource_db, request(), "/api/worker-types/nope/manifest") is None
