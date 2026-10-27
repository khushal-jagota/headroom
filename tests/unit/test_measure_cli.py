"""Transport-only CLI contract: routes, actor delegation, stdin, and bounded output."""

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("arguments", "method", "path", "body"),
    [
        (
            ["measure-source", "submit-script", "source /one"],
            "PUT",
            "/api/measure-sources/source%20%2Fone/script",
            {
                "expected_revision": 4,
                "expected_pending_revision": None,
                "text": 'value = 12\nrecord("m", "2026-10-27", value)\n',
            },
        ),
        (
            ["measure-source", "publish-script", "source"],
            "POST",
            "/api/measure-sources/source/publish",
            {"expected_revision": 4, "pending_revision": 2},
        ),
        (
            ["measure-source", "request-run", "source"],
            "POST",
            "/api/measure-sources/source/runs",
            {"mode": "test", "expected_script_revision": 2},
        ),
        (
            ["measure-source", "request-run", "source"],
            "POST",
            "/api/measure-sources/source/runs",
            {
                "mode": "collection",
                "expected_script_revision": 2,
                "history": {"first_date": "2026-10-20", "last_date": "2026-10-27"},
            },
        ),
        (
            ["measure-source", "run-show", "run /one"],
            "GET",
            "/api/measure-source-runs/run%20%2Fone",
            None,
        ),
        (
            ["measure", "edit", "measure"],
            "PATCH",
            "/api/measures/measure",
            {
                "expected_revision": 3,
                "formula": {"op": "reference", "measure_id": "input", "day_offset": -7},
            },
        ),
        (
            ["measure", "read"],
            "POST",
            "/api/measures/read",
            {"measure_ids": ["measure"], "first_date": "2026-10-27", "last_date": "2026-10-27"},
        ),
        (
            ["credential", "set-origins", "credential"],
            "PATCH",
            "/api/credentials/credential",
            {"expected_revision": 1, "allowed_origins": ["https://example.com"]},
        ),
    ],
)
def test_canonical_route_body_and_worker_actor(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    reply = {"id": "run", "status": "pending", "collection_failure": {"code": "http_error"}}

    def request(verb: str, url: str, **kwargs: Any) -> httpx.Response:
        calls.append((verb, url, kwargs))
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(httpx, "request", request)
    result = CliRunner().invoke(
        main,
        [*arguments, "--json"],
        input=json.dumps(body) if body is not None else None,
        env={
            "PLAN_ACTOR": "worker",
            "PLAN_TICKET_ID": "ticket-cli-fixture",
            "PLAN_MANAGER_ID": "",
            "PLAN_SPRINT_ITEM_ID": "",
            "PLAN_SERVER_URL": "http://cli-fixture.invalid",
        },
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == reply
    assert len(calls) == 1  # Enqueue does not poll or wait.
    actual_method, actual_url, kwargs = calls[0]
    assert (actual_method, actual_url, kwargs["json"]) == (
        method,
        "http://cli-fixture.invalid" + path,
        body,
    )
    assert kwargs["headers"] == {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "ticket-cli-fixture"}


@pytest.mark.parametrize(
    ("arguments", "path", "key"),
    [
        (["credential", "list"], "/api/credentials", "credentials"),
        (["measure", "list"], "/api/measures", "measures"),
        (["measure-source", "list"], "/api/measure-sources", "sources"),
        (["measure-source", "runs", "source"], "/api/measure-sources/source/runs", "runs"),
    ],
)
def test_bounded_list_output(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], path: str, key: str
) -> None:
    page = {
        "return_count": 1,
        "match_count": 3,
        "limit": 1,
        "offset": 1,
        "omitted_before": 1,
        "omitted_after": 1,
        "next_offset": 2,
        "complete": False,
    }
    reply = {key: [{"id": "record", "name": "Synthetic value"}], "page": page}

    def send(method: str, requested_path: str, **kwargs: Any) -> dict[str, Any]:
        assert (method, requested_path, kwargs["params"]) == (
            "GET",
            path,
            {"limit": 1, "offset": 1},
        )
        return reply

    monkeypatch.setattr(http, "send", send)
    runner = CliRunner()
    result = runner.invoke(main, [*arguments, "--limit", "1", "--offset", "1"])
    assert result.exit_code == 0, result.output
    assert "Synthetic value" in result.output
    assert "Returned 1 of 3 matches." in result.output
    assert "Omitted 1 before and 1 after." in result.output
    assert "Use --limit 1 --offset 2 for the next page." in result.output
    result = runner.invoke(main, [*arguments, "--limit", "1", "--offset", "1", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == reply


def test_stdin_refusal_and_credential_commands_have_no_secret_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid stdin must not reach HTTP")

    monkeypatch.setattr(http, "send", unexpected)
    runner = CliRunner()
    for malformed in ("not-json", "[]", "[" * 1500 + "0" + "]" * 1500):
        result = runner.invoke(main, ["measure", "create", "--json"], input=malformed)
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["code"] == "validation"
        assert malformed not in result.output
    result = runner.invoke(main, ["credential", "--help"])
    assert result.exit_code == 0
    assert "set-origins" in result.output and "list" in result.output
    assert "replace-secret" not in result.output and "create" not in result.output
