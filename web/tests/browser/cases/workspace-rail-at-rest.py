# ruff: noqa: E501
"""Shared browser case with canonical navigation-source coverage."""


def run(browser, urls):
    import runpy
    from pathlib import Path

    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
    )
    navigation_response = fixture["navigation_response"]
    import json
    import os
    import pathlib

    from playwright.sync_api import expect
    from tests.e2e.harness import browser_failure_evidence

    ORCHESTRATOR_RECAP = "Coordinate the workspace sidebar with its current shipping data paths."
    LONG_TITLE = (
        "Review the entire Worker title when the adjustable rail clips this long approval row"
    )

    def card(ticket_id, item_id, item_title, **values):
        row = {
            "id": ticket_id,
            "title": ticket_id,
            "is_backlogged": False,
            "deadline": None,
            "project_id": None,
            "project": None,
            "group_project_id": None,
            "group_project": None,
            "activity_at": 0,
            "has_pending_proposal": False,
            "ticket_status": "empty",
            "worker_type": "coding",
            "employee_backend": "codex",
            "stage": "needs_implementation",
            "stage_label": "Implementation",
            "gating_field": "implementation",
            "gating_field_label": "Implementation",
            "is_done": False,
            "blocked": False,
            "conversation_id": "fixture-ticket-" + ticket_id,
            "waiting_to_closeout": False,
            "recap_preview": "A concise recap from the current projection.",
            "sprint_item_id": item_id,
            "sprint_item_title": item_title,
            "awaiting_reply": False,
            "awaiting_answer": False,
            "awaiting_approval": False,
            "awaiting_agent_approval": False,
            "assigned": False,
            "agent_state": "idle",
        }
        row.update(values)
        return row

    def item(item_id):
        quiet = {
            "awaiting_reply": False,
            "awaiting_answer": False,
            "awaiting_approval": False,
            "awaiting_agent_approval": False,
            "assigned": False,
            "agent_state": "idle",
        }
        return {
            "id": item_id,
            "created_at": 0,
            "conversation_id": "fixture-item-" + item_id,
            "ticket_rollup": dict(quiet),
            **quiet,
        }

    board = {
        "columns": [
            {
                "stage": "needs_implementation",
                "cards": [
                    card(
                        "t_approval",
                        "si_needs_him",
                        "Needs him",
                        title=LONG_TITLE,
                        awaiting_approval=True,
                        awaiting_reply=True,
                        conversation_signal=dict(working=False, message_for_owner=True),
                    ),
                    card("t_assigned", "si_needs_him", "Needs him", assigned=True),
                    card(
                        "t_message",
                        "si_needs_him",
                        "Needs him",
                        awaiting_reply=True,
                        conversation_signal=dict(working=False, message_for_owner=True),
                    ),
                    card("t_answer", "si_needs_him", "Needs him", awaiting_answer=True),
                    card(
                        "t_agents_approval",
                        "si_needs_him",
                        "Needs him",
                        ticket_status="awaiting_approval",
                        awaiting_agent_approval=True,
                    ),
                    card(
                        "t_broken",
                        "si_needs_him",
                        "Needs him",
                        awaiting_approval=True,
                        agent_state="errored",
                    ),
                    card(
                        "t_backlog_owner",
                        "si_needs_him",
                        "Needs him",
                        title="Review saved workspace changes",
                        is_backlogged=True,
                        awaiting_approval=True,
                        awaiting_reply=True,
                        conversation_id="backlog-" + "t_backlog_owner",
                    ),
                    card(
                        "t_backlog_supervisor",
                        "si_needs_him",
                        "Needs him",
                        title="Prepare the next workspace pass",
                        is_backlogged=True,
                        awaiting_agent_approval=True,
                        awaiting_reply=True,
                        conversation_id="backlog-supervisor",
                    ),
                    card(
                        "t_chief_backlog",
                        None,
                        None,
                        title="Revisit workspace navigation",
                        is_backlogged=True,
                        awaiting_approval=True,
                        awaiting_reply=True,
                        conversation_id="backlog-chief",
                    ),
                    card(
                        "t_parent_quiet", "si_needs_him", "Needs him", title="Quiet ordinary Worker"
                    ),
                    card(
                        "t_parent_blocked",
                        "si_needs_him",
                        "Needs him",
                        title="Blocked ordinary Worker",
                        ticket_status="blocked",
                        blocked=True,
                    ),
                    card("t_quiet", "si_quiet", "Quiet", ticket_status="blocked", blocked=True),
                    card(
                        "t_done_overlap",
                        "si_needs_him",
                        "Needs him",
                        stage="done",
                        is_done=True,
                        awaiting_approval=True,
                    ),
                    card(
                        "t_blocked_overlap",
                        "si_needs_him",
                        "Needs him",
                        ticket_status="blocked",
                        blocked=True,
                        assigned=True,
                    ),
                    card(
                        "t_agent",
                        "si_needs_him",
                        "Needs him",
                        ticket_status="agent",
                        conversation_signal=dict(working=True, message_for_owner=False),
                    ),
                    card("t_consequences", "si_needs_him", "Needs him", waiting_to_closeout=True),
                    card(
                        "t_settled_backlog",
                        "si_settled",
                        "Settled work",
                        title="Saved settled message",
                        is_backlogged=True,
                        awaiting_reply=True,
                        conversation_id="backlog-settled",
                    ),
                    card(
                        "t_settled_message",
                        "si_settled",
                        "Settled work",
                        title="Ordinary settled message",
                        awaiting_reply=True,
                        conversation_id="ordinary-settled",
                    ),
                ],
            }
        ],
        "sprint_items": [
            item("si_needs_him"),
            item("si_quiet"),
            {**item("si_settled"), "settled": True},
        ],
    }
    for summary in board["sprint_items"]:
        summary["tickets"] = [
            row
            for column in board["columns"]
            for row in column["cards"]
            if row["sprint_item_id"] == summary["id"]
        ]
        summary["title"] = summary["tickets"][0]["sprint_item_title"]
        summary["recap_preview"] = ORCHESTRATOR_RECAP
    workers = {
        "chief_of_staff": {
            "conversation_id": "fixture-chief",
            "label": "Chief of Staff",
            "needs_me": False,
            "awaiting_reply": False,
            "agent_working": False,
            "latest_turn_ended_sequence": 0,
        },
        "workers": [],
    }
    FIELDS = ["brief", "success_condition", "what_changes", "plan", "implementation"]
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
        id="t_approval",
        title="Open ticket",
        worker_type="coding",
        employee_backend="claude",
        employee_launch_model=None,
        employee_launch_reasoning_effort=None,
        employee_configuration_editable=False,
        stage="needs_what_changes",
        ceiling="done",
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
    workspace = dict(
        id="si_needs_him",
        title="Needs him",
        body="The orchestrator brief",
        project_id=None,
        project=None,
        created_at=0,
        updated_at=0,
        supervisor=dict(agent_key="sprint_item:si_needs_him", conversation_id=None),
        tickets=[],
        artifacts=[],
        conversation_history=[],
        ticket_rollup={},
        settled=False,
    )
    preview_requests = []
    snapshot_reads = 0
    source_revision = 0
    last_source = None

    def navigation_snapshot():
        nonlocal snapshot_reads, source_revision, last_source
        snapshot_reads += 1
        authored = json.dumps({"board": board, "workers": workers}, sort_keys=True)
        if authored != last_source:
            source_revision += 1
            last_source = authored
        navigation_response.__globals__["_navigation_revision"] = source_revision - 1
        return navigation_response(board, workers)

    recap_requests = []

    def respond(route):
        path = route.request.url.split("/api/", 1)[1].split("?")[0]
        if path == "navigation/snapshot":
            result = navigation_snapshot()
        elif path == "board":
            raise AssertionError("sidebar summary query remains active: " + path)
        elif path == "workers/chief-of-staff/settings":
            result = workers["chief_of_staff"]
        elif path == "workers":
            assert any(

                    selection in route.request.frame.url
                    for selection in ("#/workspace/utility/settings", "#/workspace/utility/usage")

            ), ("sidebar must not request the worker roster", route.request.frame.url)
            result = workers
        elif path.endswith("/recap-preview"):
            raise AssertionError("unexpected preview request: " + path)
        elif path == "worker-types":
            result = manifest
        elif path == "skills":
            result = {"skills": []}
        elif path == "feedback":
            result = {"open": [], "open_count": 0, "handled_groups": []}
        elif path == "conversation/backends/codex/sign-in":
            result = {"sign_in": None}
        elif path.startswith("conversation/backends"):
            result = {"backends": []}
        elif path == "backends":
            result = {"backends": []}
        elif path.endswith("/workspace-content"):
            result = {key: workspace[key] for key in ["body", "artifacts", "conversation_history"]}
        elif path.endswith("/start-values"):
            result = {"backend_key": "codex", "model": None, "reasoning_effort": None}
        elif path.startswith("tickets/") or path == "tickets":
            result = ticket
        elif path == "recap-requests":
            target = route.request.post_data_json["target"]
            recap_requests.append(route.request.post_data_json)
            # The real route clears the line and wakes the agent. The projection this page
            # reads has to say the same, or the pending state has nothing to stand on.
            for summary in board["sprint_items"]:
                if summary["id"] == target["id"]:
                    summary["recap_preview"] = ""
                    summary["conversation_signal"] = dict(
                        working=True, message_for_owner=False
                    )
            result = {
                "target": target,
                "conversation_id": "c_hover",
                "fate": "started",
            }
        elif path == "chief/conversation":
            result = {"conversation_id": None}
        elif path == "chief/children":
            result = {"tickets": []}
        else:
            result = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    errors = []
    with browser_failure_evidence(browser, "workspace-rail-at-rest-browser"):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page.on(
            "pageerror",
            lambda error: (errors.append(str(error)), print("Page error:", error, flush=True)),
        )
        page.context.route("**/api/**", respond)
        page.goto([__file__, *urls][1])
        needs_him = page.locator('[data-sprint-item="si_needs_him"]')
        needs_him.wait_for()
        page.evaluate("window.__startRailWait()")
        page.wait_for_function("window.__railWaitStatus === 'included'")
        assert preview_requests == [], "initial source load must not prefetch every preview"
        assert page.locator('[data-sprint-item] [aria-current="page"]').count() == 0
        assert page.locator(".orchestrator-card.selected").count() == 0
        headers = needs_him.locator("[data-bucket-key]")
        expect(headers).to_have_count(7)
        assert [headers.nth(i).get_attribute("data-bucket-key") for i in range(7)] == [
            "awaiting_approval",
            "awaiting_answer",
            "assigned",
            "agent",
            "waiting_to_closeout",
            "status_awaiting_approval",
            "empty",
        ]
        for ticket_id, key in [
            ("t_approval", "awaiting_approval"),
            ("t_broken", "awaiting_approval"),
            ("t_assigned", "assigned"),
            ("t_message", "empty"),
            ("t_answer", "awaiting_answer"),
        ]:
            row = needs_him.locator('[data-ticket-id="' + ticket_id + '"]')
            expect(row).to_have_count(1)
            assert row.is_visible()
            assert (
                needs_him.locator(
                    '[data-bucket-key="' + key + '"] [data-ticket-id="' + ticket_id + '"]'
                ).count()
                == 1
            )
        expect(needs_him.locator('[data-ticket-id="t_agents_approval"]')).to_have_count(1)
        for excluded in ["t_done_overlap", "t_blocked_overlap", "t_parent_blocked"]:
            expect(needs_him.locator('[data-ticket-id="' + excluded + '"]')).to_have_count(0)
        expect(needs_him.locator('[data-bucket-key="empty"]')).to_contain_text("Other")
        quiet = page.locator('[data-sprint-item="si_quiet"]')
        assert quiet.locator("[data-bucket-key]").count() == 0
        assert quiet.locator("[data-ticket-id]").count() == 0
        expect(quiet.locator(".board-workspace-quiet-line")).to_have_text("Nothing in this list")
        expect(needs_him.locator(".item-head .stage-mark")).to_have_count(0)
        expect(needs_him.locator('[data-ticket-id="t_message"] .stage-mark')).to_have_count(1)
        expect(needs_him.locator('[data-ticket-id="t_agent"] .stage-mark')).to_have_attribute(
            "data-conversation-signal", "working"
        )
        expect(quiet.locator(".stage-mark")).to_have_count(0)
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
        chief = workers["chief_of_staff"]
        chief.update(needs_me=True, agent_working=True)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
        chief["awaiting_reply"] = True
        chief["conversation_signal"] = dict(working=False, message_for_owner=True)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(1)
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_attribute(
            "aria-label", "Unread message"
        )
        chief["awaiting_reply"] = False
        chief["conversation_signal"] = dict(working=False, message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
        chief_card = page.locator(".orchestrator-card.chief")

        def assert_backlog_absent():
            expect(page.locator('.approved-rail [data-bucket-key="backlog"]')).to_have_count(0)
            for ticket_id in [
                "t_backlog_owner",
                "t_backlog_supervisor",
                "t_chief_backlog",
                "t_settled_backlog",
            ]:
                expect(
                    page.locator('.approved-rail [data-ticket-id="' + ticket_id + '"]')
                ).to_have_count(0)
            expect(page.locator('[data-message-conversation="backlog-settled"]')).to_have_count(0)
            expect(
                page.locator('[data-message-conversation="backlog-t_backlog_owner"]')
            ).to_have_count(0)

        assert_backlog_absent()
        owner = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_backlog_owner")
        owner["is_backlogged"] = False
        page.evaluate("window.__refresh()")
        expect(
            needs_him.locator(
                '[data-bucket-key="awaiting_approval"] [data-ticket-id="t_backlog_owner"]'
            )
        ).to_have_count(1)
        assert owner["awaiting_approval"] is True
        owner["is_backlogged"] = True
        page.evaluate("window.__refresh()")
        assert_backlog_absent()
        page.get_by_role("button", name="Settled (1)").click()
        expect(page.locator('[data-message-conversation="ordinary-settled"]')).to_be_visible()
        assert_backlog_absent()
        page.get_by_role("button", name="Settled (1)").click()
        artifact_dir = pathlib.Path(
            os.environ.get(
                "PANELS_SIDEBAR_ARTIFACT_DIR",
                "/home/vps/Deployments/Panels/current/data/files/tickets/t_21sxtdb3/artifacts/revision",
            )
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        unread = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_message")
        running = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_agent")
        unread_row = needs_him.locator('[data-ticket-id="t_message"]')
        working_row = needs_him.locator('[data-ticket-id="t_agent"]')
        running["awaiting_reply"] = True
        running["conversation_signal"] = dict(working=True, message_for_owner=True)
        page.evaluate("window.__refresh()")
        expect(working_row.locator("[data-conversation-signal]")).to_have_attribute(
            "data-conversation-signal", "message"
        )
        expect(needs_him.locator('[data-bucket-key="awaiting_reply"]')).to_have_count(0)
        expect(needs_him.locator('[data-bucket-key="empty"]')).to_contain_text("t_message")
        expect(needs_him.locator('[data-bucket-key="agent"]')).to_contain_text("t_agent")
        expect(
            needs_him.locator(
                '[data-bucket-key="awaiting_approval"] [data-ticket-id="t_approval"] [data-conversation-signal]'
            )
        ).to_have_attribute("data-conversation-signal", "message")

        def row_state(row):
            return row.evaluate(
                "e=>({group:e.closest('[data-bucket-key]').dataset.bucketKey,tone:e.closest('[data-tone]').dataset.tone,color:getComputedStyle(e).color,order:[...e.closest('[data-bucket-key]').querySelectorAll('[data-ticket-id]')].map(x=>x.dataset.ticketId)})"
            )

        before_unread, before_working = (row_state(unread_row), row_state(working_row))
        assert before_unread["tone"] == before_working["tone"] == "quiet"
        page.wait_for_timeout(600)
        page.screenshot(path=str(artifact_dir / "sidebar-unread-quiet-working-and-needs-you.png"))
        unread["awaiting_reply"] = False
        unread["conversation_signal"] = dict(working=False, message_for_owner=False)
        approval = board["columns"][0]["cards"][0]
        approval_row = needs_him.locator('[data-ticket-id="t_approval"]')
        before_approval = row_state(approval_row)
        approval["awaiting_reply"] = False
        approval["conversation_signal"] = dict(working=False, message_for_owner=False)
        running["awaiting_reply"] = False
        running["conversation_signal"] = dict(working=True, message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(unread_row.locator(".stage-mark")).to_have_count(0)
        expect(working_row.locator("[data-conversation-signal]")).to_have_attribute(
            "data-conversation-signal", "working"
        )
        page.wait_for_timeout(600)
        after_unread, after_working = (row_state(unread_row), row_state(working_row))
        assert before_unread == after_unread
        assert before_working == after_working
        expect(approval_row.locator(".stage-mark")).to_have_count(0)
        after_approval = row_state(approval_row)
        assert before_approval == after_approval
        assert after_approval["tone"] == "attention"
        page.screenshot(path=str(artifact_dir / "sidebar-read-cleared-same-groups.png"))
        (artifact_dir / "sidebar-unread-clear-proof.json").write_text(
            json.dumps(
                dict(
                    quiet_before=before_unread,
                    quiet_after=after_unread,
                    working_before=before_working,
                    working_after=after_working,
                    approval_before=before_approval,
                    approval_after=after_approval,
                ),
                indent=2,
            )
        )
        needs_him.locator(".item-head").hover()
        panel = page.locator("[data-rail-hover-panel]")
        expect(panel).to_have_attribute("aria-hidden", "false")
        expect(panel.locator("[data-recap-text]")).to_have_text(ORCHESTRATOR_RECAP)
        expect(panel).not_to_contain_text(LONG_TITLE)
        for title in ["Review saved workspace changes", "Prepare the next workspace pass"]:
            expect(panel).not_to_contain_text(title)
        chief_card.locator(".item-head").hover()
        expect(panel).to_contain_text("Chief of Staff")
        expect(panel.locator("[data-recap-text]")).to_have_text("Nothing in this list right now.")
        expect(panel).not_to_contain_text("Revisit workspace navigation")
        # Every agent the panel can show keeps a recap, the Chief included.
        expect(panel.locator(".recap [data-request-recap-action]")).to_have_count(1)
        # The ask sits inside the recessed recap block, and the pointer has to be able to
        # leave the row, cross the gap, and press it before the panel closes behind it.
        needs_him.locator(".item-head").hover()
        ask = panel.locator(".recap [data-request-recap-action]")
        expect(ask).to_have_text("Ask for a recap")
        panel.hover()
        expect(panel).to_have_attribute("aria-hidden", "false")
        ask.click()
        # The real server pushes the cleared line down its own source. This host owns that
        # push, so it makes it rather than waiting for one that cannot arrive.
        page.evaluate("window.__refresh()")
        # The press clears the line. The panel then shows what an orchestrator without a
        # recap has always shown here, and the ask stays exactly where it was.
        expect(panel.locator("[data-recap-text]")).not_to_have_text(ORCHESTRATOR_RECAP)
        expect(ask).to_have_text("Ask for a recap")
        assert recap_requests == [{"target": {"kind": "sprint_item", "id": "si_needs_him"}}], (
            recap_requests
        )
        # The written recap lands in that same place, and the ask comes back with it.
        for summary in board["sprint_items"]:
            if summary["id"] == "si_needs_him":
                summary["recap_preview"] = ORCHESTRATOR_RECAP
                summary["conversation_signal"] = dict(working=False, message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(panel.locator("[data-recap-text]")).to_have_text(ORCHESTRATOR_RECAP)
        expect(ask).to_have_text("Ask for a recap")
        page.mouse.move(1200, 900)
        expect(panel).to_have_attribute("aria-hidden", "true")
        for viewport, label in [
            (dict(width=1440, height=1000), "desktop"),
            (dict(width=560, height=1000), "compact"),
        ]:
            page.set_viewport_size(viewport)
            for target, name, recap in [
                (needs_him.locator(".item-head"), "orchestrator", ORCHESTRATOR_RECAP),
                (chief_card.locator(".item-head"), "chief", "Nothing in this list right now."),
            ]:
                target.hover()
                expect(panel).to_have_attribute("aria-hidden", "false")
                expect(panel.locator("[data-recap-text]")).to_have_text(recap)
                expect(panel.locator("[data-bucket-key],.group-label,.child-title")).to_have_count(
                    0
                )
                expect(panel).not_to_contain_text(LONG_TITLE)
                page.wait_for_function(
                    "getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity === '1'"
                )
                assert panel.bounding_box()["x"] >= 0
                assert (
                    panel.bounding_box()["x"] + panel.bounding_box()["width"] <= viewport["width"]
                )
                page.screenshot(path=str(artifact_dir / f"sidebar-{name}-{label}-popup.png"))
        page.mouse.move(1200, 900)
        expect(panel).to_have_attribute("aria-hidden", "true")
        page.set_viewport_size(dict(width=1440, height=1000))
        left = page.locator(".board-workspace-left")
        grip = page.get_by_role("separator", name="Resize workspace panel")
        page.wait_for_function(
            "Math.abs(Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))-document.querySelector('.board-workspace-left').getBoundingClientRect().width)<2"
        )
        selected_row = needs_him.locator('[data-ticket-id="t_approval"]')
        selected_row.click()
        expect(selected_row).to_have_attribute("aria-current", "page")
        selection_hash = page.evaluate("location.hash")
        grip.focus()
        expect(grip).to_be_focused()
        original = left.evaluate("e=>e.getBoundingClientRect().width")
        grip.press("ArrowRight")
        grown = left.evaluate("e=>e.getBoundingClientRect().width")
        assert grown > original
        page.reload()
        expect(grip).to_be_visible()
        assert abs(left.evaluate("e=>e.getBoundingClientRect().width") - grown) < 2
        assert page.evaluate("location.hash") == selection_hash
        expect(selected_row).to_have_attribute("aria-current", "page")
        bounds = grip.bounding_box()
        page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + 50)
        page.mouse.down()
        page.mouse.move(bounds["x"] + 65, bounds["y"] + 50, steps=4)
        page.mouse.up()
        assert left.evaluate("e=>e.getBoundingClientRect().width") > grown
        grip.dblclick()
        assert abs(left.evaluate("e=>e.getBoundingClientRect().width") - original) < 2
        row = needs_him.locator('[data-ticket-id="t_approval"]')
        row.focus()
        expect(row).to_be_focused()
        row.hover()
        expect(panel).to_contain_text(LONG_TITLE)
        expect(panel).not_to_contain_text("Stages")
        expect(panel).to_contain_text("Awaiting your approval at implementation.")
        needs_him.locator(".item-head").hover()
        expect(panel.locator("[data-recap-text]")).to_have_text(ORCHESTRATOR_RECAP)
        expect(panel).not_to_contain_text("Quiet ordinary Worker")
        expect(panel).not_to_contain_text("Blocked ordinary Worker")
        expect(panel).not_to_contain_text("Stages")
        expect(panel).to_have_count(1)
        page.evaluate("location.hash = '#/workspace'")
        expect(selected_row).not_to_have_attribute("aria-current", "page")
        page.set_viewport_size(dict(width=820, height=1000))
        row.focus()
        expect(row).to_be_focused()
        expect(row).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size(dict(width=1440, height=1000))
        worker = board["columns"][0]["cards"][0]
        supervisor = board["sprint_items"][0]
        for target, facts in [
            (row, worker),
            (needs_him.locator(".item-head"), supervisor),
            (chief_card.locator(".item-head"), chief),
        ]:
            target.hover()
            expect(panel).to_have_attribute("aria-hidden", "false")
            identity = panel.element_handle()
            source_bounds = target.bounding_box()
            for signal, winner in [
                (dict(working=True, message_for_owner=False), "working"),
                (dict(working=True, message_for_owner=True), "message"),
                (dict(working=True, message_for_owner=False), "working"),
                (dict(working=False, message_for_owner=False), None),
            ]:
                facts["conversation_signal"] = signal
                page.evaluate("window.__refresh()")
                title = panel.locator(".title")
                expect(title.locator("[data-conversation-signal]")).to_have_count(
                    0 if winner is None else 1
                )
                if winner:
                    expect(title.locator("[data-conversation-signal]")).to_have_attribute(
                        "data-conversation-signal", winner
                    )
                expect(target.locator("[data-conversation-signal]")).to_have_count(
                    0 if winner is None else 1
                )
                if winner:
                    expect(target.locator("[data-conversation-signal]")).to_have_attribute(
                        "data-conversation-signal", winner
                    )
                assert all(

                        abs(target.bounding_box()[key] - source_bounds[key]) < 1
                        for key in ["x", "y", "width", "height"]

                )
                assert identity.evaluate("e=>e===document.querySelector('[data-rail-hover-panel]')")
                expect(panel).to_have_attribute("aria-hidden", "false")
        page.mouse.move(1200, 950)
        page.locator("body").click(position=dict(x=1100, y=920))
        row.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(row).to_be_focused()
        expect(panel).to_have_attribute("aria-hidden", "false")
        expect(panel).to_contain_text("A concise recap from the current projection.")
        expect(panel).to_contain_text("Awaiting your approval at implementation.")
        expect(panel).not_to_contain_text("Blocked by")
        page.mouse.move(1200, 950)
        row.click()
        page.mouse.move(1200, 950)
        expect(panel).to_have_attribute("aria-hidden", "true")
        page.mouse.move(1200, 950)
        expect(panel).to_have_attribute("aria-hidden", "true")
        preview_metrics = []
        for cached, trigger in [(False, "pointer"), (True, "pointer"), (False, "focus")]:
            before_requests = len(preview_requests)
            page.evaluate(
                "() => {\n              const root=document.querySelector('[data-rail-hover-panel]');\n              window.__previewTiming={start:performance.now(),visible:null,recap:null,firstText:null};\n              window.__previewObserver?.disconnect();\n              window.__previewObserver=new MutationObserver(() => {\n                const t=window.__previewTiming;\n                if(root.getAttribute('aria-hidden')==='false' && t.visible===null) {\n                  t.visible=performance.now()-t.start;t.firstText=root.querySelector('[data-recap-text]')?.textContent ?? '';\n                }\n                if(t.visible!==null && root.querySelector('[data-recap-text]')?.textContent==='A concise recap from the current projection.' && t.recap===null) t.recap=performance.now()-t.start;\n              });\n              window.__previewObserver.observe(root,{subtree:true,childList:true,characterData:true,attributes:true});\n            }"
            )
            if trigger == "pointer":
                row.hover()
            else:
                page.locator("body").click(position=dict(x=1100, y=920))
                row.focus()
                page.keyboard.press("Tab")
                page.keyboard.press("Shift+Tab")
            expect(panel).to_have_attribute("aria-hidden", "false")
            expect(panel.locator("[data-recap-text]")).to_have_text(
                "A concise recap from the current projection."
            )
            page.wait_for_function("window.__previewTiming.recap !== null")
            measurement = dict(
                cached=cached,
                trigger=trigger,
                requests=len(preview_requests) - before_requests,
                **page.evaluate("window.__previewTiming"),
            )
            assert measurement["requests"] == 0
            assert measurement["firstText"] == "A concise recap from the current projection."
            preview_metrics.append(measurement)
            page.mouse.move(1200, 950)
            page.locator("body").click(position=dict(x=1100, y=920))
            expect(panel).to_have_attribute("aria-hidden", "true")
        page.evaluate("window.__previewObserver.disconnect()")
        metric_path = (
            pathlib.Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"]) / "inline-preview-timing.json"
        )
        metric_path.parent.mkdir(parents=True, exist_ok=True)
        metric_path.write_text(json.dumps(preview_metrics, indent=2))
        utility_cases = [
            ("Usage and Backends", "usage", "backends"),
            ("Settings", "settings", "settings"),
            ("Ideas", "ideas", "ideas"),
            ("Feedback", "feedback", "feedback"),
        ]
        for label, utility, screen in utility_cases:
            link = page.locator('.rail-utilities a[aria-label="' + label + '"]')
            link.click()
            pane = page.locator(
                '.board-workspace-right [data-screen="'
                + screen
                + '"], .board-workspace-right [data-settings]'
            )
            expect(pane).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(link).to_have_attribute("aria-current", "page")
            expect(page.locator('.rail-utilities a[aria-current="page"]')).to_have_count(1)
            accent_border = page.evaluate(
                "() => {\n                const probe = document.createElement('span');\n                probe.style.borderColor = 'var(--accent-bright)';\n                document.body.append(probe);\n                const color = getComputedStyle(probe).borderTopColor;\n                probe.remove();\n                return color;\n            }"
            )
            assert (
                link.evaluate("element => getComputedStyle(element).borderTopColor")
                == accent_border
            )
            link.hover()
            assert (
                link.evaluate("element => getComputedStyle(element).borderTopColor")
                == accent_border
            )
            page.mouse.move(1200, 900)
            assert (
                link.evaluate("element => getComputedStyle(element).borderTopColor")
                == accent_border
            )
            assert page.evaluate("location.hash") == "#/workspace/utility/" + utility
            copied_address = page.url
            page.reload()
            pane = page.locator(
                '.board-workspace-right [data-screen="'
                + screen
                + '"], .board-workspace-right [data-settings]'
            )
            expect(pane).to_be_visible()
            expect(page.locator('.rail-utilities a[aria-label="' + label + '"]')).to_have_attribute(
                "aria-current", "page"
            )
            shared_context = browser.new_context(viewport={"width": 1280, "height": 900})
            shared_context.route("**/api/**", respond)
            shared_page = shared_context.new_page()
            shared_page.goto(copied_address)
            expect(
                shared_page.locator(
                    '.board-workspace-right [data-screen="'
                    + screen
                    + '"], .board-workspace-right [data-settings]'
                )
            ).to_be_visible()
            expect(
                shared_page.locator(
                    '.rail-utilities a[aria-label="' + label + '"][aria-current="page"]'
                )
            ).to_be_visible()
            shared_context.close()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size(dict(width=820, height=1000))
        expect(page.locator('.board-workspace-right [data-screen="feedback"]')).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size(dict(width=1440, height=1000))
        ordinary = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_parent_quiet")
        needs_him.locator('[data-ticket-id="t_parent_quiet"]').hover()
        expect(panel).to_have_attribute("aria-hidden", "false")
        frozen_row = needs_him.locator('[data-ticket-id="t_parent_quiet"]')
        frozen_bounds = frozen_row.bounding_box()
        ordinary["awaiting_answer"] = True
        ordinary["conversation_id"] = "rebound-quiet-worker"
        ordinary["conversation_signal"] = dict(working=True, message_for_owner=False)
        page.evaluate("window.__refresh()")
        page.evaluate("window.__startRailWait()")
        assert page.evaluate("window.__railWaitStatus") == "pending"
        expect(
            frozen_row.locator(
                '[data-conversation-id="rebound-quiet-worker"] [data-conversation-signal="working"]'
            )
        ).to_have_count(1)
        assert all(

                abs(frozen_row.bounding_box()[key] - frozen_bounds[key]) < 1
                for key in ["x", "y", "width", "height"]

        )
        expect(
            needs_him.locator('[data-bucket-key="empty"] [data-ticket-id="t_parent_quiet"]')
        ).to_have_count(1)
        expect(
            needs_him.locator(
                '[data-bucket-key="awaiting_answer"] [data-ticket-id="t_parent_quiet"]'
            )
        ).to_have_count(0)
        page.mouse.move(1200, 950)
        expect(
            needs_him.locator(
                '[data-bucket-key="awaiting_answer"] [data-ticket-id="t_parent_quiet"]'
            )
        ).to_have_count(1)
        page.wait_for_function("window.__railWaitStatus === 'included'")
        expect(
            needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').last
        ).to_have_attribute("data-ticket-id", "t_parent_quiet")
        ordinary["awaiting_answer"] = False
        ordinary["conversation_signal"] = dict(working=False, message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(
            needs_him.locator('[data-bucket-key="empty"] [data-ticket-id="t_parent_quiet"]')
        ).to_have_count(1)
        ordinary["awaiting_answer"] = True
        page.evaluate("window.__refresh()")
        expect(
            needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').last
        ).to_have_attribute("data-ticket-id", "t_parent_quiet")
        ordinary["activity_at"] = 9999
        page.evaluate("window.__refresh()")
        expect(
            needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').first
        ).to_have_attribute("data-ticket-id", "t_answer")
        artifact_dir = pathlib.Path(
            os.environ.get(
                "PANELS_SIDEBAR_ARTIFACT_DIR",
                "/home/vps/Deployments/Panels/current/data/files/tickets/t_21sxtdb3/artifacts/revision",
            )
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for width in [232, 520]:
            grip.focus()
            for _ in range(15):
                grip.press("Shift+ArrowLeft" if width == 232 else "Shift+ArrowRight")
            assert abs(left.bounding_box()["width"] - width) < 2
            clipped = row.locator(".ticket-row-title")
            assert clipped.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
            assert clipped.evaluate("e => e.scrollWidth > e.clientWidth")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            row.hover()
            expect(panel).to_contain_text(LONG_TITLE)
            page.wait_for_function(
                "getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity === '1'"
            )
            page.screenshot(path=str(artifact_dir / f"sidebar-{width}-popup.png"))
        page.set_viewport_size(dict(width=1440, height=1400))
        page.mouse.move(1200, 950)
        page.locator("body").click(position=dict(x=1100, y=920))
        mover = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_agent")
        source = board["sprint_items"][0]
        destination = board["sprint_items"][1]
        mover_row = page.locator('[data-ticket-id="t_agent"]').first
        mover_row.scroll_into_view_if_needed()
        page.mouse.move(1200, 950)
        page.evaluate("location.hash='#/workspace/item/si_quiet'")
        page.wait_for_timeout(100)
        page.wait_for_function(
            "() => {\n          const card=document.querySelector('.orchestrator-card.selected'),css=getComputedStyle(card);\n          const color=document.createElement('span').style;color.color=css.getPropertyValue('--accent-bright');\n          return css.borderTopColor===color.color;\n        }"
        )
        selected_card = page.locator('[data-sprint-item="si_quiet"]')
        selected_before = selected_card.evaluate(
            "e=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopColor})"
        )
        page.screenshot(path=str(artifact_dir / "sidebar-motion-before.png"))
        before_y = mover_row.bounding_box()["y"]
        source["tickets"] = [r for r in source["tickets"] if r["id"] != "t_agent"]
        destination["tickets"].append(mover)
        mover["sprint_item_id"] = destination["id"]
        mover["sprint_item_title"] = destination["title"]
        mover["assigned"] = True
        page.evaluate(
            "() => {\n          window.__motionEvidence=[];\n          const start=performance.now();\n          const sample=()=>{\n            const row=document.querySelector('[data-ticket-id=\"t_agent\"]');\n            const mark=row?.querySelector('.stage-mark');\n            const css=row&&getComputedStyle(row);\n            const rect=row?.getBoundingClientRect();\n            const animations=row?.getAnimations().map(a=>({duration:a.effect.getTiming().duration,easing:a.effect.getTiming().easing}));\n            const overlaps=rect&&[...document.querySelectorAll('.worker-row')].some(other=>{const r=other.getBoundingClientRect();return other!==row&&r.top<rect.bottom&&r.bottom>rect.top&&r.left<rect.right&&r.right>rect.left;});\n            const top=rect&&document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)?.closest('[data-ticket-id]')?.getAttribute('data-ticket-id');\n            window.__motionEvidence.push({ms:performance.now()-start,y:row?.getBoundingClientRect().y,\n              color:css?.color,background:css?.backgroundColor,z:css?.zIndex,\n              cardBackground:row&&getComputedStyle(row.closest(\".orchestrator-card\")).backgroundColor,\n              selectedBorder:getComputedStyle(document.querySelector('.orchestrator-card.selected')).borderTopColor,\n              cardZ:row&&getComputedStyle(row.closest('.orchestrator-card')).zIndex,\n              selectedTravellingCard:row?.closest('.orchestrator-card')?.matches('.selected.rail-travelling-card'),\n              spinner:mark&&getComputedStyle(mark).borderTopColor,\n              animations,overlaps,top,travelling:row?.classList.contains('rail-travelling'),\n              parent:row?.closest('[data-sprint-item]')?.getAttribute('data-sprint-item')});\n            if(performance.now()-start<1100)requestAnimationFrame(sample);\n          };\n          requestAnimationFrame(sample); void window.__refresh();\n        }"
        )
        page.wait_for_timeout(350)
        page.screenshot(path=str(artifact_dir / "sidebar-motion-mid.png"))
        page.wait_for_timeout(1000)
        samples = page.evaluate("window.__motionEvidence")
        (artifact_dir / "sidebar-motion-samples.json").write_text(json.dumps(samples, indent=2))
        moving = [x for x in samples if x.get("travelling")]
        selected_after = selected_card.evaluate(
            "e=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopColor})"
        )
        (artifact_dir / "selected-card-endpoints.json").write_text(
            json.dumps(dict(before=selected_before, after=selected_after), indent=2)
        )
        page.screenshot(path=str(artifact_dir / "sidebar-motion-settled.png"))
        assert moving, "No actual cross-parent travelling frames"
        after_y = mover_row.bounding_box()["y"]
        assert abs(after_y - before_y) > 10
        assert any(
            min(before_y, after_y) + 1 < x["y"] < max(before_y, after_y) - 1 for x in moving
        )
        assert all(x["background"].startswith("rgb(") for x in moving), (
            "Traveller surface must be immediately opaque"
        )
        assert selected_before["border"] == selected_after["border"]
        assert all(x["selectedBorder"] == selected_before["border"] for x in moving)
        assert all(x["selectedTravellingCard"] and int(x["cardZ"]) > 0 for x in moving)
        assert all(int(x["z"]) > 0 for x in moving)
        crossing = [x for x in moving if x.get("overlaps")]
        assert crossing, "No measured crossing frames"
        assert all(x.get("top") == "t_agent" for x in crossing), crossing
        page.screenshot(path=str(artifact_dir / "sidebar-motion-settled.png"))
        assert page.locator('[style*="--rail-motion-"]').count() == 0

        def surface_motion(name, selection, mutate, cancel=False):
            page.evaluate("hash=>location.hash=hash", selection)
            page.wait_for_timeout(100)
            page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
            page.wait_for_function(
                "() => {\n              const card=document.querySelector('.orchestrator-card.selected');\n              if(!card)return true;\n              const css=getComputedStyle(card),color=document.createElement('span').style;\n              color.color=css.getPropertyValue('--accent-bright');\n              return css.borderTopColor===color.color && css.backgroundColor==='rgba(0, 0, 0, 0)';\n            }"
            )
            mutate()
            page.evaluate(
                "() => {\n              const effective=node=>{\n                while(node) {const color=getComputedStyle(node).backgroundColor;\n                  if(color!=='rgba(0, 0, 0, 0)') return color; node=node.parentElement;}\n              };\n              window.__surfaceExpected=Object.fromEntries([...document.querySelectorAll('.orchestrator-card')].map(card=>\n                [card.dataset.railMotion,{own:effective(card),edge:effective(card.parentElement),border:getComputedStyle(card).borderTopColor}]));\n              const active=document.querySelector('.worker-row.ticket-row--active');\n              window.__activeFill=active&&effective(active);\n              window.__surfaceEvidence=[];\n              const start=performance.now();\n              const sample=()=>{\n                const visibility=node=>{\n                  let opacity=1,fading=false;\n                  for(let ancestor=node;ancestor;ancestor=ancestor.parentElement){\n                    opacity*=Number(getComputedStyle(ancestor).opacity);\n                    fading ||= ancestor.getAnimations().some(a=>a.effect.getKeyframes().some(f=>f.opacity!==undefined));\n                  }\n                  return {opacity,fading};\n                };\n                const nodes=[...document.querySelectorAll('.orchestrator-card.rail-moving,.bucket-label.rail-moving,.worker-row.rail-moving')];\n                window.__surfaceEvidence.push({ms:performance.now()-start,nodes:nodes.map(node=>{\n                  const css=getComputedStyle(node), card=node.closest('.orchestrator-card');\n                  const resting=window.__surfaceExpected[card.dataset.railMotion];\n                  const active=node.classList.contains('ticket-row--active');\n                  const visible=visibility(node);\n                  return {id:node.dataset.railMotion,background:css.backgroundColor,edge:css.outlineColor,\n                    expected:active?window.__activeFill:resting.own,expectedEdge:node===card?resting.edge:resting.own,\n                    border:css.borderTopColor,expectedBorder:resting.border,selected:node===card&&card.classList.contains(\"selected\"),\n                    opacity:css.opacity,effectiveOpacity:visible.opacity,fading:visible.fading,active,width:css.outlineWidth};\n                })});\n                if(performance.now()-start<850)requestAnimationFrame(sample);\n              }; requestAnimationFrame(sample); void window.__refresh();\n            }"
            )
            page.wait_for_function(
                "document.querySelector('.orchestrator-card.rail-moving,.worker-row.rail-moving')"
            )
            page.wait_for_timeout(80)
            if name == "selected-card-reorder":
                paint = page.evaluate(
                    "() => {\n                  const card=document.querySelector('.orchestrator-card.selected');\n                  const r=card.getBoundingClientRect();\n                  return {cardRect:r.toJSON(),clipRect:card.parentElement.getBoundingClientRect().toJSON(),\n                    borderHit:document.elementFromPoint(r.right-1,r.top+r.height/2)?.closest('[data-rail-motion]')?.getAttribute('data-rail-motion')};\n                }"
                )
                assert paint["borderHit"] == "card:si_quiet", paint
                card_rect, clip_rect = (paint["cardRect"], paint["clipRect"])
                assert (
                    clip_rect["left"]
                    <= card_rect["left"]
                    < card_rect["right"]
                    <= clip_rect["right"]
                ), paint
                assert min(card_rect["bottom"], clip_rect["bottom"]) > max(
                    card_rect["top"], clip_rect["top"]
                ), paint
                page.screenshot(path=str(artifact_dir / "selected-card-motion.png"))
            page.screenshot(path=str(artifact_dir / f"{name}-mid.png"))
            if cancel:
                page.emulate_media(reduced_motion="reduce")
            page.wait_for_timeout(900)
            values = page.evaluate("window.__surfaceEvidence")
            (artifact_dir / f"{name}-samples.json").write_text(json.dumps(values, indent=2))
            nodes = [node for sample in values for node in sample["nodes"]]
            assert nodes
            for node in nodes:
                assert node["background"].startswith("rgb("), node
                if node["selected"]:
                    assert node["border"] == node["expectedBorder"], node
                if not node["fading"]:
                    assert float(node["effectiveOpacity"]) == 1, node
            assert (
                page.locator(
                    '.rail-moving,.rail-travelling,.rail-travelling-card,[style*="--rail-motion-"]'
                ).count()
                == 0
            )
            assert page.locator("[data-rail-motion-busy]").count() == 0
            surviving = page.evaluate(
                "ids => ids.filter(id=>id.startsWith('card:')||id.startsWith('row:')).map(id=>{\n              const node=document.querySelector(`[data-rail-motion=\"${CSS.escape(id)}\"]`);\n              if(!node)return {id,missing:true};\n              const r=node.getBoundingClientRect();\n              let opacity=1,left=0,top=0,right=innerWidth,bottom=innerHeight;\n              for(let ancestor=node;ancestor;ancestor=ancestor.parentElement){\n                const css=getComputedStyle(ancestor);opacity*=Number(css.opacity);\n                if(ancestor!==node&&[css.overflowX,css.overflowY].some(value=>value!=='visible')){\n                  const clip=ancestor.getBoundingClientRect();\n                  left=Math.max(left,clip.left);top=Math.max(top,clip.top);\n                  right=Math.min(right,clip.right);bottom=Math.min(bottom,clip.bottom);\n                }\n              }\n              const x=(Math.max(r.left,left)+Math.min(r.right,right))/2;\n              const y=(Math.max(r.top,top)+Math.min(r.bottom,bottom))/2;\n              const hit=document.elementFromPoint(x,y)?.closest(id.startsWith('card:')?'.orchestrator-card':'.worker-row');\n              return {id,opacity,width:r.width,height:r.height,visibleWidth:Math.min(r.right,right)-Math.max(r.left,left),\n                visibleHeight:Math.min(r.bottom,bottom)-Math.max(r.top,top),hit:hit?.dataset.railMotion};\n            })",
                list({node["id"] for node in nodes}),
            )
            assert surviving
            for survivor in surviving:
                assert not survivor.get("missing"), survivor
                assert survivor["opacity"] == 1, survivor
                assert survivor["width"] > 0 and survivor["height"] > 0, survivor
                assert survivor["visibleWidth"] >= survivor["width"] - 1, survivor
                assert survivor["visibleHeight"] >= survivor["height"] - 1, survivor
                assert survivor["hit"] == survivor["id"], survivor
            if cancel:
                page.emulate_media(reduced_motion="no-preference")
            return nodes

        reordered = surface_motion(
            "selected-card-reorder",
            "#/workspace/item/si_quiet",
            lambda: destination.update(created_at=-1),
        )
        assert any(n["id"] == "card:si_quiet" for n in reordered)
        assert any(n["id"] == "card:si_needs_him" for n in reordered)
        active = surface_motion(
            "active-row-group-move",
            "#/workspace/item/si_quiet/t_agent",
            lambda: mover.update(assigned=False),
        )
        assert any(n["active"] for n in active)
        assert any(n["id"].startswith("heading:") for n in active)
        surface_motion(
            "selected-card-return",
            "#/workspace/item/si_quiet",
            lambda: destination.update(created_at=0),
        )
        surface_motion(
            "cancelled-group-move",
            "#/workspace/item/si_quiet",
            lambda: mover.update(assigned=True),
            cancel=True,
        )
        page.evaluate("location.hash='#/workspace'")

        def sample_phase(name):
            page.evaluate(
                "() => {\n              window.__phaseEvidence=[]; const start=performance.now();\n              const sample=()=>{\n                const row=document.querySelector('[data-ticket-id=\"t_agent\"]');\n                const card=document.querySelector('[data-sprint-item=\"si_quiet\"]');\n                const heading=card.querySelector('.bucket-label');\n                window.__phaseEvidence.push({ms:performance.now()-start,\n                  opacity:row&&Number(getComputedStyle(row).opacity),height:card.getBoundingClientRect().height,\n                  heading:heading&&Number(getComputedStyle(heading).opacity)});\n                if(performance.now()-start<900)requestAnimationFrame(sample);\n              };requestAnimationFrame(sample);void window.__refresh();\n            }"
            )
            page.wait_for_timeout(85 if name == "departure" else 220)
            page.screenshot(path=str(artifact_dir / f"sidebar-{name}-mid.png"))
            page.wait_for_timeout(950)
            values = page.evaluate("window.__phaseEvidence")
            (artifact_dir / f"sidebar-{name}-samples.json").write_text(json.dumps(values, indent=2))
            return values

        mover["blocked"] = True
        departure = sample_phase("departure")
        fading = [x for x in departure if x["opacity"] is not None and 0 < x["opacity"] < 1]
        assert fading
        assert max(x["height"] for x in fading) - min(x["height"] for x in fading) < 1
        assert departure[-1]["opacity"] is None
        assert departure[-1]["height"] < fading[0]["height"]
        mover["blocked"] = False
        arrival = sample_phase("arrival")
        entered = [x for x in arrival if x["opacity"] is not None]
        assert any(x["opacity"] == 0 for x in entered)
        assert any(0 < x["opacity"] < 1 for x in entered)
        assert all(abs(x["height"] - entered[-1]["height"]) < 1 for x in entered)
        assert any(x["heading"] is not None and 0 < x["heading"] < 1 for x in arrival)
        page.emulate_media(reduced_motion="reduce")
        spinner = page.locator('[data-ticket-id="t_agent"] [data-conversation-signal="working"]')
        expect(spinner).to_have_count(1)
        assert spinner.evaluate("e => getComputedStyle(e).animationName") == "none"
        mover["assigned"] = False
        page.evaluate("window.__refresh()")
        expect(
            page.locator(
                '[data-sprint-item="si_quiet"] [data-bucket-key="agent"] [data-ticket-id="t_agent"]'
            )
        ).to_have_count(1)
        assert (
            page.locator(".approved-rail").evaluate("e=>e.getAnimations({subtree:true}).length")
            == 0
        )
        mover["assigned"] = True
        page.evaluate("window.__refresh()")
        expect(
            page.locator(
                '[data-sprint-item="si_quiet"] [data-bucket-key="assigned"] [data-ticket-id="t_agent"]'
            )
        ).to_have_count(1)
        assert (
            page.locator(".approved-rail").evaluate("e=>e.getAnimations({subtree:true}).length")
            == 0
        )
        page.mouse.move(1200, 950)
        page.emulate_media(reduced_motion="no-preference")
        phone = browser.new_context(
            viewport=dict(width=560, height=900), is_mobile=True, has_touch=True
        )
        phone_page = phone.new_page()
        phone_page.route("**/api/**", respond)
        phone_page.goto([__file__, *urls][1])
        assert phone_page.evaluate("innerWidth") == 560
        phone_row = phone_page.locator('[data-ticket-id="t_approval"]').first
        phone_panel = phone_page.locator("[data-rail-hover-panel]")
        phone_row.dispatch_event("pointerover", dict(pointerType="touch", bubbles=True))
        phone_page.wait_for_timeout(250)
        expect(phone_panel).to_have_attribute("aria-hidden", "true")
        cdp = phone.new_cdp_session(phone_page)
        bounds = phone_row.bounding_box()
        point = dict(x=bounds["x"] + 20, y=bounds["y"] + bounds["height"] / 2)

        def touch(kind, points):
            cdp.send("Input.dispatchTouchEvent", dict(type=kind, touchPoints=points))

        touch("touchStart", [point])
        phone_page.wait_for_timeout(180)
        touch("touchMove", [dict(x=point["x"] + 40, y=point["y"])])
        phone_page.wait_for_timeout(300)
        expect(phone_panel).to_have_attribute("aria-hidden", "true")
        touch("touchEnd", [])
        touch("touchStart", [point])
        phone_page.wait_for_timeout(440)
        expect(phone_panel).to_have_attribute("aria-hidden", "false")
        phone_page.screenshot(path=str(artifact_dir / "sidebar-phone-hold.png"))
        touch("touchEnd", [])
        phone_page.get_by_role("button", name="Settled (1)").click()
        for target in [
            phone_page.locator(".settled-title"),
            phone_page.locator('[data-message-conversation="ordinary-settled"]'),
        ]:
            phone_row.dispatch_event("pointerleave", dict(pointerType="touch"))
            target.scroll_into_view_if_needed()
            target.dispatch_event("pointerover", dict(pointerType="touch", bubbles=True))
            phone_page.wait_for_timeout(250)
            expect(phone_panel).to_have_attribute("aria-hidden", "true")
            bounds = target.bounding_box()
            point = dict(x=bounds["x"] + 20, y=bounds["y"] + bounds["height"] / 2)
            touch("touchStart", [point])
            phone_page.wait_for_timeout(440)
            expect(phone_panel).to_have_attribute("aria-hidden", "false")
            touch("touchEnd", [])
        phone_page.evaluate("location.hash='#/workspace/item/si_needs_him'")
        item_pane = phone_page.locator('[data-sprint-item-workspace="si_needs_him"]')
        blocked_group = item_pane.locator('[data-workspace-group="blocked"]')
        if not blocked_group.evaluate("e=>e.open"):
            blocked_group.locator("summary").click()
        blocked_child = item_pane.locator('[data-sprint-ticket-id="t_parent_blocked"]')
        blocked_child.scroll_into_view_if_needed()
        child_panel = phone_page.locator("[data-rail-hover-panel]").first
        blocked_child.dispatch_event("pointerover", dict(pointerType="touch", bubbles=True))
        phone_page.wait_for_timeout(250)
        expect(child_panel).to_have_attribute("aria-hidden", "true")
        bounds = blocked_child.bounding_box()
        point = dict(x=bounds["x"] + 20, y=bounds["y"] + bounds["height"] / 2)
        touch("touchStart", [point])
        phone_page.wait_for_timeout(440)
        expect(child_panel).to_have_attribute("aria-hidden", "false")
        expect(child_panel.locator(".kicker")).to_contain_text("Needs him")
        expect(child_panel).to_contain_text("Blocked at implementation.")
        expect(child_panel).to_contain_text("A concise recap from the current projection.")
        expect(child_panel).not_to_contain_text("Stages")
        expect(child_panel).not_to_contain_text("Blocked by")
        phone_page.wait_for_function(
            "getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity==='1'"
        )
        phone_page.screenshot(path=str(artifact_dir / "sidebar-item-phone-popup.png"))
        touch("touchEnd", [])
        assert phone_page.evaluate("location.hash") == "#/workspace/item/si_needs_him"
        phone.close()
        page.evaluate("location.hash = '#/workspace'")
        page.set_viewport_size(dict(width=560, height=1000))
        for selector, pane in [
            ('[data-ticket-id="t_approval"]', '[data-screen="ticket"]'),
            (
                '[data-sprint-item="si_needs_him"] .item-head',
                '[data-sprint-item-workspace="si_needs_him"]',
            ),
            ("[data-chief-destination]", ".chief-workspace"),
        ]:
            target = page.locator(selector).first
            target.focus()
            expect(target).to_be_focused()
            target.click()
            expect(page.locator(pane)).to_be_visible()
            expect(left).not_to_be_visible()
            page.go_back()
            expect(left).to_be_visible()
            target.focus()
            expect(target).to_be_focused()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size(dict(width=1440, height=1000))
        page.evaluate("location.hash='#/workspace/item/si_needs_him'")
        blocked_group = page.locator(
            '[data-sprint-item-workspace="si_needs_him"] [data-workspace-group="blocked"]'
        )
        if not blocked_group.evaluate("e=>e.open"):
            blocked_group.locator("summary").click()
        blocked_child = page.locator(
            '[data-sprint-item-workspace="si_needs_him"] [data-sprint-ticket-id="t_parent_blocked"]'
        )
        blocked_child.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(blocked_child).to_be_focused()
        child_panel = page.locator("[data-rail-hover-panel]").first
        expect(child_panel).to_have_attribute("aria-hidden", "false")
        expect(child_panel.locator(".kicker")).to_contain_text("Needs him")
        expect(child_panel).to_contain_text("Blocked at implementation.")
        expect(child_panel).not_to_contain_text("Stages")
        assert page.locator(".board-workspace-item-line").count() == 0
        assert errors == [], errors
    pass
