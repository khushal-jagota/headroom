"""Focused Workspace hierarchy and running-indicator regressions."""

from __future__ import annotations

import sqlite3

from planner.tickets.conversation_projection import TicketConversationProjection

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


def test_workspace_ticket_rows_contain_only_title_and_existing_condition_mark(
    server, context_factory, open_page, cli, api
) -> None:
    waiting = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Waiting ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    running = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Running ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    errored = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Errored ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    for ticket_id in (waiting, running, errored):
        _add_today(api, server, ticket_id)
        _set_ticket_stage(server, ticket_id, "needs_success")
    _set_ticket_status(server, running, "agent_running_step")
    _set_ticket_status(server, errored, "errored")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        '[data-project-key="project_vylo"]',
        settled=True,
    )

    waiting_card = f'[data-card][data-ticket-id="{waiting}"]'
    running_card = f'[data-card][data-ticket-id="{running}"]'
    errored_card = f'[data-card][data-ticket-id="{errored}"]'
    for selector, title in (
        (waiting_card, "Waiting ticket"),
        (running_card, "Running ticket"),
        (errored_card, "Errored ticket"),
    ):
        page.wait_for_selector(selector, timeout=WAIT_MS)
        assert page.text_content(f"{selector} .list-row-title").strip() == title
        assert page.locator(f"{selector} .board-workspace-row-byline").count() == 0
        assert page.locator(f"{selector} .board-workspace-row-metadata").count() == 0

    for card in (waiting_card, running_card, errored_card):
        assert page.locator(f"{card} > *").count() == 2
        assert page.locator(f"{card} .board-workspace-stage-mark").count() == 1

    waiting_mark = page.locator(
        f'{waiting_card} .board-workspace-stage-mark[data-stage-state="current-waiting"]'
    )
    assert waiting_mark.count() == 1
    assert waiting_mark.get_attribute("data-workspace-dot-state") == "quiet"
    assert waiting_mark.get_attribute("data-marker") is None
    assert waiting_mark.get_attribute("aria-label") == "Worker quiet"

    running_mark = page.locator(
        f'{running_card} [data-marker="agent-running-step"][data-stage-state="current-running"]'
    )
    assert running_mark.count() == 1
    assert running_mark.get_attribute("data-stage-field") == "success"
    assert running_mark.get_attribute("data-workspace-dot-state") == "active"
    assert running_mark.get_attribute("aria-label") == "Worker active"

    errored_mark = page.locator(
        f'{errored_card} [data-marker="errored"][data-stage-state="errored"]'
    )
    assert errored_mark.count() == 1
    assert errored_mark.get_attribute("data-workspace-dot-state") == "exceptional"
    assert errored_mark.get_attribute("aria-label") == "Worker exception"


def test_workspace_dot_follows_projection_activity_and_reload(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Projection Workspace ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    _add_today(api, server, ticket_id)
    _set_ticket_stage(server, ticket_id, "needs_success")
    _set_ticket_status(server, ticket_id, "empty")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{ticket_id}"]',
        settled=True,
    )
    mark = f'[data-card][data-ticket-id="{ticket_id}"] .board-workspace-stage-mark'
    assert page.get_attribute(mark, "data-stage-state") == "current-waiting"
    assert page.get_attribute(mark, "data-workspace-dot-state") == "quiet"
    assert page.get_attribute(mark, "data-marker") is None
    assert page.get_attribute(mark, "aria-label") == "Worker quiet"

    projection = TicketConversationProjection(server.db_path, now=lambda: 2)
    projection.record_activity(ticket_id, "thinking")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-running'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-marker") == "agent-running-step"
    assert page.get_attribute(mark, "data-workspace-dot-state") == "active"
    assert page.get_attribute(mark, "aria-label") == "Worker active"

    page.reload()
    page.wait_for_selector(mark, timeout=WAIT_MS)
    assert page.get_attribute(mark, "data-stage-state") == "current-running"
    assert page.get_attribute(mark, "data-workspace-dot-state") == "active"
    assert page.get_attribute(mark, "aria-label") == "Worker active"

    projection.record_activity(ticket_id, "idle")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-awaiting-approval'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-marker") is None
    assert page.get_attribute(mark, "data-workspace-dot-state") == "needs_attention"
    assert page.get_attribute(mark, "aria-label") == "Worker needs attention"

    projection.reset(ticket_id)
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-waiting'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-workspace-dot-state") == "quiet"
    assert page.get_attribute(mark, "aria-label") == "Worker quiet"


