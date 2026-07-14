"""Focused board stage-indicator rendering regressions."""

from __future__ import annotations

import sqlite3

WAIT_MS = 10_000


def _add_today(api, server, ticket_id: str) -> None:
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})


def _set_ticket_status(server, ticket_id: str, status: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (status, ticket_id),
        )


def _set_ticket_stage(server, ticket_id: str, stage: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = ? WHERE id = ?",
            (stage, ticket_id),
        )


def _set_ticket_updated_at(server, ticket_id: str, updated_at: int) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            (updated_at, ticket_id),
        )


def _stage(page, card: str, field: str) -> str:
    return f'{card} [data-stage-field="{field}"]'


def test_workspace_row_byline_shows_registered_type_active_stage_and_mark(
    server, context_factory, open_page, cli, api
) -> None:
    active = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "Board new worker metadata",
    )["id"]
    done = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board done metadata",
    )["id"]
    running = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board running metadata",
    )["id"]
    takeover = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board takeover metadata",
    )["id"]
    for ticket_id in (active, done, running, takeover):
        _add_today(api, server, ticket_id)
    _set_ticket_stage(server, active, "needs_stages")
    _set_ticket_stage(server, done, "done")
    _set_ticket_stage(server, running, "needs_success")
    _set_ticket_status(server, running, "agent_running_step")
    _set_ticket_stage(server, takeover, "needs_plan")
    _set_ticket_status(server, takeover, "user_takeover")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        'section[data-screen="workspace"]',
        settled=True,
    )

    card = f'[data-card][data-ticket-id="{active}"]'
    page.wait_for_selector(card, timeout=WAIT_MS)
    assert page.text_content(f"{card} .list-row-title").strip() == "Board new worker metadata"
    page.wait_for_function(
        """selector =>
          document.querySelector(selector)?.textContent.trim() === 'New Worker · Stages'
        """,
        arg=f"{card} .board-workspace-row-byline",
        timeout=WAIT_MS,
    )
    assert page.text_content(f"{card} .board-workspace-row-byline").strip() == (
        "New Worker · Stages"
    )
    assert (
        page.eval_on_selector_all(
            f"{card} .board-workspace-row-byline .board-workspace-stage-mark",
            "els => els.length",
        )
        == 1
    )
    assert (
        page.eval_on_selector_all(
            f"{card} > .board-workspace-stage-rail .board-workspace-stage-mark",
            "els => els.length",
        )
        == 0
    )
    assert page.get_attribute(_stage(page, card, "stages"), "data-stage-state") == (
        "current-waiting"
    )

    geometry = page.eval_on_selector(
        card,
        """card => {
          const rect = selector => card.querySelector(selector).getBoundingClientRect();
          const title = rect('.list-row-title');
          const byline = rect('.board-workspace-row-byline');
          const metadata = rect('.board-workspace-row-metadata');
          const rail = rect('.board-workspace-stage-rail');
          const mark = rect('.board-workspace-stage-mark');
          return { title, byline, metadata, rail, mark };
        }""",
    )
    assert geometry["title"]["bottom"] <= geometry["byline"]["top"]
    assert abs(geometry["metadata"]["left"] - geometry["byline"]["left"]) < 1
    assert abs(geometry["rail"]["right"] - geometry["byline"]["right"]) < 1
    assert abs(geometry["mark"]["right"] - geometry["rail"]["right"]) < 1
    assert (
        abs(
            (geometry["mark"]["top"] + geometry["mark"]["bottom"]) / 2
            - (geometry["byline"]["top"] + geometry["byline"]["bottom"]) / 2
        )
        < 1
    )

    done_card = f'[data-card][data-ticket-id="{done}"]'
    page.wait_for_selector(done_card, timeout=WAIT_MS)
    assert page.text_content(f"{done_card} .board-workspace-row-byline").strip() == (
        "Coding · Done"
    )
    assert (
        page.eval_on_selector_all(
            f"{done_card} .board-workspace-row-byline .board-workspace-stage-mark",
            "els => els.length",
        )
        == 1
    )
    assert page.get_attribute(_stage(page, done_card, "closeout"), "data-stage-state") == (
        "completed"
    )

    running_card = f'[data-card][data-ticket-id="{running}"]'
    page.wait_for_selector(running_card, timeout=WAIT_MS)
    assert page.text_content(f"{running_card} .board-workspace-row-byline").strip() == (
        "Coding · Success"
    )
    assert page.get_attribute(_stage(page, running_card, "success"), "data-stage-state") == (
        "current-running"
    )
    assert (
        page.eval_on_selector_all(
            f'{running_card} [data-marker="agent-running-step"]', "els => els.length"
        )
        == 1
    )

    takeover_card = f'[data-card][data-ticket-id="{takeover}"]'
    page.wait_for_selector(takeover_card, timeout=WAIT_MS)
    assert page.text_content(f"{takeover_card} .board-workspace-row-byline").strip() == (
        "Coding · Plan"
    )
    assert page.get_attribute(_stage(page, takeover_card, "plan"), "data-stage-state") == (
        "current-waiting"
    )
    assert (
        page.eval_on_selector_all(
            f'{takeover_card} [data-marker="user-takeover"]', "els => els.length"
        )
        == 1
    )


