"""CLI identity reaches its actual outgoing HTTP request."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from planner.cli.main import main


@pytest.mark.parametrize("actor", ["worker", None])
def test_cli_headers_say_what_the_environment_says_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    actor: str | None,
) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PLAN_SERVER_URL", "http://panels.test")
    monkeypatch.setenv("PLAN_TICKET_ID", "t_worker")
    if actor is not None:
        monkeypatch.setenv("PLAN_ACTOR", actor)
    observed: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "projects": [],
                "page": {
                    "match_count": 0,
                    "return_count": 0,
                    "omitted_before": 0,
                    "omitted_after": 0,
                    "complete": True,
                    "next_offset": None,
                    "limit": 30,
                    "offset": 0,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:

        def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            return client.request(method, url, **kwargs)

        monkeypatch.setattr(httpx, "request", request)
        result = CliRunner().invoke(main, ["project", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert len(observed) == 1
    assert observed[0].url.path == "/api/projects"
    headers = {
        key.lower(): value
        for key, value in observed[0].headers.items()
        if key.lower().startswith("x-plan-")
    }
    assert headers == ({"x-plan-actor": "worker", "x-plan-ticket-id": "t_worker"} if actor else {})
