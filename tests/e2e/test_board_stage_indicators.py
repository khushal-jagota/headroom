"""Focused board stage-indicator rendering regressions."""

from __future__ import annotations

import sqlite3

WAIT_MS = 10_000


def _add_today(api, server, ticket_id: str) -> None:
    api.human_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})


def _set_ticket_status(server, ticket_id: str, status: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (status, ticket_id),
        )


def _stage(page, card: str, field: str) -> str:
    return f'{card} [data-stage-field="{field}"]'


def test_board_stage_rail_keeps_markers_and_distinguishes_errored(
    server, context_factory, open_page, cli, api
) -> None:
    waiting = cli(server, "ticket", "create", "--title", "Board waiting indicator")["id"]
    pending = cli(server, "ticket", "create", "--title", "Board pending indicator")["id"]
    errored = cli(server, "ticket", "create", "--title", "Board errored indicator")["id"]
    for ticket_id in (waiting, pending, errored):
        _add_today(api, server, ticket_id)

    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success proposed.",
        ticket_id=pending,
        stdin="Pending success body.",
    )
    _set_ticket_status(server, errored, "errored")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        'section[data-screen="workspace"]',
        settled=True,
    )

    waiting_card = f'[data-column="needs_success"] [data-card][data-ticket-id="{waiting}"]'
    pending_card = f'[data-column="needs_success"] [data-card][data-ticket-id="{pending}"]'
    errored_card = f'[data-column="needs_success"] [data-card][data-ticket-id="{errored}"]'
    for card in (waiting_card, pending_card, errored_card):
        page.wait_for_selector(card, timeout=WAIT_MS)
        assert page.eval_on_selector_all(
            f"{card} .board-workspace-stage-mark", "els => els.length"
        ) == 1

    assert page.get_attribute(_stage(page, waiting_card, "success"), "data-stage-state") == (
        "current-waiting"
    )
    assert page.eval_on_selector_all(
        f'{waiting_card} [data-marker="pending-proposal"], '
        f'{waiting_card} [data-marker="agent-running-step"], '
        f'{waiting_card} [data-marker="errored"]',
        "els => els.length",
    ) == 0

    assert page.get_attribute(_stage(page, pending_card, "success"), "data-stage-state") == (
        "current-awaiting-approval"
    )
    assert page.eval_on_selector_all(
        f'{pending_card} [data-marker="pending-proposal"]', "els => els.length"
    ) == 1

    assert page.get_attribute(_stage(page, errored_card, "success"), "data-stage-state") == (
        "errored"
    )
    assert page.eval_on_selector_all(
        f'{errored_card} [data-marker="errored"]', "els => els.length"
    ) == 1