def test_board_stage_rail_keeps_markers_and_distinguishes_errored(
    server, context_factory, open_page, cli, api
) -> None:
    waiting = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board waiting indicator",
    )["id"]
    pending = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board pending indicator",
    )["id"]
    errored = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board errored indicator",
    )["id"]
    implementation = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board implementation indicator",
    )["id"]
    closeout = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board closeout indicator",
    )["id"]
    done = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board done indicator",
    )["id"]
    for ticket_id in (waiting, pending, errored, implementation, closeout, done):
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
    _set_ticket_stage(server, implementation, "needs_implementation")
    _set_ticket_stage(server, closeout, "needs_closeout")
    _set_ticket_stage(server, done, "done")

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
    implementation_card = f'[data-card][data-ticket-id="{implementation}"]'
    closeout_card = f'[data-card][data-ticket-id="{closeout}"]'
    done_card = f'[data-card][data-ticket-id="{done}"]'
    for card in (
        waiting_card,
        pending_card,
        errored_card,
        implementation_card,
        closeout_card,
        done_card,
    ):
        page.wait_for_selector(card, timeout=WAIT_MS)
        assert (
            page.eval_on_selector_all(f"{card} .board-workspace-stage-mark", "els => els.length")
            == 1
        )

    assert page.get_attribute(_stage(page, waiting_card, "success"), "data-stage-state") == (
        "current-waiting"
    )
    assert (
        page.eval_on_selector_all(
            f'{waiting_card} [data-marker="pending-proposal"], '
            f'{waiting_card} [data-marker="agent-running-step"], '
            f'{waiting_card} [data-marker="errored"]',
            "els => els.length",
        )
        == 0
    )

    assert page.get_attribute(_stage(page, pending_card, "success"), "data-stage-state") == (
        "current-awaiting-approval"
    )
    assert (
        page.eval_on_selector_all(
            f'{pending_card} [data-marker="pending-proposal"]', "els => els.length"
        )
        == 1
    )

    assert page.get_attribute(_stage(page, errored_card, "success"), "data-stage-state") == (
        "errored"
    )
    assert (
        page.eval_on_selector_all(f'{errored_card} [data-marker="errored"]', "els => els.length")
        == 1
    )

    assert (
        page.get_attribute(_stage(page, implementation_card, "implementation"), "data-stage-state")
        == "current-waiting"
    )
    assert (
        page.get_attribute(_stage(page, closeout_card, "closeout"), "data-stage-state")
        == "current-waiting"
    )
    assert page.get_attribute(_stage(page, done_card, "closeout"), "data-stage-state") == (
        "completed"
    )


