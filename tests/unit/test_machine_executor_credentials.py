"""Credential theft, legacy reopening, and local secret exposure lack single-Mac coverage.

Use the real credential SQL on a temporary home and a private file. No connected host.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.core.errors import PlannerError
from planner.executor.credential_file import read_credential, write_credential
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.machines.executor_credentials import (
    authenticate_executor,
    issue_original_credential,
    register_machine,
    replace_credential,
)


def test_original_pending_handover_survives_restart_until_first_authentication(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = str(tmp_path / "home.db")
    with closing(connect(database)) as bootstrap:
        create_schema(bootstrap)
    incarnation = str(uuid4())
    with closing(connect(database)) as conn:
        assert authenticate_executor(conn, "original", None, "original") == ORIGINAL_MAC_MACHINE_ID
        grant = issue_original_credential(conn, incarnation)
        assert grant.credential not in caplog.text
        assert incarnation in caplog.text and str(grant.issued_at_ms) in caplog.text
    # A lost reply and a restarted runtime must recover the same pending grant.
    with closing(connect(database)) as bootstrap:
        create_schema(bootstrap)
    with closing(connect(database)) as conn:
        assert issue_original_credential(conn, str(uuid4())) == grant
        row = conn.execute("SELECT * FROM machine_executor_credentials").fetchone()
        assert row["issued_incarnation"] == incarnation
        assert row["pending_credential"] == grant.credential
        assert authenticate_executor(conn, "original", None, "original") == grant.machine_id
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", "wrong", "original")
        assert authenticate_executor(conn, "original", None, "original") == grant.machine_id
        assert (
            authenticate_executor(conn, "original", grant.credential, "original")
            == grant.machine_id
        )
        row = dict(conn.execute("SELECT * FROM machine_executor_credentials").fetchone())
        assert grant.credential not in json.dumps(row)
        assert row["legacy_closed_at_ms"] >= grant.issued_at_ms
        assert row["pending_credential"] is None
        # Schema admission never reopens a completed handover.
        create_schema(conn)
        with pytest.raises(PlannerError):
            issue_original_credential(conn, str(uuid4()))
        assert (
            authenticate_executor(conn, "original", grant.credential, "original")
            == grant.machine_id
        )
        assert caplog.text.count("credential issued at_ms=") == 1
        assert caplog.text.count("credential confirmed at_ms=") == 1
        replacement = replace_credential(conn, grant.machine_id)
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", grant.credential, "original")
        assert (
            authenticate_executor(conn, "original", replacement.credential, "original")
            == grant.machine_id
        )
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", None, "original")


def test_pending_retry_serializes_with_first_authentication_and_rotation(tmp_path: Path) -> None:
    database = str(tmp_path / "home.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
        grant = issue_original_credential(conn, str(uuid4()))
    barrier = Barrier(2)

    def retry() -> object:
        with closing(connect(database)) as conn:
            barrier.wait()
            try:
                return issue_original_credential(conn, str(uuid4()))
            except PlannerError:
                return None  # Acknowledgment won the race.

    def confirm() -> str:
        with closing(connect(database)) as conn:
            barrier.wait()
            return authenticate_executor(conn, "original", grant.credential, "original")

    with ThreadPoolExecutor(max_workers=2) as executor:
        retry_result = executor.submit(retry)
        confirm_result = executor.submit(confirm)
        assert retry_result.result() in (None, grant)
        assert confirm_result.result() == grant.machine_id
    with closing(connect(database)) as conn:
        row = conn.execute("SELECT * FROM machine_executor_credentials").fetchone()
        assert row["legacy_closed_at_ms"] is not None and row["pending_credential"] is None
        with pytest.raises(PlannerError):
            issue_original_credential(conn, str(uuid4()))
    # Operator replacement of an unconfirmed grant closes recovery permanently too.
    second_database = str(tmp_path / "replacement.db")
    with closing(connect(second_database)) as conn:
        create_schema(conn)
        pending = issue_original_credential(conn, str(uuid4()))
        replacement = replace_credential(conn, pending.machine_id)
        with pytest.raises(PlannerError):
            issue_original_credential(conn, str(uuid4()))
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", pending.credential, "original")
        assert authenticate_executor(conn, "original", replacement.credential, "original")
        assert conn.execute(
            "SELECT pending_credential FROM machine_executor_credentials"
        ).fetchone()[0] is None


def test_prior_closed_handover_stays_closed_after_forward_migration(tmp_path: Path) -> None:
    database = str(tmp_path / "prior-home.db")
    with schema_migration_transaction(database) as migration:
        upgrade_schema_connection(migration, "multi_machine_credentials")
        migration.exec_driver_sql(
            "UPDATE machine_executor_credentials SET credential_sha256='previous-verifier',"
            "issued_at_ms=123,issued_incarnation=?,legacy_closed_at_ms=123",
            (str(uuid4()),),
        )
    with closing(connect(database)) as conn:
        create_schema(conn)
        create_schema(conn)
        row = conn.execute("SELECT * FROM machine_executor_credentials").fetchone()
        assert row["legacy_closed_at_ms"] == 123
        assert row["credential_sha256"] == "previous-verifier"
        assert row["pending_credential"] is None
        with pytest.raises(PlannerError):
            issue_original_credential(conn, str(uuid4()))
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", None, "original")


def test_registration_and_rotation_do_not_grant_other_machine(tmp_path: Path) -> None:
    database = str(tmp_path / "home.db")
    with closing(connect(database)) as bootstrap:
        create_schema(bootstrap)
    with closing(connect(database)) as conn:
        one = register_machine(conn, "One")
        two = register_machine(conn, "Two")
        for claim, secret in ((one.machine_id, two.credential), (two.machine_id, one.credential)):
            with pytest.raises(PlannerError):
                authenticate_executor(conn, claim, secret, "original")
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", two.credential, "original")
        with pytest.raises(PlannerError):
            authenticate_executor(conn, one.machine_id, None, "original")
        replace_credential(conn, one.machine_id)
        assert (
            authenticate_executor(conn, two.machine_id, two.credential, "original")
            == two.machine_id
        )


def test_secret_file_is_private_from_creation_and_replacement(tmp_path: Path) -> None:
    path = tmp_path / "executor" / "machine-credential.json"
    previous = os.umask(0)
    try:
        write_credential(path, "a" * 43)
        assert path.stat().st_mode & 0o777 == 0o600
        write_credential(path, "b" * 43)
    finally:
        os.umask(previous)
    assert read_credential(path) == "b" * 43
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]
