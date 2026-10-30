"""Descendants cannot mutate account sentinels through Python or native tools."""

from __future__ import annotations

import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# These destructive probes run only after tests/conftest.py admits the kernel
# boundary. Ordinary CI retains its pre-existing behavior without these probes.
pytestmark = pytest.mark.skipif(
    os.environ.get("PANELS_REQUIRE_TEST_CONTAINMENT") != "1",
    reason="containment probes require explicit OS boundary admission",
)


def test_python_and_native_children_cannot_write_account_skill_paths() -> None:
    """The OS boundary covers descendants that never load the Python audit hook."""
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    target = account / ".codex" / "skills" / "sentinel.txt"
    before = target.read_bytes() if target.exists() else None
    programs = [
        [
            sys.executable,
            "-I",
            "-c",
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("changed")',
            str(target),
        ],
        ["/bin/sh", "-c", 'printf changed > "$1"', "containment-proof", str(target)],
    ]
    for command in programs:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, close_fds=True, check=False
        )
        assert result.returncode != 0, (command, result.stdout, result.stderr)
    assert (target.read_bytes() if target.exists() else None) == before


def test_original_inventory_remains_separate_from_synthetic_homes() -> None:
    """The serialized original paths cannot be replaced by post-mask discovery."""
    contract = json.loads(Path("/run/panels-test-containment/contract.json").read_text())
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert str(account / ".codex/skills") in contract["protected_paths"]
    assert Path(os.environ["CODEX_HOME"]).is_relative_to(contract["scratch_root"])
    assert Path(os.environ["CODEX_HOME"]) != account / ".codex"


def test_private_socket_survives_until_final_evidence_export() -> None:
    """A private runtime socket must not disable the final guard evidence export."""
    directory = Path(tempfile.mkdtemp(prefix="socket-evidence-", dir="/tmp"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        endpoint.bind(str(directory / "runtime.sock"))
    assert (directory / "runtime.sock").is_socket()
