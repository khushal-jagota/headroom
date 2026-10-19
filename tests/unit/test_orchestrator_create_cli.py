"""Creation reports startup separately from the durable orchestrator save."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from click.testing import CliRunner, Result

from planner.cli.main import main


def _create(
    monkeypatch: pytest.MonkeyPatch,
    startup: dict[str, Any] | None,
    *,
    as_json: bool = False,
    command: tuple[str, ...] = ("orchestrator",),
    goal: str | None = None,
) -> tuple[Result, dict[str, Any]]:
    payload = {"id": "si_saved", "title": "A useful goal", "startup": startup}
    if goal is None and startup is not None:
        goal = "Do the work"

    def respond(method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert method == "POST"
        assert url.endswith("/api/items")
        assert kwargs["json"]["goal"] == (goal or "")
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", respond)
    args = [*command, "create", "--title", "A useful goal", "--project-id", "p_test"]
    if goal is not None:
        args.extend(["--goal-file", "-"])
    if as_json:
        args.append("--json")
    return CliRunner().invoke(main, args, input=goal), payload