def test_workspace_groups_by_project_orders_by_activity_and_filters_status(
    server, context_factory, open_page, cli, api
) -> None:
    older_activity = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo older activity",
        "--project-id",
        "project_vylo",
    )["id"]
    newer_activity = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo newer activity",
        "--project-id",
        "project_vylo",
    )["id"]
    done_activity = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo done activity",
        "--project-id",
        "project_vylo",
    )["id"]
    learning = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Learning errored ticket",
        "--project-id",
        "project_learning",
    )["id"]
    no_project = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "No project ticket",
    )["id"]
    for ticket_id in (older_activity, newer_activity, done_activity, learning, no_project):
        _add_today(api, server, ticket_id)

    _set_ticket_stage(server, older_activity, "needs_plan")
    _set_ticket_stage(server, done_activity, "done")
    _set_ticket_status(server, learning, "errored")
    _set_ticket_updated_at(server, older_activity, 10)
    _set_ticket_updated_at(server, newer_activity, 30)
    _set_ticket_updated_at(server, done_activity, 20)

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
        "[data-project-section] .board-workspace-index-heading-main .section-heading-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert headers == ["Learning", "Vylo", "No project"]

    vylo_titles = page.eval_on_selector_all(
        '[data-project-key="project_vylo"] [data-card] .list-row-title',
        "els => els.map(el => el.textContent.trim())",
    )
    assert vylo_titles == [
        "Vylo newer activity",
        "Vylo done activity",
        "Vylo older activity",
    ]

    assert page.eval_on_selector_all(
        "[data-card]",
        "els => els.every(el => el.querySelectorAll('.board-workspace-stage-mark').length === 1)",
    )

    page.select_option('[data-filter-group="ticket-status"] select', "errored")
    page.wait_for_selector(f'[data-card][data-ticket-id="{learning}"]', timeout=WAIT_MS)
    visible_titles = page.eval_on_selector_all(
        "[data-card] .list-row-title",
        "els => els.map(el => el.textContent.trim())",
    )
    assert visible_titles == ["Learning errored ticket"]

    page.select_option('[data-filter-group="ticket-status"] select', "all")
    page.wait_for_selector(f'[data-card][data-ticket-id="{newer_activity}"]', timeout=WAIT_MS)

    page.check("[data-hide-done-toggle]")
    page.wait_for_selector(
        f'[data-card][data-ticket-id="{done_activity}"]',
        state="detached",
        timeout=WAIT_MS,
    )
    visible_titles = page.eval_on_selector_all(
        "[data-card] .list-row-title",
        "els => els.map(el => el.textContent.trim())",
    )
    assert "Vylo done activity" not in visible_titles

    page.click('.shell-links a[data-screen="day"]')
    page.wait_for_selector('section[data-screen="day"]', timeout=WAIT_MS)
    page.click('.shell-links a[data-screen="workspace"]')
    page.wait_for_selector("[data-hide-done-toggle]", timeout=WAIT_MS)
    assert page.is_checked("[data-hide-done-toggle]")
    page.wait_for_selector(
        f'[data-card][data-ticket-id="{done_activity}"]',
        state="detached",
        timeout=WAIT_MS,
    )

    page.uncheck("[data-hide-done-toggle]")
    page.wait_for_selector(f'[data-card][data-ticket-id="{done_activity}"]', timeout=WAIT_MS)

    page.click('.shell-links a[data-screen="day"]')
    page.wait_for_selector('section[data-screen="day"]', timeout=WAIT_MS)
    page.click('.shell-links a[data-screen="workspace"]')
    page.wait_for_selector("[data-hide-done-toggle]", timeout=WAIT_MS)
    assert not page.is_checked("[data-hide-done-toggle]")
    page.wait_for_selector(f'[data-card][data-ticket-id="{done_activity}"]', timeout=WAIT_MS)
