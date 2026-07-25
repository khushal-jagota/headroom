"""Focused Workspace status-group and row-signal regressions."""

from __future__ import annotations

import sqlite3

from planner.tickets.conversation_projection import TicketConversationProjection

WAIT_MS = 10_000

# Workspace groups by the Ticket's own status, except a done Ticket, which groups
# as done. This is the full display order, top to bottom.
BUCKET_ORDER = [
    "errored",
    "needs_user",
    "empty",
    "user",
    "paired",
    "agent",
    "awaiting_approval",
    "blocked",
    "done",
]

# The label is the humanized status name.
BUCKET_LABELS = {
    "errored": "Errored",
    "needs_user": "Needs user",
    "empty": "Empty",
    "user": "User",
    "paired": "Paired",
    "agent": "Agent",
    "awaiting_approval": "Awaiting approval",
    "blocked": "Blocked",
    "done": "Done",
}


def _bucket(key: str) -> str:
    return f'[data-bucket-section][data-bucket-key="{key}"]'


def _add_today(api, server, ticket_id: str) -> None:
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})


def _set_ticket_status(
    server, ticket_id: str, status: str, *, backend_error: str | None = None
) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = ?, backend_error = ? WHERE id = ?",
            (status, backend_error, ticket_id),
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


def _create_ticket(cli, server, title: str, *, worker_type: str = "coding") -> str:
    return cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        worker_type,
        "--title",
        title,
        "--project-id",
        "project_vylo",
    )["id"]


def test_workspace_ticket_rows_contain_only_title_and_signal_mark(
    server, context_factory, open_page, cli, api
) -> None:
    waiting = _create_ticket(cli, server, "Waiting ticket")
    running = _create_ticket(cli, server, "Running ticket")
    errored = _create_ticket(cli, server, "Errored ticket")
    completed = _create_ticket(cli, server, "Completed ticket")
    for ticket_id in (waiting, running, errored, completed):
        _add_today(api, server, ticket_id)
        _set_ticket_stage(server, ticket_id, "needs_success")
    _set_ticket_status(server, running, "agent")
    _set_ticket_status(
        server, errored, "errored", backend_error="Provider process exited unexpectedly"
    )
    _set_ticket_stage(server, completed, "done")
    _set_ticket_status(server, completed, "empty")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        _bucket("empty"),
        settled=True,
    )

    waiting_card = f'[data-card][data-ticket-id="{waiting}"]'
    running_card = f'[data-card][data-ticket-id="{running}"]'
    errored_card = f'[data-card][data-ticket-id="{errored}"]'
    completed_card = f'[data-card][data-ticket-id="{completed}"]'

    # Only the populated status groups render, in display order; the old
    # Project -> Worker type -> Stage tree is gone.
    rendered = page.eval_on_selector_all(
        "[data-bucket-section]",
        "els => els.map(el => el.getAttribute('data-bucket-key'))",
    )
    assert rendered == ["errored", "empty", "agent", "done"]
    assert page.locator("[data-project-section]").count() == 0
    assert page.locator("[data-worker-section]").count() == 0
    assert page.locator("[data-stage-section]").count() == 0

    # Each card sits in exactly one group.
    assert page.locator(f'{_bucket("empty")} {waiting_card}').count() == 1
    assert page.locator(f'{_bucket("agent")} {running_card}').count() == 1
    assert page.locator(f'{_bucket("errored")} {errored_card}').count() == 1
    assert page.locator(f'{_bucket("done")} {completed_card}').count() == 1

    # Done is collapsed by default; opening it reveals the completed row.
    assert page.get_attribute(_bucket("done"), "open") is None
    assert not page.is_visible(completed_card)
    page.click(f'{_bucket("done")} > summary')
    for selector, title in (
        (waiting_card, "Waiting ticket"),
        (running_card, "Running ticket"),
        (errored_card, "Errored ticket"),
        (completed_card, "Completed ticket"),
    ):
        page.wait_for_selector(selector, timeout=WAIT_MS)
        assert page.text_content(f"{selector} .list-row-title").strip() == title
        assert page.locator(f"{selector} .board-workspace-row-byline").count() == 0
        assert page.locator(f"{selector} .board-workspace-row-metadata").count() == 0

    for card in (waiting_card, running_card, errored_card, completed_card):
        assert page.locator(f"{card} > *").count() == 2
        assert page.locator(f"{card} .board-workspace-stage-mark").count() == 1

    # A quiet ticket: not working, no reply.
    waiting_mark = page.locator(
        f'{waiting_card} .board-workspace-stage-mark[data-stage-state="upcoming"]'
    )
    assert waiting_mark.count() == 1
    assert waiting_mark.get_attribute("data-agent-working") == "false"
    assert waiting_mark.get_attribute("data-reply-state") == "none"
    assert waiting_mark.get_attribute("data-workspace-dot-state") is None
    assert waiting_mark.get_attribute("data-marker") is None
    assert waiting_mark.get_attribute("aria-label") == "Nothing waiting"

    # A running step: the working signal wins the mark.
    running_mark = page.locator(
        f'{running_card} .board-workspace-stage-mark[data-stage-state="current-running"]'
    )
    assert running_mark.count() == 1
    assert running_mark.get_attribute("data-agent-working") == "true"
    assert running_mark.get_attribute("data-reply-state") == "none"
    assert running_mark.get_attribute("aria-label") == "Agent working"

    # The errored condition lives on the bucket, not the row mark: the mark
    # carries only the two signals, and the Errored label carries the error red.
    errored_mark = page.locator(
        f'{errored_card} .board-workspace-stage-mark[data-stage-state="upcoming"]'
    )
    assert errored_mark.count() == 1
    assert errored_mark.get_attribute("data-agent-working") == "false"
    assert errored_mark.get_attribute("data-reply-state") == "none"
    assert errored_mark.get_attribute("aria-label") == "Nothing waiting"
    errored_label_color = page.eval_on_selector(
        f'{_bucket("errored")} .board-workspace-bucket-label',
        "el => getComputedStyle(el).color",
    )
    assert errored_label_color == "rgb(216, 93, 93)"

    completed_mark = page.locator(
        f'{completed_card} .board-workspace-stage-mark[data-stage-state="upcoming"]'
    )
    assert completed_mark.count() == 1
    assert completed_mark.get_attribute("aria-label") == "Nothing waiting"


