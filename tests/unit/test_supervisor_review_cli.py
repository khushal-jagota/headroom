from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("args", "path", "body", "output"),
    [
        (
            [
                "sprint",
                "item",
                "supervisor",
                "approve",
                "si_one",
                "t_one",
                "--ceiling",
                "done",
                "--at-cap",
                "agent_review",
            ],
            "/api/items/si_one/supervisor/tickets/t_one/approve",
            {"next_ceiling": "done", "at_cap": "agent_review"},
            "t_one agent review approved",
        ),
        (
            [
                "sprint",
                "item",
                "supervisor",
                "reject",
                "si_one",
                "t_one",
                "--message",
                "Keep the proof focused.",
            ],
            "/api/items/si_one/supervisor/tickets/t_one/reject",
            {"message": "Keep the proof focused."},
            "t_one agent review rejected",
        ),
        (
            [
                "sprint",
                "item",
                "supervisor",
                "transfer-to-user-review",
                "si_one",
                "t_one",
            ],
            "/api/items/si_one/supervisor/tickets/t_one/transfer-to-user-review",
            {},
            "t_one transferred to user review",
        ),
    ],
)
def test_supervisor_review_commands_call_the_scoped_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    path: str,
    body: dict[str, Any],
    output: str,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path, kwargs))
        return {"id": "t_one"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0, result.output
    assert calls == [("POST", path, {"as_json": False, "json_body": body})]
    assert output in result.output


def test_supervisor_reject_requires_one_guidance_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no HTTP request expected")

    monkeypatch.setattr(http, "send", explode)
    result = CliRunner().invoke(
        main,
        ["sprint", "item", "supervisor", "reject", "si_one", "t_one"],
    )

    assert result.exit_code != 0
    assert "reject requires exactly one" in result.output
