"""Transport-only CLI contract: routes, actor delegation, stdin, and bounded output."""

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main
from planner.goals import cli as goal_cli
from planner.measure_sources import cli as source_cli
from planner.measures import cli as measure_cli


@pytest.mark.parametrize(
    ("arguments", "method", "path", "body"),
    [
        (
            ["measure-source", "submit-script", "source /one"],
            "PUT",
            "/api/measure-sources/source%20%2Fone/script",
            {
                "expected_revision": 4,
                "text": 'value = 12\nrecord("m", "2026-10-27", value)\n',
            },
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
    assert "list" in result.output and "set-origins" not in result.output
    assert "replace-secret" not in result.output and "create" not in result.output


def test_contract_guidance_is_discoverable_without_transport_or_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent can learn the write shape before it has a server or a payload."""

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("schema discovery must not read stdin or contact HTTP")

    for module in (goal_cli, source_cli, measure_cli):
        monkeypatch.setattr(module, "stdin_object", unexpected)
    monkeypatch.setattr(http, "send", unexpected)
    monkeypatch.setattr(httpx, "request", unexpected)
    runner = CliRunner()
    for group, command in (
        ("goal", "create"),
        ("goal", "edit"),
        ("measure-source", "create"),
        ("measure-source", "submit-script"),
        ("measure-source", "request-run"),
        ("measure", "create"),
    ):
        for arguments in ([group, "--help"], [group, command, "--help"]):
            help_result = runner.invoke(main, arguments)
            assert help_result.exit_code == 0, help_result.output
            assert "schema" in help_result.output
    for arguments, example in (
        (["goal", "schema", "create"], "chief_goal"),
        (["goal", "schema", "edit"], "primary_line_chart"),
        (["goal", "schema", "primary-chart"], "primary_line_chart"),
        (["measure-source", "schema"], "goal_source"),
        (["measure-source", "schema", "create"], "goal_source"),
        (["measure-source", "schema", "script"], "scalar_script"),
        (["measure-source", "schema", "run"], "test_run"),
        (["measure", "schema"], "taken_daily_count"),
    ):
        result = runner.invoke(main, [*arguments, "--json"], input="not JSON")
        assert result.exit_code == 0, result.output
        guidance = json.loads(result.output)
        assert set(guidance) == {"schema", "examples", "notes"}
        assert guidance["schema"] and guidance["notes"]
        assert isinstance(guidance["examples"][example], dict)
    default = runner.invoke(main, ["measure-source", "schema", "--json"])
    explicit = runner.invoke(main, ["measure-source", "schema", "create", "--json"])
    assert json.loads(default.output) == json.loads(explicit.output)
    runtime = runner.invoke(main, ["measure-source", "runtime", "--json"], input="not JSON")
    assert runtime.exit_code == 0, runtime.output
    guide = json.loads(runtime.output)
    assert set(guide) == {"helpers", "context", "examples", "notes"}
    assert set(guide["helpers"]) == {"request", "record", "latest", "record_dataset"}
    assert set(guide["context"]) == {
        "run_id",
        "frozen_at",
        "required_outputs",
        "dataset_outputs",
        "history",
    }
    assert isinstance(guide["examples"]["scalar_request"], str)
    script = runner.invoke(main, ["measure-source", "schema", "script", "--json"])
    assert (
        json.loads(script.output)["examples"]["scalar_script"]["text"]
        == guide["examples"]["scalar_request"]
    )
    for arguments in (
        ["measure-source", "--help"],
        ["measure-source", "submit-script", "--help"],
        ["measure-source", "request-run", "--help"],
    ):
        result = runner.invoke(main, arguments)
        assert result.exit_code == 0, result.output
        assert "runtime" in result.output
