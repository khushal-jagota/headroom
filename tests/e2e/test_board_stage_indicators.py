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


def _set_ticket_state(server, ticket_id: str, state: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET state = ? WHERE id = ?",
            (state, ticket_id),
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

    waiting_card = f'[data-card][data-ticket-id="{waiting}"]'
    pending_card = f'[data-card][data-ticket-id="{pending}"]'
    errored_card = f'[data-card][data-ticket-id="{errored}"]'
    for card in (waiting_card, pending_card, errored_card):
        page.wait_for_selector(card, timeout=WAIT_MS)
        assert page.eval_on_selector_all(
            f"{card} .board-workspace-stage-mark", "els => els.length"
        ) == 4

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


def test_workspace_groups_by_project_orders_by_progress_and_filters_status(
    server, context_factory, open_page, cli, api
) -> None:
    later_progress = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Vylo later progress",
        "--project-id",
        "project_vylo",
    )["id"]
    earlier_progress = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Vylo earlier progress",
        "--project-id",
        "project_vylo",
    )["id"]
    done_progress = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Vylo done progress",
        "--project-id",
        "project_vylo",
    )["id"]
    learning = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Learning errored ticket",
        "--project-id",
        "project_learning",
    )["id"]
    no_project = cli(server, "ticket", "create", "--title", "No project ticket")["id"]
    for ticket_id in (later_progress, earlier_progress, done_progress, learning, no_project):
        _add_today(api, server, ticket_id)

    _set_ticket_state(server, later_progress, "needs_plan")
    _set_ticket_state(server, done_progress, "done")
    _set_ticket_status(server, learning, "errored")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        'section[data-screen="workspace"] [data-workspace-filters]',
        settled=True,
    )

    page.wait_for_selector('[data-project-key="project_learning"]', timeout=WAIT_MS)
    page.wait_for_selector('[data-project-key="project_vylo"]', timeout=WAIT_MS)
    page.wait_for_selector('[data-project-key="__no_project__"]', timeout=WAIT_MS)
    headers = page.eval_on_selector_all(
        "[data-project-section] .board-workspace-index-heading-main span:last-child",
        "els => els.map(el => el.textContent.trim())",
    )
    assert headers == ["Learning", "Vylo", "No project"]

    vylo_titles = page.eval_on_selector_all(
        '[data-project-key="project_vylo"] [data-card] .board-workspace-item-label',
        "els => els.map(el => el.textContent.trim())",
    )
    assert vylo_titles == [
        "Vylo earlier progress",
        "Vylo later progress",
        "Vylo done progress",
    ]

    assert page.eval_on_selector_all(
        "[data-card]",
        "els => els.every(el => el.querySelectorAll('.board-workspace-stage-mark').length === 4)",
    )

    page.select_option('[data-filter-group="ticket-status"] select', "errored")
    page.wait_for_selector(f'[data-card][data-ticket-id="{learning}"]', timeout=WAIT_MS)
    visible_titles = page.eval_on_selector_all(
        "[data-card] .board-workspace-item-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert visible_titles == ["Learning errored ticket"]

    page.select_option('[data-filter-group="ticket-status"] select', "all")
    page.wait_for_selector(f'[data-card][data-ticket-id="{earlier_progress}"]', timeout=WAIT_MS)

    page.check("[data-hide-done-toggle]")
    page.wait_for_selector(
        f'[data-card][data-ticket-id="{done_progress}"]',
        state="detached",
        timeout=WAIT_MS,
    )
    visible_titles = page.eval_on_selector_all(
        "[data-card] .board-workspace-item-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert "Vylo done progress" not in visible_titles

    page.uncheck("[data-hide-done-toggle]")
    page.wait_for_selector(f'[data-card][data-ticket-id="{done_progress}"]', timeout=WAIT_MS)
