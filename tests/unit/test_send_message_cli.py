"""CLI contract for the top-level Send Message command."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from planner.cli.main import main


@pytest.mark.parametrize(
    "args",
    [
        ("--message", "Hello"),
        ("--chief", "--ticket", "t_one", "--message", "Hello"),
        ("--chief",),
        ("--chief", "--message", "Hello", "--body-file", "-"),
        ("--chief", "--message", "   "),
    ],
)
def test_cli_rejects_ambiguous_or_empty_input(args: tuple[str, ...]) -> None:
    result = CliRunner().invoke(main, ["send-message", *args, "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "validation"


