"""Machine authority has no TS proof for stolen keys, revocation, or pending handover.

Use private populated SQLite copies and the existing Node evaluator boundary. No
listener, executor, provider, browser, or credential bootstrap process starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.machines import executor_credentials as authority
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID

ROOT = Path(__file__).resolve().parents[3]
INCARNATION = "11111111-1111-4111-8111-111111111111"


def _code(work: Callable[[], object]) -> str:
    try:
        work()
        return "accepted"
    except PlannerError as error:
        return str(error.code)


def _python(path: Path) -> dict[str, Any]:
    with closing(connect(str(path))) as conn:
        first = authority.register_machine(conn, "First", "linux")
        second = authority.register_machine(conn, "Second")
        cross_machine = _code(
            lambda: authority.authenticate_executor(conn, second.machine_id, first.credential, None)
        )
        no_remote_legacy = _code(
            lambda: authority.authenticate_executor(conn, first.machine_id, None, None)
        )
        platform = authority.executor_platform(conn, first.machine_id)
        rotated = authority.replace_credential(conn, first.machine_id)
        old_refused = _code(
            lambda: authority.authenticate_executor(conn, first.machine_id, first.credential, None)
        )
        rotation_valid = (
            authority.authenticate_executor(conn, first.machine_id, rotated.credential, None)
            == first.machine_id
        )
        legacy_before = (
            authority.authenticate_executor(conn, "original", None, "original")
            == ORIGINAL_MAC_MACHINE_ID
        )
        grant = authority.issue_original_credential(conn, INCARNATION)
        retry_same = (
            authority.issue_original_credential(conn, "22222222-2222-4222-8222-222222222222")
            == grant
        )
        wrong_original = _code(
            lambda: authority.authenticate_executor(conn, "original", "wrong", "original")
        )
        legacy_after_wrong = (
            authority.authenticate_executor(conn, "original", None, "original")
            == ORIGINAL_MAC_MACHINE_ID
        )
        credential_accepted = (
            authority.authenticate_executor(conn, "original", grant.credential, "original")
            == ORIGINAL_MAC_MACHINE_ID
        )
        before_close = dict(
            conn.execute(
                "SELECT * FROM machine_executor_credentials WHERE machine_id=?",
                (ORIGINAL_MAC_MACHINE_ID,),
            ).fetchone()
        )
        auth_keeps_pending = (
            before_close["pending_credential"] == grant.credential
            and before_close["legacy_closed_at_ms"] is None
        )
        legacy_after_auth = (
            authority.authenticate_executor(conn, "original", None, "original")
            == ORIGINAL_MAC_MACHINE_ID
        )
        retry_after_auth_same = authority.issue_original_credential(conn, INCARNATION) == grant
        wrong_close = _code(
            lambda: authority.close_original_handover(
                conn, ORIGINAL_MAC_MACHINE_ID, "wrong", INCARNATION
            )
        )
        legacy_after_wrong_close = (
            authority.authenticate_executor(conn, "original", None, "original")
            == ORIGINAL_MAC_MACHINE_ID
        )
        authority.close_original_handover(
            conn, ORIGINAL_MAC_MACHINE_ID, grant.credential, INCARNATION
        )
        row = dict(
            conn.execute(
                "SELECT * FROM machine_executor_credentials WHERE machine_id=?",
                (ORIGINAL_MAC_MACHINE_ID,),
            ).fetchone()
        )
        with patch(
            "planner.machines.executor_credentials.time.time_ns",
            return_value=(row["legacy_closed_at_ms"] + 1000) * 1_000_000,
        ):
            authority.close_original_handover(
                conn, ORIGINAL_MAC_MACHINE_ID, grant.credential, INCARNATION
            )
        closure_idempotent = row == dict(
            conn.execute(
                "SELECT * FROM machine_executor_credentials WHERE machine_id=?",
                (ORIGINAL_MAC_MACHINE_ID,),
            ).fetchone()
        )
        retired_pending = (
            row["pending_credential"] is None and row["legacy_closed_at_ms"] is not None
        )
        verifier_matches = (
            row["credential_sha256"] == hashlib.sha256(grant.credential.encode()).hexdigest()
        )
        no_stored_secret = grant.credential not in json.dumps(row)
        legacy_after = _code(
            lambda: authority.authenticate_executor(conn, "original", None, "original")
        )
        closed_reissue = _code(lambda: authority.issue_original_credential(conn, INCARNATION))
        home_refused = _code(
            lambda: authority.authenticate_executor(conn, HOME_MACHINE_ID, grant.credential, None)
        )
    with closing(connect(str(path))) as conn:
        durable_closure = _code(
            lambda: authority.authenticate_executor(conn, "original", None, "original")
        )
    return {
        name: value
        for name, value in locals().items()
        if name
        in {
            "cross_machine",
            "no_remote_legacy",
            "platform",
            "old_refused",
            "rotation_valid",
            "legacy_before",
            "retry_same",
            "wrong_original",
            "legacy_after_wrong",
            "credential_accepted",
            "auth_keeps_pending",
            "legacy_after_auth",
            "retry_after_auth_same",
            "wrong_close",
            "legacy_after_wrong_close",
            "closure_idempotent",
            "retired_pending",
            "verifier_matches",
            "no_stored_secret",
            "legacy_after",
            "closed_reissue",
            "home_refused",
            "durable_closure",
        }
    }


def test_machine_credentials_match_python(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    began = time.perf_counter()
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "TMPDIR": str(tmp_path),
        **create_synthetic_homes(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    source, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
    with synthetic_home_environment(environment), closing(connect(str(source))) as conn:
        create_schema(conn)
    with sqlite3.connect(source) as reference, sqlite3.connect(candidate) as target:
        reference.backup(target)
    expected = _python(source)
    completed = subprocess.run(
        ["node", str(Path(__file__).with_name("machine_credentials.ts")), str(candidate)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, "Private machine credential evaluator failed"
    actual = json.loads(completed.stdout)
    assert actual == expected
    assert all(value is True for value in actual.values() if type(value) is bool)
    assert expected["cross_machine"] == expected["old_refused"] == "agent_forbidden"
    assert expected["wrong_close"] == expected["wrong_original"] == "agent_forbidden"
    assert expected["legacy_after"] == expected["durable_closure"] == "agent_forbidden"
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "machine-credentials.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - began,
                    "outcomes": actual,
                    "boundary": "Real credential SQL on populated copies; no connected host",
                },
                indent=2,
            )
            + "\n"
        )
