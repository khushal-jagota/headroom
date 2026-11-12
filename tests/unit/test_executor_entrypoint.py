"""The installed entrypoint's light paths: executor and serve skip the full CLI."""

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


def test_serve_runs_the_cli_command_without_importing_the_other_command_groups() -> None:
    # Every server start pays for what the supervisor imports; the full CLI is over a
    # second of CPU. The light path must also run the very command the full CLI registers.
    program = """
import sys
sys.argv = ["panels", "serve", "--help"]
from planner.executor.entrypoint import main
try:
    main()
except SystemExit as exit:
    assert exit.code == 0
assert "planner.cli.main" not in sys.modules
assert "planner.environments.cli" not in sys.modules
from planner.cli.main import main as panels_main
from planner.server_lifecycle.cli import serve
assert panels_main.commands["serve"] is serve and serve.hidden
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Usage: panels serve [OPTIONS]")