def test_workspace_project_filter_uses_effective_project_and_keeps_inspector_open(
    server, context_factory, open_page, cli, api
) -> None:
    standalone = _create_ticket(cli, server, "Standalone Vylo ticket")
    no_project = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "No project ticket",
    )["id"]
    parent_item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Parented work",
        "--project",
        "Vylo",
    )["id"]
    parented = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Parented Vylo ticket",
        "--sprint-item",
        parent_item,
    )["id"]
    other_day = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Other day ticket",
    )["id"]
    cli(server, "day", "remove-ticket", other_day, "--date", "today")
    cli(server, "day", "add-ticket", other_day, "--date", "2026-07-23")
    for ticket_id in (standalone, no_project, parented):
        _add_today(api, server, ticket_id)

    no_project_card = f'[data-card][data-ticket-id="{no_project}"]'
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{no_project}",
        no_project_card,
        settled=True,
    )
    project_filter = page.locator("[data-project-filter]")
    assert project_filter.locator("option").all_text_contents() == [
        "All projects",
        "Vylo",
        "No project",
    ]
    assert page.locator(f'[data-card][data-ticket-id="{other_day}"]').count() == 0

    project_filter.select_option("project_vylo")
    assert page.locator(f'[data-card][data-ticket-id="{standalone}"]').count() == 1
    assert page.locator(f'[data-card][data-ticket-id="{parented}"]').count() == 1
    assert page.locator(no_project_card).count() == 0
    assert page.locator(
        f'section[data-screen="ticket"][data-ticket-id="{no_project}"]'
    ).count() == 1
    assert page.url.endswith(f"#/workspace/{no_project}")

    project_filter.select_option("__no_project__")
    assert page.locator(no_project_card).count() == 1
    assert page.locator(f'[data-card][data-ticket-id="{standalone}"]').count() == 0
    assert page.locator(f'[data-card][data-ticket-id="{parented}"]').count() == 0

    project_filter.select_option("__all_projects__")
    assert page.locator(no_project_card).count() == 1
    assert page.locator(f'[data-card][data-ticket-id="{standalone}"]').count() == 1
    assert page.locator(f'[data-card][data-ticket-id="{parented}"]').count() == 1

    # Live invalidation removes the last cards for the selected concrete
    # project. The stale selection resets to All projects and the remaining
    # roster recovers without a reload.
    project_filter.select_option("project_vylo")
    cli(server, "day", "remove-ticket", standalone, "--date", "today")
    cli(server, "day", "remove-ticket", parented, "--date", "today")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.value === '__all_projects__'",
        arg="[data-project-filter]",
        timeout=WAIT_MS,
    )
    assert project_filter.input_value() == "__all_projects__"
    assert page.locator(no_project_card).count() == 1
    assert page.locator(f'[data-card][data-ticket-id="{standalone}"]').count() == 0
    assert page.locator(f'[data-card][data-ticket-id="{parented}"]').count() == 0


