"""The actual server harness keeps parent and child native homes private."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from tests.support.server_fixtures import _start_server, _stop_server


@pytest.mark.skipif(
    sys.platform == "darwin", reason="Owned child environment proof needs Linux /proc"
)
def test_server_parent_setup_and_scrubbed_child_share_private_homes(tmp_path: Path) -> None:
    keys = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PLAN_HERMES_HOME", "HERMES_HOME"}
    original = {key: os.environ.get(key) for key in keys}
    observed: dict[str, str] = {}

    def capture_parent(database: Path) -> None:
        assert database.parent == tmp_path / "server"
        observed.update({key: os.environ[key] for key in keys})

    handle = _start_server(
        tmp_path / "server", seed_db=capture_parent,
        trusted_ingress_env={key: str(tmp_path / "poison" / key) for key in keys},
    )
    try:
        # Read this owned child's launch environment, without an observability endpoint.
        child = dict(
            item.split("=", 1)
            for item in Path(f"/proc/{handle.proc.pid}/environ").read_bytes().decode().split("\0")
            if "=" in item
        )
        assert {key: child[key] for key in keys} == observed
        assert observed["PLAN_HERMES_HOME"] == observed["HERMES_HOME"]
        for value in observed.values():
            assert Path(value).is_relative_to(tmp_path / "server")
        assert {key: os.environ.get(key) for key in keys} == original
    finally:
        _stop_server(handle)
