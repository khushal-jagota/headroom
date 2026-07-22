from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from planner.cli import main as cli_main


def test_sprint_item_block_and_unblock_are_thin_blocks_link_calls(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"ok": True, "from_id": "t_source", "to_id": "si_target", "kind": "blocks"}

    monkeypatch.setattr(cli_main.http, "send", fake_send)
    runner = CliRunner()

    blocked = runner.invoke(
        cli_main.main,
        ["sprint", "item", "block", "si_target", "--by", "t_source"],
    )
    unblocked = runner.invoke(
        cli_main.main,
        ["sprint", "item", "unblock", "si_target", "--by", "t_source"],
    )

    assert blocked.exit_code == 0, blocked.output
    assert unblocked.exit_code == 0, unblocked.output
    assert calls == [
        (
            "POST",
            "/api/links",
            {
                "as_json": False,
                "json_body": {"from_id": "t_source", "to_id": "si_target", "kind": "blocks"},
                "request_actor": "ordinary",
            },
        ),
        (
            "DELETE",
            "/api/links",
            {
                "as_json": False,
                "params": {"from_id": "t_source", "to_id": "si_target", "kind": "blocks"},
                "request_actor": "ordinary",
            },
        ),
    ]


def test_ticket_creation_clis_send_repeated_blocker_ids(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"id": "t_created", "stage": "needs_kickoff"}

    monkeypatch.setattr(cli_main.http, "send", fake_send)
    runner = CliRunner()
    ordinary = runner.invoke(
        cli_main.main,
        [
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            "Ordinary dependent",
            "--blocked-by",
            "t_first",
            "--blocked-by",
            "t_second",
        ],
    )
    with runner.isolated_filesystem():
        with open("kickoff.md", "w", encoding="utf-8") as handle:
            handle.write("External kickoff")
        chief = runner.invoke(
            cli_main.main,
            [
                "chief",
                "create-ticket-from-external-work",
                "--worker-type",
                "coding",
                "--title",
                "Chief dependent",
                "--stage",
                "needs_success",
                "--kickoff-note-file",
                "kickoff.md",
                "--blocked-by",
                "t_shared",
            ],
        )

    assert ordinary.exit_code == 0, ordinary.output
    assert chief.exit_code == 0, chief.output
    assert calls[0][2]["json_body"]["blocked_by_ticket_ids"] == ["t_first", "t_second"]
    assert calls[1][2]["json_body"]["blocked_by_ticket_ids"] == ["t_shared"]