def test_backend_error_reason_and_workspace_treatment_clear_with_canonical_fact(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = _create_ticket(cli, server, "Backend failure ticket")
    _add_today(api, server, ticket_id)
    _set_ticket_stage(server, ticket_id, "needs_success")
    _set_ticket_status(
        server,
        ticket_id,
        "errored",
        backend_error="Provider process exited with status 17",
    )

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'[data-card][data-ticket-id="{ticket_id}"]',
        settled=True,
    )
    card = f'[data-card][data-ticket-id="{ticket_id}"]'
    mark = f"{card} .board-workspace-stage-mark"
    reason = "[data-backend-error]"
    page.wait_for_selector(reason, timeout=WAIT_MS)
    assert page.text_content(reason).strip() == "Provider process exited with status 17"
    assert page.locator(f'{_bucket("errored")} {card}').count() == 1
    assert page.get_attribute(mark, "data-stage-state") == "upcoming"
    assert page.get_attribute(mark, "data-agent-working") == "false"

    _set_ticket_status(server, ticket_id, "empty")
    page.reload()
    page.wait_for_selector(mark, timeout=WAIT_MS)
    assert page.locator(reason).count() == 0
    assert page.locator(f'{_bucket("empty")} {card}').count() == 1
    assert page.locator(_bucket("errored")).count() == 0
    assert page.get_attribute(mark, "data-stage-state") == "upcoming"
    assert page.get_attribute(mark, "data-reply-state") == "none"
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT ticket_status, backend_error FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone() == ("empty", None)


def test_workspace_signals_follow_projection_activity_reply_and_acknowledgement(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = _create_ticket(cli, server, "Projection Workspace ticket")
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
    assert page.get_attribute(mark, "data-stage-state") == "upcoming"
    assert page.get_attribute(mark, "data-agent-working") == "false"
    assert page.get_attribute(mark, "data-reply-state") == "none"
    assert page.get_attribute(mark, "aria-label") == "Nothing waiting"

    # A working activity state flips the working signal live.
    projection = TicketConversationProjection(server.db_path, now=lambda: 2)
    projection.record_activity(ticket_id, "thinking")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-running'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-agent-working") == "true"
    assert page.get_attribute(mark, "aria-label") == "Agent working"

    page.reload()
    page.wait_for_selector(mark, timeout=WAIT_MS)
    assert page.get_attribute(mark, "data-stage-state") == "current-running"
    assert page.get_attribute(mark, "data-agent-working") == "true"

    # The completed turn becomes an unseen reply.
    projection.record_activity(ticket_id, "idle")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-awaiting-approval'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-agent-working") == "false"
    assert page.get_attribute(mark, "data-reply-state") == "unseen"
    assert page.get_attribute(mark, "aria-label") == "Unseen agent reply"

    # Opening the ticket acknowledges the reply: seen, not gone.
    page.click(f'[data-card][data-ticket-id="{ticket_id}"]')
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'reply-seen'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-reply-state") == "seen"
    assert page.get_attribute(mark, "aria-label") == "Agent reply seen"

    # A pending permission ask reads as an unseen reply again.
    page.click("[data-chief-of-staff-button]")
    projection.record_activity(ticket_id, "thinking")
    projection.record_activity(ticket_id, "idle")
    projection.record_permission(ticket_id, True)
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-reply-state') "
        "=== 'unseen'",
        arg=mark,
        timeout=WAIT_MS,
    )

    # Opening the ticket screen acknowledges the completed response, but the
    # pending permission keeps the reply unseen.
    with page.expect_response(
        lambda response: response.url.endswith(
            f"/api/tickets/{ticket_id}/acknowledge-completed-response"
        ),
        timeout=WAIT_MS,
    ) as acknowledgement_response:
        page.goto(f"{server.base}/#/ticket/{ticket_id}")
    assert acknowledgement_response.value.status == 200
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    acknowledged = projection.read(ticket_id)
    assert acknowledged.has_completed_response_awaiting_user is False
    assert acknowledged.has_completed_response is True
    assert acknowledged.has_pending_permission is True

    page.goto(f"{server.base}/#/workspace")
    page.wait_for_selector(mark, timeout=WAIT_MS)
    assert page.get_attribute(mark, "data-reply-state") == "unseen"
    assert page.get_attribute(mark, "data-stage-state") == "current-awaiting-approval"


