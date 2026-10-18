# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import json
    import os
    import time
    from urllib.parse import parse_qs

    from playwright.sync_api import expect

    ticket = dict(
        project_id="project_one",
        project="One",
        sprint_id="sp_old",
        sprint_item_id="outcome_kept",
        id="t_guidance",
        title="Guidance ticket",
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model=None,
        employee_launch_reasoning_effort=None,
        employee_configuration_editable=False,
        stage="needs_success_condition",
        ceiling="needs_success_condition",
        sprint_item_title="Kept outcome",
        ticket_status="awaiting_approval",
        ceiling_holder={"kind": "chief", "id": "chief"},
        conversation_id=None,
        conversation_history=[],
        day_ids=[],
        blocked=False,
        blocker_summary={"blocked_by": []},
        recap="Orientation",
        guidance="Original **constraint**",
        field_values={"brief": "Request"},
        pending_proposal={
            "field": "success_condition",
            "body": "A result",
            "proposed_by": "agent",
            "created_at": 1,
        },
    )
    stages = [
        dict(
            id=s,
            label=label,
            gating_field=f,
            is_terminal=f is None,
            ownership_mode="worker" if f else None,
        )
        for s, label, f in [
            ("needs_brief", "Kickoff", "brief"),
            ("needs_success_condition", "Success", "success_condition"),
            ("done", "Done", None),
        ]
    ]
    personal_stages = [
        dict(id=s, label=label, gating_field=f, is_terminal=f is None, ownership_mode=o)
        for s, label, f, o in [
            ("needs_brief", "Kickoff", "brief", "user"),
            ("needs_outcome", "Outcome", "outcome", "user"),
            ("needs_consequences", "Closeout", "consequences", "worker"),
            ("done", "Done", None, None),
        ]
    ]
    manifest = {
        "worker_types": [
            dict(
                worker_type="coding",
                label="Coding",
                stages=stages,
                advance={
                    "needs_brief": "needs_success_condition",
                    "needs_success_condition": "done",
                },
                ceiling_range=["needs_brief", "needs_success_condition", "done"],
                default_ceiling="needs_success_condition",
                worker_profile_id="panels-worker-coding",
                default_backend="codex",
                default_model=None,
                default_reasoning_effort=None,
                fields=[
                    {"id": "brief", "label": "Kickoff"},
                    {"id": "success_condition", "label": "Success"},
                ],
            ),
            dict(
                worker_type="personal",
                label="Personal",
                stages=personal_stages,
                advance={
                    "needs_brief": "needs_outcome",
                    "needs_outcome": "needs_consequences",
                    "needs_consequences": "done",
                },
                ceiling_range=["needs_brief", "needs_outcome", "needs_consequences", "done"],
                default_ceiling="needs_brief",
                worker_profile_id="panels-worker-personal-task",
                default_backend="codex",
                default_model=None,
                default_reasoning_effort=None,
                fields=[
                    {"id": "brief", "label": "Kickoff"},
                    {"id": "outcome", "label": "Outcome"},
                    {"id": "consequences", "label": "Closeout"},
                ],
            ),
        ]
    }
    worker_detail = {
        "manifest": manifest["worker_types"][0],
        "settings": {
            "worker_type": "coding",
            "specialist_skill": {
                "name": "panels-worker-coding",
                "description": "Coding worker",
                "markdown_body": "# Coding",
            },
            "launch_defaults": {
                "employee_backend": "codex",
                "employee_launch_model": None,
                "employee_launch_reasoning_effort": None,
            },
        },
    }
    writes = []
    placement_writes = []
    ceiling_writes = []
    approvals = []
    revisions = []
    session_revised = False

    def respond(route):
        nonlocal session_revised
        path = route.request.url.split("/api/", 1)[1]
        # A single object is read through its collection address now: tickets?detail=full&id=X.
        if path.startswith("tickets?"):
            path = "tickets/" + parse_qs(path.split("?", 1)[1])["id"][0]
        if path == "tickets/t_guidance":
            if route.request.method == "PATCH":
                body = route.request.post_data_json
                assert not ({"project_id", "sprint_id", "sprint_item_id"} & set(body))
                if "guidance" in body:
                    writes.append({"guidance": body["guidance"]})
                    ticket["guidance"] = body["guidance"]
                elif "field_values" in body:
                    writes.append({"value": body["field_values"]})
                    ticket["field_values"].update(body["field_values"])
                elif "ceiling" in body or "ceiling_holder" in body:
                    ceiling_writes.append(body)
                    ticket.update(body)
                else:
                    placement_writes.append(body)
                    ticket.update(body)
            result = ticket
        elif path == "tickets/t_open" and route.request.method == "PATCH":
            body = route.request.post_data_json
            ceiling_writes.append(body)
            result = {**ticket, "id": "t_open", "ticket_status": "empty", "pending_proposal": None}
        elif path == "tickets/t_personal/complete/outcome":
            assert route.request.method == "POST"
            body = route.request.post_data_json
            writes.append({"personal_outcome": body})
            result = {
                **ticket,
                "id": "t_personal",
                "worker_type": "personal",
                "stage": "needs_consequences",
                "ceiling": "needs_consequences",
                "field_values": {"brief": "Context", "outcome": body["body"]},
                "pending_proposal": None,
                "ticket_status": "empty",
                "assigned": False,
            }
        elif path == "tickets/t_personal_pending":
            result = {
                **ticket,
                "id": "t_personal_pending",
                "worker_type": "personal",
                "stage": "needs_outcome",
                "ceiling": "needs_outcome",
                "ceiling_holder": {"kind": "owner", "id": "owner"},
                "field_values": {"brief": "Original personal brief"},
                "pending_proposal": {
                    "field": "outcome",
                    "body": "Original personal outcome proposal",
                    "revision": 1,
                    "proposed_by": "agent",
                    "created_at": 1,
                },
            }
        elif path == "tickets/t_personal":
            # assigned is what the route reads to decide the owner may edit this Stage.
            # The server puts it on every Ticket payload, so this stand-in carries it too.
            result = {
                **ticket,
                "id": "t_personal",
                "worker_type": "personal",
                "stage": "needs_outcome",
                "ceiling": "needs_outcome",
                "field_values": {"brief": "Context"},
                "pending_proposal": None,
                "ticket_status": "empty",
                "assigned": True,
            }
        elif path == "tickets/t_kickoff":
            result = {
                **ticket,
                "id": "t_kickoff",
                "stage": "needs_brief",
                "ceiling": "needs_brief",
                "ceiling_holder": {"kind": "chief", "id": "chief"},
                "sprint_item_id": None,
                "sprint_item_title": None,
                "pending_proposal": {
                    "field": "brief",
                    "body": "Opened for somebody else",
                    "proposed_by": "chief",
                    "created_at": 1,
                },
                "field_values": {},
            }
        elif path == "tickets/t_artifacts":
            result = {
                **ticket,
                "id": "t_artifacts",
                "title": "Artifact links in place",
                "conversation_id": "artifact-conversation",
                "conversation_history": [
                    {"conversation_id": "past-artifact-conversation", "created_at": 1},
                    {"conversation_id": "artifact-conversation", "created_at": 2},
                ],
                "field_values": {
                    "brief": "Saved [HTML plan](/files/sprint-items/si_artifacts/artifacts/plan.html?mode=read#summary). "
                    "[Ticket document](/files/tickets/t_artifacts/artifacts/plan.md) "
                    "[Receipt](/files/tickets/t_artifacts/artifacts/receipt.json) "
                    "[Raw receipt](/files/sprint-items/si_artifacts/artifacts/receipt.json) "
                    "[Unsupported](/files/sprint-items/si_artifacts/artifacts/archive.zip) "
                    "[External](https://example.test/files/sprint-items/si_artifacts/plan.html) "
                    "[Dev preview](/dev/tickets/t_artifacts/8791/) "
                    "[Malformed](/files/sprint-items/si_artifacts/artifacts/%252e%252e/plan.html) "
                    "[Explicit receipt](#/preview?source=ticket&ticket=t_artifacts&path=artifacts%2Freceipt.json)"
                },
                "pending_proposal": {
                    "field": "success_condition",
                    "body": "Proposed [Markdown plan](/files/sprint-items/si_artifacts/artifacts/plan.md).",
                    "proposed_by": "agent",
                    "created_at": 1,
                },
            }
        elif path == "conversation/conversations/artifact-conversation":
            result = dict(
                conversation_id="artifact-conversation",
                backend_key="codex",
                supports_steer=True,
                model="gpt-test",
                reasoning_effort=None,
                workspace_folder="/tmp",
                access="full",
                role_text=None,
                identity_environment_variable_names=[],
                latest_sequence=1,
                owner_read_through_sequence=1,
                is_running=False,
                held_prompts=[],
                pending_permission_ask=None,
                pending_user_input=None,
                composer_catalog=[],
            )
        elif path.startswith("conversation/conversations/artifact-conversation/events?"):
            result = {
                "events": [
                    dict(
                        conversation_id="artifact-conversation",
                        sequence=1,
                        created_at=1,
                        kind="message_to_owner",
                        payload={
                            "text": "Conversation [Audio plan](/files/sprint-items/si_artifacts/artifacts/plan.wav).",
                            "sender_label": "Worker",
                            "sender": {"kind": "ticket", "id": "t_artifacts"},
                            "recipient": {"kind": "owner", "id": "owner"},
                        },
                    )
                ]
            }
        elif path.endswith("/tail"):
            route.fulfill(status=200, content_type="text/event-stream", body="")
            return
        elif path == "tickets/t_with_session/reject":
            assert route.request.method == "POST"
            revisions.append(route.request.post_data_json)
            session_revised = True
            result = {**ticket, "id": "t_with_session", "pending_proposal": None}
        elif path == "tickets/t_with_session":
            result = {
                **ticket,
                "id": "t_with_session",
                "conversation_id": "existing-worker-session",
                "pending_proposal": None if session_revised else ticket["pending_proposal"],
            }
        elif path == "tickets/t_open":
            result = {
                **ticket,
                "id": "t_open",
                "ticket_status": "empty",
                "pending_proposal": None,
                "ceiling_holder": {"kind": "owner", "id": "owner"},
            }
        elif path == "tickets/t_done":
            result = {
                **ticket,
                "id": "t_done",
                "stage": "done",
                "ticket_status": "empty",
                "pending_proposal": None,
            }
        elif path.endswith("/accept/success_condition"):
            assert route.request.method == "POST"
            approvals.append(route.request.post_data_json)
            result = {**ticket, "pending_proposal": None, "ticket_status": "empty"}
        elif path == "worker-types":
            result = manifest
        elif path == "workers/coding":
            result = worker_detail
        elif path == "conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    page = browser.new_page()
    page.on("pageerror", lambda e: print(str(e), flush=True))
    page.route("**/api/**", respond)

    def serve_file(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith(".html"):
            route.fulfill(
                content_type="text/html",
                body='<html><body style="background:#fff;color:#17202a;padding:24px;font:18px system-ui"><h1>Artifact plan</h1><p>Opened over the Ticket.</p></body></html>',
            )
        elif path.endswith(".md"):
            route.fulfill(
                content_type="text/markdown",
                body="# Markdown plan\n\nRead [Nested image](/files/sprint-items/si_artifacts/artifacts/image.svg).",
            )
        elif path.endswith(".svg"):
            route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="300" height="180"><rect width="300" height="180" fill="steelblue"/></svg>',
            )
        else:
            route.fulfill(content_type="application/octet-stream", body="fixture")

    page.route("**/files/**", serve_file)
    page.goto([__file__, *urls][1])
    expect(page.locator("[data-project-fact]")).to_have_text("One")
    expect(page.locator("[data-ticket-identity]")).to_have_text("One · Coding")
    expect(page.locator("[data-parent-link]")).to_have_count(0)
    assert (
        page.locator(
            "[data-project-control], [data-outcome-control], [data-sprint-control]"
        ).count()
        == 0
    )
    assert page.get_by_label("Ticket project").count() == 0
    assert page.get_by_label("Ticket outcome").count() == 0
    assert page.get_by_label("Ticket Sprint").count() == 0
    assert page.locator("[data-ticket-guidance], [data-ticket-history]").count() == 0
    assert page.get_by_text("Old unapproved draft").count() == 0
    # A later step with no Worker session cannot deliver a revision; do not offer an invalid send.
    expect(page.locator("[data-send-back]")).to_be_disabled()
    expect(page.locator("[data-return-unavailable]")).to_contain_text(
        "No existing Worker conversation"
    )
    # Waiting uses the same editable stage+reviewer control as running. Scope-only
    # rerouting preserves the pending body; choices past the boundary announce approval.
    parked_leash = page.locator("[data-approval-block]")
    expect(parked_leash.locator("[data-scope-ceiling]")).to_have_count(0)
    parked_leash.locator("[data-reviewer-heading]").click()
    expect(parked_leash.get_by_role("listbox")).to_have_attribute(
        "aria-label", "Who holds the ceiling"
    )
    assert parked_leash.locator("[data-ceiling-picker-locked]").count() == 0
    assert not parked_leash.locator('[data-ceiling-part="ceiling"]').is_disabled()
    parked_leash.locator('[data-ceiling-part="holder"]').click()
    parked_leash.get_by_role("listbox").press("u")
    assert ceiling_writes == []
    parked_leash.get_by_role("listbox").press("Enter")
    for _ in range(100):
        if ceiling_writes:
            break
        page.wait_for_timeout(50)
    assert ceiling_writes == [{"ceiling_holder": {"kind": "owner", "id": "owner"}}]
    assert approvals == []

    page.evaluate("window.__showTicket('t_open')")
    page.locator('[data-ticket-id="t_open"]').wait_for()
    leash = page.locator("[data-leash]")
    # No proposal parked, so the whole ceiling is here: the stage and the holder.
    expect(leash.locator("[data-scope-ceiling]")).to_have_attribute(
        "aria-label", "Approved until Success, then User reviews"
    )
    assert leash.locator("select").count() == 0
    assert leash.locator("[data-scope-ceiling]").count() == 1
    assert leash.locator("[data-scope-holder]").count() == 0
    leash.locator("[data-scope-ceiling]").click()
    assert leash.locator("[data-ceiling-picker-panel]").count() == 1
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    assert (
        leash.locator('[data-ceiling-part="ceiling"] .picker-rail-row-detail').inner_text()
        == "Success"
    )
    assert (
        leash.locator('[data-ceiling-part="holder"] .picker-rail-row-detail').inner_text() == "User"
    )
    # A stage chosen and then abandoned is a draft. Escape discards it: nothing is sent,
    # the shut trigger still reads the saved ceiling, and the list ticks it again.
    leash.get_by_role("listbox").press("End")
    leash.get_by_role("listbox").press("Enter")
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    expect(leash.locator('[data-ceiling-part="ceiling"] .picker-rail-row-detail')).to_have_text(
        "Done"
    )
    leash.get_by_role("listbox").press("Escape")
    expect(leash.get_by_role("listbox")).to_have_count(0)
    page.wait_for_timeout(200)
    assert ceiling_writes == [{"ceiling_holder": {"kind": "owner", "id": "owner"}}], ceiling_writes
    assert (
        leash.locator("[data-scope-ceiling]").get_attribute("aria-label")
        == "Approved until Success, then User reviews"
    )
    leash.locator("[data-scope-ceiling]").click()
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    assert (
        leash.locator('[role="option"][aria-selected="true"]').get_attribute(
            "data-ceiling-picker-choice"
        )
        == "needs_success_condition"
    )
    # Each rail names its part and retains the selected draft as the other list opens.
    assert "Approved until Success" in leash.locator("[data-scope-ceiling]").get_attribute(
        "aria-label"
    )
    assert "then User reviews" in leash.locator("[data-scope-ceiling]").get_attribute("aria-label")
    leash.locator('[data-ceiling-part="holder"]').click()
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    leash.locator('[data-ceiling-part="ceiling"]').click()
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    leash.get_by_role("listbox").press("End")
    leash.get_by_role("listbox").press("Enter")
    # A stage choice still leads to the reviewer, so setting both stays one sequence.
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    leash.get_by_role("listbox").press("c")
    leash.get_by_role("listbox").press("Enter")
    expect(leash.get_by_role("listbox")).to_have_count(0)
    for _ in range(100):
        if len(ceiling_writes) == 2:
            break
        page.wait_for_timeout(50)
    assert ceiling_writes[-1] == {
        "ceiling": "done",
        "ceiling_holder": {"kind": "chief", "id": "chief"},
    }, ceiling_writes

    # A reviewer changed on its own writes the reviewer alone. The ceiling is not sent,
    # so a Ticket cannot have its ceiling moved by a reader who only changed the queue.
    leash.locator("[data-scope-ceiling]").click()
    leash.locator('[data-ceiling-part="holder"]').click()
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    leash.locator('[data-ceiling-picker-choice="owner"]').click()
    expect(leash.get_by_role("listbox")).to_have_count(0)
    for _ in range(100):
        if len(ceiling_writes) == 3:
            break
        page.wait_for_timeout(50)
    assert ceiling_writes[-1] == {"ceiling_holder": {"kind": "owner", "id": "owner"}}, (
        ceiling_writes
    )

    # A reviewer chosen and then abandoned is discarded too, and letters typed at one
    # half never join letters typed at the other.
    leash.locator("[data-scope-ceiling]").click()
    expect(leash.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    leash.get_by_role("listbox").press("d")
    leash.locator('[data-ceiling-part="holder"]').click()
    leash.get_by_role("listbox").press("c")
    assert (
        leash.locator("[data-listbox-picker-active]").get_attribute("data-ceiling-picker-choice")
        == "chief"
    )
    leash.get_by_role("listbox").press("Escape")
    expect(leash.get_by_role("listbox")).to_have_count(0)
    page.wait_for_timeout(200)
    assert len(ceiling_writes) == 3, ceiling_writes
    assert (
        leash.locator("[data-scope-ceiling]").get_attribute("aria-label")
        == "Approved until Success, then User reviews"
    )
    leash.locator("[data-scope-ceiling]").click()
    page.locator(".ticket-title").click()
    expect(leash.get_by_role("listbox")).to_have_count(0)
    assert page.locator("[data-copy], [data-ticket-takeover-toggle]").count() == 0
    assert placement_writes == []
    assert page.locator(".ticket-operating").count() == 0

    def assert_ticket_layout(width):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(50)
        assert page.evaluate(
            """() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1"""
        )
        identity_box = page.locator("[data-ticket-identity]").bounding_box()
        title_box = page.locator(".ticket-title").bounding_box()
        leash.locator("[data-scope-ceiling]").click()
        menu_box = leash.locator("[data-ceiling-picker-panel]").bounding_box()
        assert identity_box and title_box and menu_box
        assert identity_box["y"] + identity_box["height"] <= title_box["y"] + 1
        assert menu_box["x"] >= -1
        assert menu_box["x"] + menu_box["width"] <= width + 1
        assert page.evaluate(
            """() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1"""
        )
        leash.get_by_role("listbox").press("Escape")

    assert_ticket_layout(1280)
    assert_ticket_layout(390)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.evaluate("window.__showTicket('t_guidance')")
    page.locator('[data-ticket-id="t_guidance"]').wait_for()
    assert page.locator("[data-accept]").count() == 1
    proposal = page.locator(
        '[data-approval-block][data-field="success_condition"] [contenteditable]'
    )
    proposal.click()
    proposal.fill("Edited pending result")
    proposal.press("Tab")
    page.wait_for_function("document.body.textContent.includes('Edited pending result')")
    page.locator("[data-stage-fold]").evaluate("(element) => element.open = true")
    page.locator('[data-stage-fold] [data-field="brief"]').evaluate(
        "(element) => element.open = true"
    )
    saved = page.locator('[data-stage-fold] [data-field="brief"] [contenteditable]')
    saved.click()
    saved.fill("Edited saved kickoff")
    saved.press("Tab")
    page.wait_for_function("document.body.textContent.includes('kickoff')")
    assert page.locator("summary").filter(has_text="Notes").count() == 0
    # The proposal edit stays in the browser until it is approved: a proposal has two
    # outcomes and no third door, so nothing is written back over the author's text.
    assert writes == [{"value": {"brief": "Edited saved kickoff"}}]
    page.evaluate("window.__showTicket('t_personal')")
    page.locator('[data-ticket-id="t_personal"]').wait_for()
    personal_outcome = page.locator(
        '[data-worker-live-card] [data-field="outcome"] [contenteditable]'
    )
    expect(personal_outcome).to_have_count(1)
    assert page.locator('[data-field="consequences"] [contenteditable]').count() == 0
    personal_outcome.fill("Completed by user")
    personal_outcome.press("Tab")
    page.wait_for_function("document.body.textContent.includes('Completed by user')")
    assert writes[-1] == {"personal_outcome": {"body": "Completed by user"}}
    page.evaluate("window.__showTicket('t_kickoff')")
    page.locator('[data-ticket-id="t_kickoff"]').wait_for()
    expect(page.locator("[data-project-fact]")).to_have_text("One")
    assert page.locator("[data-outcome-fact]").count() == 0
    assert page.locator("[data-copy]").count() == 0
    # Kickoff keeps its no-revision exception while both ceiling halves remain editable.
    kickoff_leash = page.locator("[data-approval-block]")
    expect(page.locator("[data-send-back]")).to_have_count(0)
    kickoff_leash.locator("[data-accept]").click()
    assert not kickoff_leash.locator('[data-ceiling-part="ceiling"]').is_disabled()
    kickoff_leash.locator('[data-ceiling-part="holder"]').click()
    assert [
        option.get_attribute("data-ceiling-picker-choice")
        for option in kickoff_leash.locator('[role="option"]').all()
    ] == ["owner", "chief"]
    kickoff_leash.get_by_role("listbox").press("Escape")
    page.evaluate("window.__showTicket('t_with_session')")
    page.locator('[data-ticket-id="t_with_session"]').wait_for()
    expect(page.locator("[data-send-back]")).to_be_enabled()
    expect(page.locator("[data-return-unavailable]")).to_have_count(0)
    page.locator("[data-send-back]").click()
    expect(page.locator("[data-revision-note]")).to_be_visible()
    exact_revision = "Keep the agreed scope.\nPreserve the attributed details."
    page.locator("[data-revision-note]").fill(exact_revision)
    page.locator("[data-revision-send]").click()
    expect(
        page.locator('[data-ticket-id="t_with_session"] [data-approval-block][data-mode="pending"]')
    ).to_have_count(0)
    assert revisions == [{"message": exact_revision}]

    page.evaluate("window.__showTicket('t_done')")
    page.locator('[data-ticket-id="t_done"]').wait_for()
    assert page.locator("[data-copy]").count() == 0
    assert page.locator(".ticket-operating").count() == 0
    page.evaluate("window.__showTicket('t_personal_pending')")
    historical = page.locator('[data-ticket-id="t_personal_pending"]')
    expect(historical).to_have_attribute("data-stage", "needs_outcome")
    expect(historical).to_contain_text("Original personal outcome proposal")
    expect(page.locator("[data-ticket-manifest-error]")).to_have_count(0)
    page.evaluate("window.__showReview('t_personal_pending', 'outcome')")
    historical_review = page.locator('[data-review-card][data-ticket-id="t_personal_pending"]')
    expect(historical_review).to_have_attribute("data-field", "outcome")
    expect(historical_review).to_contain_text("Original personal outcome proposal")
    expect(page.locator("[data-review-manifest-error]")).to_have_count(0)
    page.evaluate("window.__showReview()")
    expect(page.locator("[data-ticket-guidance]")).to_have_count(1)
    guidance = page.locator("[data-ticket-guidance]")
    guidance.locator("summary").click()
    expect(guidance).to_contain_text("Original constraint")
    assert guidance.locator("[contenteditable]").count() == 0
    # Review shows what the author wrote. The edit on the Ticket page was never saved
    # over it, because an edited proposal is approved as the edit or it is nothing.
    expect(page.locator('[data-approval-block][data-field="success_condition"]')).to_contain_text(
        "A result"
    )
    assert page.locator("[data-accept]").count() == 1
    # Review uses the same two-part gesture and reviewer-only route as Ticket.
    approve_row = page.locator('[data-approval-block][data-field="success_condition"]')
    expect(approve_row.locator("[data-scope-ceiling]")).to_have_count(0)
    approve_row.locator("[data-reviewer-heading]").click()
    expect(approve_row.get_by_role("listbox")).to_have_attribute(
        "aria-label", "Who holds the ceiling"
    )
    assert [
        option.get_attribute("data-ceiling-picker-choice")
        for option in approve_row.locator('[role="option"]').all()
    ] == ["owner", "chief", "outcome_kept"]
    expect(approve_row.get_by_role("option", name="Orchestrator", exact=True)).to_have_count(1)
    approve_row.get_by_role("listbox").press("Escape")
    approve_row.locator("[data-accept]").click()
    expect(approve_row.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    expect(approve_row.get_by_role("listbox")).to_have_attribute(
        "aria-label", "Who holds the ceiling"
    )
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    for _ in range(100):
        if approvals:
            break
        page.wait_for_timeout(50)
    assert len(approvals) == 1
    assert approvals[0]["next_ceiling"] == "done"
    assert approvals[0]["next_holder"] == {"kind": "chief", "id": "chief"}
    # Declared ownership is Config's to change. Every non-terminal Stage carries one
    # owner control, and the Stage table carries nothing else that edits.
    # Reuse this Ticket host: raw managed Markdown links must reach its delegated
    # handler across saved fields, proposals, nested documents, and the real transcript.
    artifact_started = time.monotonic()
    page.evaluate("window.__showTicket('t_artifacts')")
    page.locator('[data-ticket-id="t_artifacts"]').wait_for()
    page.locator("[data-conversation-rest-bar]").click()
    page.locator('[data-conversation-lens-choice="full"]').click()
    expect(page.locator("[data-conversation-input]")).to_be_visible()
    expect(page.get_by_role("button", name="Conversation options")).to_be_visible()
    page.get_by_role("button", name="Conversation options").click()
    expect(page.locator("[data-conversation-history]")).to_have_count(0)
    page.keyboard.press("Escape")

    page.locator("[data-stage-fold]").evaluate("(element) => element.open = true")
    page.locator('[data-stage-fold] [data-field="brief"]').evaluate(
        "(element) => element.open = true"
    )
    saved_artifacts = page.locator('[data-stage-fold] [data-field="brief"]')
    html_link = saved_artifacts.get_by_role("link", name="HTML plan", exact=True)
    assert html_link.get_attribute("href").startswith("/files/sprint-items/")
    original_url = page.url

    # Probe browser-owned gestures without allowing their default navigation to destroy
    # the fixture. The listener observes prevention after the Ticket handler, then cancels.
    def click_was_claimed(link, **options):
        return link.evaluate(
            """(anchor, options) => {
          let claimed;
          document.addEventListener('click', event => {
            claimed = event.defaultPrevented;
            event.preventDefault();
          }, {once:true});
          anchor.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, ...options}));
          return claimed;
        }""",
            options,
        )

    for modifier in ["ctrlKey", "metaKey", "shiftKey", "altKey"]:
        assert not click_was_claimed(html_link, **{modifier: True})
    assert not click_was_claimed(html_link, button=1)
    html_link.evaluate("a => a.setAttribute('target', '_blank')")
    assert not click_was_claimed(html_link)
    html_link.evaluate("a => a.removeAttribute('target')")
    html_link.evaluate("a => a.setAttribute('download', '')")
    assert not click_was_claimed(html_link)
    html_link.evaluate("a => a.removeAttribute('download')")
    html_link.evaluate(
        "a => a.addEventListener('click', event => event.preventDefault(), {once:true})"
    )
    assert click_was_claimed(html_link)
    expect(page.locator("[data-ticket-artifact]")).to_have_count(0)
    for label in [
        "Download receipt.json",
        "Raw receipt",
        "Unsupported",
        "External",
        "Dev preview",
        "Malformed",
    ]:
        assert not click_was_claimed(saved_artifacts.get_by_role("link", name=label, exact=True))
    html_link.click()
    viewer = page.locator("[data-ticket-artifact]")
    expect(viewer).to_have_attribute("aria-label", "plan.html")
    expect(
        viewer.frame_locator("iframe").get_by_role("heading", name="Artifact plan")
    ).to_be_visible()
    assert page.url == original_url
    if os.environ.get("PANELS_ARTIFACT_SCREENSHOT"):
        page.screenshot(path=os.environ["PANELS_ARTIFACT_SCREENSHOT"], full_page=True)
    viewer.get_by_role("button", name="Close", exact=True).click()
    page.locator("[data-approval-block]").get_by_role(
        "link", name="Markdown plan", exact=True
    ).click()
    expect(viewer).to_have_attribute("aria-label", "plan.md")
    viewer.get_by_role("link", name="Nested image", exact=True).click()
    expect(viewer).to_have_attribute("aria-label", "image.svg")
    expect(viewer.locator("img")).to_be_visible()
    viewer.get_by_role("button", name="Close", exact=True).click()
    saved_artifacts.get_by_role("link", name="Open plan.md", exact=True).click()
    expect(viewer).to_have_attribute("aria-label", "plan.md")
    viewer.get_by_role("button", name="Close", exact=True).click()
    saved_artifacts.get_by_role("link", name="Explicit receipt", exact=True).click()
    expect(viewer).to_have_attribute("aria-label", "receipt.json")
    expect(viewer.get_by_role("link", name="Download receipt.json", exact=True)).to_have_attribute(
        "download", ""
    )
    viewer.get_by_role("button", name="Close", exact=True).click()
    if page.locator('[data-conversation-pane][data-conversation-state="rest"]').count():
        page.locator("[data-conversation-input]").click()
    if page.locator('[data-conversation-pane][data-conversation-state="peeked"]').count():
        page.locator("[data-conversation-expand]").click()
    expect(page.locator("[data-conversation-pane]")).to_have_attribute(
        "data-conversation-state", "opened"
    )
    page.get_by_role("link", name="Audio plan", exact=True).click()
    expect(viewer).to_have_attribute("aria-label", "plan.wav")
    expect(viewer.locator("audio")).to_have_attribute(
        "src", "/files/sprint-items/si_artifacts/artifacts/plan.wav"
    )
    expect(page.locator("[data-conversation-pane]")).to_have_attribute(
        "data-conversation-state", "peeked"
    )
    assert page.url == original_url
    print(f"Artifact link browser coverage: {time.monotonic() - artifact_started:.2f}s", flush=True)
    page.evaluate("window.__showConfig()")
    stage_table = page.locator("[data-worker-stage-table]")
    stage_table.locator("[data-worker-stage-row]").first.wait_for()
    editable_rows = stage_table.locator('[data-worker-stage-row][data-terminal="false"]').count()
    assert editable_rows > 0
    assert stage_table.locator("[data-stage-owner-select]").count() == editable_rows
    assert stage_table.locator("select").count() == editable_rows
    assert stage_table.locator("input, [contenteditable]").count() == 0
    pass  # Shared runner owns the browser lifecycle.
