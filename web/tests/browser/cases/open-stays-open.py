# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import json
    from urllib.parse import parse_qs

    conversation_url, ticket_url, sprint_url = [__file__, *urls][1:4]

    FIELDS = ["brief", "success_condition", "what_changes", "plan"]
    STAGE_OF = {f: "needs_" + f for f in FIELDS}
    stages = [
        dict(id=STAGE_OF[f], label=f, gating_field=f, is_terminal=False, ownership_mode="worker")
        for f in FIELDS
    ]
    stages.append(
        dict(id="done", label="Done", gating_field=None, is_terminal=True, ownership_mode=None)
    )
    advance = {STAGE_OF[FIELDS[i]]: STAGE_OF[FIELDS[i + 1]] for i in range(len(FIELDS) - 1)}
    advance[STAGE_OF[FIELDS[-1]]] = "done"
    manifest = {
        "worker_types": [
            dict(
                worker_type="coding",
                label="Coding",
                stages=stages,
                advance=advance,
                ceiling_range=[s["id"] for s in stages],
                default_ceiling="done",
                worker_profile_id="panels-worker-coding",
                default_backend="claude",
                default_model=None,
                default_reasoning_effort=None,
                fields=[{"id": f, "label": f} for f in FIELDS],
            )
        ]
    }
    ticket = dict(
        project_id="project_one",
        project="One",
        sprint_id=None,
        sprint_item_id=None,
        id="t_open",
        title="Open ticket",
        worker_type="coding",
        employee_backend="claude",
        employee_launch_model=None,
        employee_launch_reasoning_effort=None,
        employee_configuration_editable=False,
        stage="needs_what_changes",
        ceiling="done",
        sprint_item_title=None,
        ticket_status="agent",
        ceiling_holder={"kind": "owner", "id": "owner"},
        conversation_id=None,
        conversation_history=[],
        day_ids=[],
        blocked=False,
        blocker_summary={"blocked_by": []},
        recap="Orientation",
        pending_proposal=None,
        field_values={
            "brief": "The request",
            "success_condition": "What done means",
            "what_changes": "The change",
        },
    )

    def serve_ticket(route):
        path = route.request.url.split("/api/", 1)[1]
        if path.startswith("tickets?"):
            path = "tickets/" + parse_qs(path.split("?", 1)[1])["id"][0]
        if path == "__advance":
            ticket["stage"] = "needs_plan"
            result = {"ok": True}
        elif path == "tickets/t_open":
            result = ticket
        elif path == "worker-types":
            result = manifest
        elif path == "conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    def is_open(page, selector):
        return page.locator(selector).first.evaluate("node => node.open")

    errors = []
    try:
        # --- The thread ---------------------------------------------------------------
        page = browser.new_page(viewport={"width": 900, "height": 760})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(conversation_url)
        page.wait_for_selector("[data-conversation-transcript]")
        page.evaluate("window.__settle()")

        page.locator("[data-conversation-work-fold]").first.click()
        row = page.locator('[data-conversation-tool="tool-2"]')
        row.first.click()
        page.evaluate("window.__settle()")
        assert row.first.get_attribute("aria-expanded") == "true", (
            "the reader could not open a tool call"
        )

        page.evaluate("window.__endTheTurn()")
        page.evaluate("window.__settle()")
        page.wait_for_timeout(200)
        page.locator("[data-conversation-turn-fold]").first.click()
        page.evaluate("window.__settle()")
        page.wait_for_timeout(100)
        reopened = page.locator('[data-conversation-tool="tool-2"]')
        assert reopened.count() == 1, "the tool call did not come back when the turn was opened"
        assert reopened.first.get_attribute("aria-expanded") == "true", (
            "the turn ending closed a tool call the reader had opened"
        )
        page.close()

        # --- The Ticket screen --------------------------------------------------------
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", serve_ticket)
        page.goto(ticket_url)
        page.wait_for_selector("[data-worker-live-card]")
        assert not is_open(page, "[data-stage-fold]"), "Steps did not start closed"
        assert page.locator('[data-field="plan"]').count() == 0, "future stage leaked into Steps"
        page.locator("[data-stage-fold-open]").click()
        page.locator('[data-field="what_changes"] summary').first.click()
        assert is_open(page, '[data-field="what_changes"]')
        page.evaluate("window.__refresh()")
        page.wait_for_timeout(200)
        assert is_open(page, "[data-stage-fold]"), "refresh closed the reader-opened Steps band"
        assert is_open(page, '[data-field="what_changes"]'), "refresh closed the opened step"

        page.evaluate("() => fetch('/api/__advance')")
        page.evaluate("window.__refresh()")
        page.wait_for_function(
            "document.querySelector('[data-worker-live-card]')?.textContent.includes('plan')"
        )
        assert is_open(page, "[data-stage-fold]"), "advancing closed the reader-opened Steps band"
        assert is_open(page, '[data-field="what_changes"]'), (
            "advancing the stage closed a step the reader had opened"
        )
        assert not is_open(page, '[data-field="plan"]'), (
            "the new current step opened history instead of staying in its live card"
        )
        page.close()

        # --- The Sprint documents -----------------------------------------------------
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(sprint_url)
        page.wait_for_selector('[data-phase="kickoff"]')

        page.locator('[data-phase="kickoff"] summary').first.click()
        page.locator('[data-phase="kickoff"] summary').first.click()
        page.wait_for_timeout(100)
        assert is_open(page, '[data-phase="kickoff"]')

        page.evaluate("window.__writeTheCheckpoint()")
        page.wait_for_timeout(500)
        assert is_open(page, '[data-phase="kickoff"]'), (
            "writing the Checkpoint closed the Kickoff the reader had opened"
        )
        page.close()
    finally:
        pass  # Shared runner owns the browser lifecycle.
    assert not errors, errors
    print("open stays open on all three surfaces")