def test_workspace_groups_populated_project_worker_and_stage_sections_in_contract_order(
    server, context_factory, open_page, cli, api
) -> None:
    coding_success = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo coding success",
        "--project-id",
        "project_vylo",
    )["id"]
    older_plan = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo older plan",
        "--project-id",
        "project_vylo",
    )["id"]
    newer_plan = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo newer plan",
        "--project-id",
        "project_vylo",
    )["id"]
    done = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Vylo done",
        "--project-id",
        "project_vylo",
    )["id"]
    new_worker_stages = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "Vylo new worker stages",
        "--project-id",
        "project_vylo",
    )["id"]
    new_worker_thinking = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "Vylo new worker thinking",
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
        "Learning ticket",
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
    for ticket_id in (
        coding_success,
        older_plan,
        newer_plan,
        done,
        new_worker_stages,
        new_worker_thinking,
        learning,
        no_project,
    ):
        _add_today(api, server, ticket_id)

    _set_ticket_stage(server, coding_success, "needs_success")
    _set_ticket_stage(server, older_plan, "needs_plan")
    _set_ticket_stage(server, newer_plan, "needs_plan")
    _set_ticket_stage(server, done, "done")
    _set_ticket_stage(server, new_worker_stages, "needs_stages")
    _set_ticket_stage(server, new_worker_thinking, "needs_thinking")
    _set_ticket_updated_at(server, older_plan, 10)
    _set_ticket_updated_at(server, newer_plan, 30)

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        '[data-project-key="project_vylo"]',
        settled=True,
    )

    project_labels = page.eval_on_selector_all(
        "[data-project-section] > summary .board-workspace-project-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert project_labels == ["Learning", "Vylo", "No project"]
    project_geometry = page.eval_on_selector_all(
        "[data-project-section]",
        "els => els.map(el => ({marginTop: getComputedStyle(el).marginTop, "
        "fontSize: getComputedStyle("
        "el.querySelector('.board-workspace-project-label')).fontSize}))",
    )
    assert [item["fontSize"] for item in project_geometry] == ["40px", "40px", "40px"]
    assert [item["marginTop"] for item in project_geometry] == ["0px", "44px", "44px"]

    vylo = '[data-project-key="project_vylo"]'
    worker_labels = page.eval_on_selector_all(
        f"{vylo} > .disclosure-body > .board-workspace-index-items "
        "> [data-worker-section] > summary .board-workspace-worker-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert worker_labels == ["Coding", "New Worker"]
    worker_type = page.eval_on_selector(
        f"{vylo} [data-worker-type='coding'] .board-workspace-worker-label",
        "el => ({fontSize: getComputedStyle(el).fontSize, "
        "textTransform: getComputedStyle(el).textTransform})",
    )
    assert worker_type == {"fontSize": "20px", "textTransform": "none"}

    coding = f'{vylo} [data-worker-type="coding"]'
    coding_stage_labels = page.eval_on_selector_all(
        f"{coding} > .disclosure-body > .board-workspace-worker-stages "
        "> [data-stage-section] > summary .board-workspace-stage-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert coding_stage_labels == ["Success", "Plan"]
    stage_type = page.eval_on_selector(
        f"{coding} [data-stage-key='needs_success'] .board-workspace-stage-label",
        "el => ({fontSize: getComputedStyle(el).fontSize, "
        "textTransform: getComputedStyle(el).textTransform})",
    )
    assert stage_type == {"fontSize": "11px", "textTransform": "uppercase"}
    assert page.locator(f'{coding} [data-stage-key="needs_kickoff"]').count() == 0
    assert page.locator(f'{coding} [data-stage-key="done"]').count() == 0

    plan = f'{coding} [data-stage-key="needs_plan"]'
    plan_titles = page.eval_on_selector_all(
        f"{plan} [data-card] .list-row-title",
        "els => els.map(el => el.textContent.trim())",
    )
    assert plan_titles == ["Vylo newer plan", "Vylo older plan"]

    new_worker = f'{vylo} [data-worker-type="new_worker"]'
    new_worker_stage_labels = page.eval_on_selector_all(
        f"{new_worker} > .disclosure-body > .board-workspace-worker-stages "
        "> [data-stage-section] > summary .board-workspace-stage-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert new_worker_stage_labels == ["Stages", "Thinking"]
    assert page.locator(f'{new_worker} [data-stage-key="needs_understanding"]').count() == 0

    assert page.is_checked("[data-hide-done-toggle]")
    assert page.locator(f'[data-card][data-ticket-id="{done}"]').count() == 0

    page.click('.shell-links a[data-screen="day"]')
    page.wait_for_selector('section[data-screen="day"]', timeout=WAIT_MS)
    page.click('.shell-links a[data-screen="workspace"]')
    page.wait_for_selector("[data-hide-done-toggle]", timeout=WAIT_MS)
    assert page.is_checked("[data-hide-done-toggle]")
    assert page.locator(f'[data-card][data-ticket-id="{done}"]').count() == 0

    page.uncheck("[data-hide-done-toggle]")
    page.wait_for_selector(f'[data-card][data-ticket-id="{done}"]', timeout=WAIT_MS)
    assert page.locator(f'{coding} [data-stage-key="done"]').count() == 1

    page.click('.shell-links a[data-screen="day"]')
    page.wait_for_selector('section[data-screen="day"]', timeout=WAIT_MS)
    page.click('.shell-links a[data-screen="workspace"]')
    page.wait_for_selector("[data-hide-done-toggle]", timeout=WAIT_MS)
    assert not page.is_checked("[data-hide-done-toggle]")
    page.wait_for_selector(f'[data-card][data-ticket-id="{done}"]', timeout=WAIT_MS)


def test_workspace_group_disclosures_are_independent_and_chevrons_reveal_on_intent(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Nested disclosure ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    _add_today(api, server, ticket_id)
    _set_ticket_stage(server, ticket_id, "needs_plan")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        '[data-project-key="project_vylo"]',
        settled=True,
    )

    project = '[data-project-key="project_vylo"]'
    worker = f'{project} [data-worker-type="coding"]'
    stage = f'{worker} [data-stage-key="needs_plan"]'
    card = f'[data-card][data-ticket-id="{ticket_id}"]'
    assert page.locator(project).get_attribute("open") == ""
    assert page.locator(worker).get_attribute("open") == ""
    assert page.locator(stage).get_attribute("open") == ""

    project_chevron = page.locator(f"{project} > summary > .disclosure-chev")
    worker_chevron = page.locator(f"{worker} > summary > .disclosure-chev")
    assert project_chevron.evaluate("el => getComputedStyle(el).opacity") == "0"
    assert worker_chevron.evaluate("el => getComputedStyle(el).opacity") == "0"
    page.hover(f"{project} > summary")
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '1'",
        arg=f"{project} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )
    assert worker_chevron.evaluate("el => getComputedStyle(el).opacity") == "0"
    chevron_geometry = page.eval_on_selector(
        f"{project} > summary",
        "summary => ({summary: summary.getBoundingClientRect(), "
        "chevron: summary.querySelector('.disclosure-chev').getBoundingClientRect()})",
    )
    assert abs(chevron_geometry["summary"]["right"] - chevron_geometry["chevron"]["right"]) < 1

    page.click(f"{project} > summary")
    page.click(f"{project} > summary")
    page.mouse.move(0, 0)
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '0'",
        arg=f"{project} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )
    page.keyboard.press("Tab")
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '1'",
        arg=f"{worker} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )

    page.click(f"{stage} > summary")
    assert page.locator(stage).get_attribute("open") is None
    assert page.locator(worker).get_attribute("open") == ""
    assert page.locator(project).get_attribute("open") == ""
    assert page.locator(card).is_hidden()
    page.click(f"{stage} > summary")
    assert page.locator(card).is_visible()

    page.click(f"{worker} > summary")
    assert page.locator(worker).get_attribute("open") is None
    assert page.locator(project).get_attribute("open") == ""
    assert page.locator(stage).get_attribute("open") == ""
    assert page.locator(card).is_hidden()
    page.click(f"{worker} > summary")
    assert page.locator(card).is_visible()

    page.click(f"{project} > summary")
    assert page.locator(project).get_attribute("open") is None
    assert page.locator(worker).get_attribute("open") == ""
    assert page.locator(stage).get_attribute("open") == ""
    assert page.locator(card).is_hidden()
