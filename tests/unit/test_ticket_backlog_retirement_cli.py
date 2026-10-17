"""Lifecycle commands carry bounded filters and fresh-record preconditions."""

import json
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize("action", ["backlog", "resume"])
def test_lifecycle_command_passes_fresh_record_guard(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requests.append((method, path, kwargs))
        return {"id": "t_example"}

    monkeypatch.setattr(http, "send", send)
    arguments = ["ticket", action, "t_example", "--expected-record-sha256", "a" * 64, "--json"]
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert requests[0][0:2] == ("POST", f"/api/tickets/t_example/{action}")
    assert requests[0][2]["json_body"]["expected_record_sha256"] == "a" * 64


def test_retire_command_is_unavailable_without_sending_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pytest.fail("Removed command must not contact the server")

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, ["ticket", "retire", "t_example", "--reason", "Obsolete"])
    assert result.exit_code == 2
    assert '"retire" is not a command under panels ticket' in result.output
    help_result = CliRunner().invoke(main, ["ticket", "--help"])
    assert help_result.exit_code == 0
    assert "retire" not in help_result.output


def test_cancelled_history_list_preserves_filters_and_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []
    response = {
        "tickets": [
            {
                "id": "t_history",
                "stage": "cancelled",
                "ticket_status": "cancelled",
                "priority": "P2",
                "title": "Historical work",
                "project": None,
                "recap_preview": "",
            }
        ],
        "page": {
            "limit": 3,
            "match_count": 5,
            "return_count": 1,
            "omitted_before": 1,
            "omitted_after": 3,
            "complete": False,
            "next_offset": 2,
        },
    }

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requests.append((method, path, kwargs))
        return response

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(
        main,
        [
            "ticket",
            "list",
            "--include-terminal",
            "--stage",
            "cancelled",
            "--limit",
            "3",
            "--offset",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert requests == [
        (
            "GET",
            "/api/tickets",
            {
                "as_json": True,
                "params": {
                    "detail": "summary",
                    "include_terminal": True,
                    "stage": ["cancelled"],
                    "limit": 3,
                    "offset": 1,
                },
            },
        )
    ]
    assert json.loads(result.output) == response


def test_cancelled_history_show_preserves_exact_retirement_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []
    provenance = (
        '{ "reason": "Historical disposition 🌱",\n'
        ' "original_record": {"stage": "needs_plan"} }'
    )

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requests.append((method, path, kwargs))
        if path == "/api/tickets":
            return {
                "id": "t_history",
                "stage": "cancelled",
                "worker_type": "coding",
                "field_values": {"retirement": provenance},
            }
        assert path == "/api/worker-types"
        return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, ["ticket", "show", "t_history", "retirement", "--json"])
    assert result.exit_code == 0, result.output
    assert requests == [
        (
            "GET",
            "/api/tickets",
            {"as_json": True, "params": {"detail": "full", "id": "t_history"}},
        ),
        ("GET", "/api/worker-types", {"as_json": True}),
    ]
    projected = json.loads(result.output)
    assert projected["header"]["stage"] == "cancelled"
    assert projected["parts"] == {"retirement": {"value": provenance, "proposal": None}}
