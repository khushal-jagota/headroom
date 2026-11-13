"""Credential theft, legacy reopening, and local secret exposure lack single-Mac coverage.

Use the real credential SQL on a temporary home and a private file. No connected host.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.executor.credential_file import read_credential, write_credential
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.machines.executor_credentials import (
    authenticate_executor,
    issue_original_credential,
    register_machine,
    replace_credential,
)


def test_original_issuance_closes_legacy_permanently(
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
    # A fresh connection and schema admission cannot reopen the old authority.
    with closing(connect(database)) as bootstrap:
        create_schema(bootstrap)
    with closing(connect(database)) as conn:
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", None, "original")
        with pytest.raises(PlannerError):
            issue_original_credential(conn, str(uuid4()))
        assert (
            authenticate_executor(conn, "original", grant.credential, "original")
            == grant.machine_id
        )
        row = dict(conn.execute("SELECT * FROM machine_executor_credentials").fetchone())
        assert grant.credential not in json.dumps(row)
        assert row["legacy_closed_at_ms"] == grant.issued_at_ms
        replacement = replace_credential(conn, grant.machine_id)
        with pytest.raises(PlannerError):
            authenticate_executor(conn, "original", grant.credential, "original")
        assert (
            authenticate_executor(conn, "original", replacement.credential, "original")
            == grant.machine_id
        )
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
