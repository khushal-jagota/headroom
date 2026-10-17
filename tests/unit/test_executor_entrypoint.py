"""The installed executor command needs no local Panels state or HTTP server."""

from __future__ import annotations

import subprocess
import sys


def test_executor_help_does_not_open_database_or_network() -> None:
    program = """
import sqlite3, socket, sys

def forbidden(*args, **kwargs):
    raise AssertionError("executor help accessed runtime state")
sqlite3.connect = forbidden
socket.socket.connect = forbidden
socket.create_connection = forbidden
sys.argv = ["panels", "executor", "--help"]
from planner.executor.entrypoint import main
try:
    main()
except SystemExit as exit:
    assert exit.code == 0
assert "planner.core.server" not in sys.modules
assert "planner.environments.cli" not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "quiesce" in result.stdout and "run" in result.stdout