def test_workspace_buckets_render_membership_in_canonical_order(
    server, context_factory, open_page, cli, api
) -> None:
    errored = _create_ticket(cli, server, "Errored group ticket")
    needs_user_ticket = _create_ticket(cli, server, "Needs user ticket")
    kickoff_idle = _create_ticket(cli, server, "Kickoff idle ticket")
    kickoff_awaiting = _create_ticket(cli, server, "Kickoff awaiting ticket")
    empty_older = _create_ticket(cli, server, "Empty older ticket")
    empty_newer = _create_ticket(cli, server, "Empty newer ticket")
    user_owned = _create_ticket(cli, server, "User ticket")
    paired = _create_ticket(cli, server, "Paired ticket")
    running = _create_ticket(cli, server, "Agent ticket")
    approval = _create_ticket(cli, server, "Awaiting approval ticket")
    closing = _create_ticket(cli, server, "Closeout idle ticket")
    done = _create_ticket(cli, server, "Done ticket")
    blocked_idle = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Blocked idle ticket",
        "--project-id",
        "project_vylo",
        "--blocked-by",
        empty_older,
    )["id"]
    blocked_running = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Blocked running ticket",
        "--project-id",
        "project_vylo",
        "--blocked-by",
        empty_older,
    )["id"]

    _set_ticket_status(server, errored, "errored", backend_error="boom")
    _set_ticket_status(server, needs_user_ticket, "needs_user")
    _set_ticket_stage(server, kickoff_idle, "needs_kickoff")
    _set_ticket_stage(server, kickoff_awaiting, "needs_kickoff")
    _set_ticket_status(server, kickoff_awaiting, "awaiting_approval")
    for ticket_id in (empty_older, empty_newer):
        _set_ticket_stage(server, ticket_id, "needs_success")
    _set_ticket_updated_at(server, empty_older, 10)
    _set_ticket_updated_at(server, kickoff_idle, 20)
    _set_ticket_updated_at(server, empty_newer, 30)
    _set_ticket_status(server, user_owned, "user")
    _set_ticket_status(server, paired, "paired")
    _set_ticket_status(server, running, "agent")
    _set_ticket_stage(server, approval, "needs_plan")
    _set_ticket_status(server, approval, "awaiting_approval")
    _set_ticket_stage(server, closing, "needs_closeout")
    _set_ticket_updated_at(server, closing, 40)
    _set_ticket_stage(server, done, "done")
    _set_ticket_stage(server, blocked_idle, "needs_success")
    _set_ticket_stage(server, blocked_running, "needs_success")
    _set_ticket_status(server, blocked_running, "agent")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        _bucket("empty"),
        settled=True,
    )

    # All nine status groups are populated, so all render, in display order with
    # the humanized status names as labels.
    rendered = page.eval_on_selector_all(
        "[data-bucket-section]",
        "els => els.map(el => el.getAttribute('data-bucket-key'))",
    )
    assert rendered == BUCKET_ORDER
    labels = page.eval_on_selector_all(
        "[data-bucket-section] > summary .board-workspace-bucket-label",
        "els => els.map(el => el.textContent.trim())",
    )
    assert labels == [BUCKET_LABELS[key] for key in BUCKET_ORDER]

    # Blocked and Done are collapsed by default; every other group is open.
    for key in BUCKET_ORDER:
        is_open = page.get_attribute(_bucket(key), "open") is not None
        assert is_open is (key not in ("blocked", "done")), key

    # Membership: exactly one group per ticket, and the group is the status.
    memberships = {
        errored: "errored",
        needs_user_ticket: "needs_user",
        # Stage no longer subdivides a group: an idle Ticket resting at the
        # Kickoff or the Closeout stage is just empty.
        kickoff_idle: "empty",
        closing: "empty",
        empty_older: "empty",
        empty_newer: "empty",
        # ...and an awaiting-approval Ticket is one group whatever its stage.
        kickoff_awaiting: "awaiting_approval",
        approval: "awaiting_approval",
        user_owned: "user",
        paired: "paired",
        running: "agent",
        # A Ticket resting with a live blocker carries the blocked status.
        blocked_idle: "blocked",
        # The blocker link no longer groups anything on its own: this Ticket has
        # the same live blocker and groups by its status.
        blocked_running: "agent",
        done: "done",
    }
    for ticket_id, bucket_key in memberships.items():
        card = f'[data-card][data-ticket-id="{ticket_id}"]'
        assert page.locator(card).count() == 1, ticket_id
        assert page.locator(f"{_bucket(bucket_key)} {card}").count() == 1, ticket_id
    assert (
        page.locator(
            f'{_bucket("blocked")} [data-card][data-ticket-id="{blocked_running}"]'
        ).count()
        == 0
    )

    # Rows within a group sort by activity, newest first, and carry no chips.
    empty_titles = page.eval_on_selector_all(
        f'{_bucket("empty")} [data-card] .list-row-title',
        "els => els.map(el => el.textContent.trim())",
    )
    assert empty_titles == [
        "Closeout idle ticket",
        "Empty newer ticket",
        "Kickoff idle ticket",
        "Empty older ticket",
    ]
    assert page.locator("[data-card] .chip").count() == 0
    row_child_counts = page.eval_on_selector_all(
        "[data-card]",
        "els => els.map(el => el.children.length)",
    )
    assert set(row_child_counts) == {2}


def test_workspace_bucket_disclosures_collapse_and_chevrons_reveal_on_intent(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = _create_ticket(cli, server, "Nested disclosure ticket")
    _add_today(api, server, ticket_id)
    _set_ticket_stage(server, ticket_id, "needs_plan")

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        _bucket("empty"),
        settled=True,
    )

    bucket = _bucket("empty")
    card = f'[data-card][data-ticket-id="{ticket_id}"]'
    assert page.locator(bucket).get_attribute("open") == ""

    chevron = page.locator(f"{bucket} > summary > .disclosure-chev")
    assert chevron.evaluate("el => getComputedStyle(el).opacity") == "0"
    page.hover(f"{bucket} > summary")
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '1'",
        arg=f"{bucket} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )
    chevron_geometry = page.eval_on_selector(
        f"{bucket} > summary",
        "summary => ({summary: summary.getBoundingClientRect(), "
        "chevron: summary.querySelector('.disclosure-chev').getBoundingClientRect()})",
    )
    assert abs(chevron_geometry["summary"]["right"] - chevron_geometry["chevron"]["right"]) < 1

    page.mouse.move(0, 0)
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '0'",
        arg=f"{bucket} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )
    # Keyboard intent reveals the chevron too: Tab from the Chief-of-Staff peer
    # lands on the bucket summary.
    page.click("[data-chief-of-staff-button]")
    page.keyboard.press("Tab")
    page.wait_for_function(
        "selector => getComputedStyle(document.querySelector(selector)).opacity === '1'",
        arg=f"{bucket} > summary > .disclosure-chev",
        timeout=WAIT_MS,
    )

    page.click(f"{bucket} > summary")
    assert page.locator(bucket).get_attribute("open") is None
    assert page.locator(card).is_hidden()
    page.click(f"{bucket} > summary")
    assert page.locator(bucket).get_attribute("open") == ""
    assert page.locator(card).is_visible()
