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
    import re

    from playwright.sync_api import expect

    def card(ticket_id, item_id, item_title, **values):
        row = {
            "id": ticket_id,
            "title": ticket_id,
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

    old = card("t_done", "si_old", "Old Item", stage="done", is_done=True)
    peer = card("t_peer", "si_peer", "Peer Item")
    newer = card("t_newer", "si_newer", "Newer Item")
    board = {
        "columns": [{"stage": "needs_implementation", "cards": [newer, peer, old]}],
        "sprint_items": [
            {**item("si_old"), "created_at": 1},
            {**item("si_peer"), "created_at": 2},
            {**item("si_newer"), "created_at": 3},
        ],
    }
    children = [
        old,
        card("t_older", "si_old", "Old Item", assigned=True, activity_at=10),
        card("t_recent", "si_old", "Old Item", assigned=True, activity_at=20),
    ]

    def workspace(item_id):
        title = {
            "si_old": "Old Item",
            "si_peer": "Peer Item",
            "si_newer": "Newer Item",
            "si_direct": "Direct Item",
        }[item_id]
        rows = children if item_id == "si_old" else [card("t_" + item_id, item_id, title)]
        return {
            **item(item_id),
            "title": title,
            "body": "",
            "project_id": None,
            "project": None,
            "created_at": {"si_old": 1, "si_peer": 2, "si_newer": 3, "si_direct": 4}[item_id],
            "updated_at": 1,
            "committed_sprints": [],
            "planning_day_id": "day_test",
            "today_ticket_ids": ["t_done"] if item_id == "si_old" else [],
            "tickets": rows,
            "artifacts": [],
            "conversation_history": [],
            "supervisor": {
                "agent_key": "sprint_item:" + item_id,
                "conversation_id": None,
                "launch_configuration": {
                    "employee_backend": "codex",
                    "employee_launch_model": None,
                    "employee_launch_reasoning_effort": None,
                },
            },
        }

    for summary in board["sprint_items"]:
        detail = workspace(summary["id"])
        summary.update(title=detail["title"], tickets=detail["tickets"])
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
    held = []
    delay_old = True
    snapshot_failure = True
    conversation_reads = []
    chief_sends = []
    chief_resets = []

    def fulfill(route, result):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    def respond(route):
        path = route.request.url.split("/api/", 1)[1].split("?")[0]
        if path == "navigation/snapshot":
            if snapshot_failure:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "code": "unavailable",
                                "message": "Workspace unavailable",
                                "detail": None,
                            }
                        }
                    ),
                )
                return
            result = navigation_response(board, workers)
        elif path == "chief/conversation":
            raise AssertionError("competing Chief pointer query remains")
        elif path == "chief/conversation/send":
            chief_sends.append(route.request.post_data_json)
            result = {"conversation_id": "chief-delivered", "fate": "started"}
        elif path == "chief/conversation/reset":
            chief_resets.append(True)
            result = {}
        elif path.startswith("conversation/conversations/"):
            identity = path.split("/")[2]
            if path.endswith("/events"):
                result = {"events": []}
            else:
                conversation_reads.append(identity)
                result = dict(
                    conversation_id=identity,
                    backend_key="codex",
                    supports_steer=True,
                    model="fixture",
                    reasoning_effort=None,
                    workspace_folder="/tmp",
                    access="full",
                    role_text=None,
                    identity_environment_variable_names=[],
                    latest_sequence=0,
                    owner_read_through_sequence=0,
                    is_running=False,
                    held_prompts=[],
                    pending_permission_ask=None,
                    pending_user_input=None,
                    composer_catalog=[],
                )
        elif path == "workers/chief-of-staff/settings":
            result = workers["chief_of_staff"]
        elif path == "board" or path == "workers":
            raise AssertionError("summary query remains active: " + path)
        elif path.endswith("/recap-preview"):
            raise AssertionError("unexpected preview request: " + path)
        elif path.startswith("items/") and path.endswith("/workspace-content"):
            item_id = path.split("/")[1]
            if item_id == "si_old" and delay_old:
                held.append(route)
                return
            result = {
                key: workspace(item_id)[key]
                for key in ["body", "artifacts", "conversation_history"]
            }
        elif path == "tickets":
            route.fulfill(
                status=404, content_type="application/json", body='{"detail":"Ticket not found"}'
            )
            return
        elif path == "worker-types":
            result = {"worker_types": []}
        elif path == "conversation/backends":
            result = {"backends": []}
        elif path.endswith("start-values"):
            result = {"backends": [], "current": None}
        else:
            result = {}
        fulfill(route, result)

    def order(page, expected):
        expect(page.locator(".board-workspace-left [data-sprint-item]")).to_have_count(
            len(expected)
        )
        try:
            page.wait_for_function(
                "ids => JSON.stringify([...document.querySelectorAll('.board-workspace-left [data-sprint-item]')].map(n => n.dataset.sprintItem)) === JSON.stringify(ids)",
                arg=expected,
                timeout=5000,
            )
        except Exception:
            actual = page.locator(".board-workspace-left [data-sprint-item]").evaluate_all(
                "nodes => nodes.map(n => n.dataset.sprintItem)"
            )
            raise AssertionError(
                f"Item order changed: expected {expected}, actual {actual}"
            ) from None

    def title(page, item_id):
        return page.locator('[data-sprint-item="' + item_id + '"] .item-title')

    page = browser.new_page(viewport={"width": 1280, "height": 900})
    # Install before application timers, then pause only for the dismissal proof.
    page.clock.install()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    page.goto([__file__, *urls][1] + "#/workspace?view=items")
    expected = ["si_old", "si_peer", "si_newer"]
    expect(page.locator(".error-line .error-code")).to_have_text("unavailable")
    expect(page.locator(".error-line .error-message")).to_have_text("Workspace unavailable")
    expect(page.locator(".quiet-line", has_text="Loading workspace")).to_have_count(0)
    snapshot_failure = False
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(0)
    order(page, expected)
    snapshot_failure = True
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line .error-code")).to_have_text("unavailable")
    order(page, expected)
    snapshot_failure = False
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(0)
    order(page, expected)
    expect(title(page, "si_old")).to_have_class(re.compile("\\bitem-title\\b"))
    page.locator('[data-sprint-item="si_old"] .item-head').click()
    expect(page.locator('[data-sprint-item="si_old"] .item-head')).to_have_attribute(
        "aria-current", "page"
    )
    order(page, expected)
    page.wait_for_function(
        "() => document.querySelector('[data-sprint-item-workspace=si_old]') !== null"
    )
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(10)
    assert len(held) == 1, "the full workspace request was not delayed"
    delay_old = False
    fulfill(
        held.pop(),
        {key: workspace("si_old")[key] for key in ["body", "artifacts", "conversation_history"]},
    )
    expect(
        page.locator('[data-sprint-item-workspace="si_old"] .sprint-workspace-title')
    ).to_contain_text("Old Item")
    snapshot_failure = True
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(1)
    expect(
        page.locator('[data-sprint-item-workspace="si_old"] .sprint-workspace-title')
    ).to_contain_text("Old Item")
    snapshot_failure = False
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(0)
    expect(title(page, "si_old")).to_have_class(re.compile("\\bitem-title\\b"))
    order(page, expected)
    rows = page.locator('[data-sprint-item="si_old"] [data-ticket-id]')
    expect(rows).to_have_count(2)
    assert [rows.nth(i).get_attribute("data-ticket-id") for i in range(2)] == [
        "t_recent",
        "t_older",
    ]
    page.locator('[data-sprint-item="si_old"] [data-ticket-id="t_recent"]').click()
    page.wait_for_function("() => location.hash === '#/workspace/item/si_old/t_recent'")
    order(page, expected)
    expect(page.locator(".ticket-screen .error-line")).to_have_count(1)
    snapshot_failure = True
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(2)
    expect(page.locator(".error-code", has_text="unavailable")).to_have_count(1)
    expect(page.locator(".ticket-screen .error-line")).to_have_count(1)
    snapshot_failure = False
    page.evaluate("window.__recover()")
    expect(page.locator(".error-line")).to_have_count(1)
    expect(page.locator('[data-sprint-item="si_old"] [data-ticket-id="t_recent"]')).to_have_class(
        re.compile("\\bticket-row--active\\b")
    )
    assert page.locator(".orchestrator-card.selected").count() == 0
    page.go_back()
    expect(page.locator('[data-sprint-item="si_old"] .item-head')).to_have_attribute(
        "aria-current", "page"
    )
    order(page, expected)
    page.locator('[data-sprint-item="si_peer"] .item-head').click()
    order(page, expected)
    page.go_back()
    order(page, expected)
    page.reload()
    expect(title(page, "si_old")).to_have_class(re.compile("\\bitem-title\\b"))
    order(page, expected)
    board["sprint_items"][1]["created_at"] = 0
    page.evaluate("window.__refresh()")
    order(page, ["si_peer", "si_old", "si_newer"])
    children[1]["activity_at"] = 30
    page.evaluate("window.__refresh()")
    expect(rows.first).to_have_attribute("data-ticket-id", "t_recent")
    children[2]["activity_at"] = 50
    page.evaluate("window.__refresh()")
    expect(rows.first).to_have_attribute("data-ticket-id", "t_recent")
    order(page, ["si_peer", "si_old", "si_newer"])
    page.goto([__file__, *urls][1] + "#/workspace?view=items")
    summary = board["sprint_items"][0]
    summary["tickets"] = [old]
    summary["awaiting_reply"] = True
    summary["conversation_signal"] = dict(working=False, message_for_owner=True)
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-item="si_old"] .item-head .stage-mark')).to_have_attribute(
        "data-conversation-signal", "message"
    )
    expect(title(page, "si_old")).to_have_class(re.compile("\\bitem-title\\b"))
    order(page, ["si_peer", "si_old", "si_newer"])
    summary["awaiting_reply"] = False
    summary["conversation_signal"] = dict(working=False, message_for_owner=False)
    page.evaluate("window.__refresh()")
    expect(title(page, "si_old")).to_have_class(re.compile("\\bitem-title\\b"))
    order(page, ["si_peer", "si_old", "si_newer"])
    page.goto([__file__, *urls][1] + "#/workspace/item/si_direct")
    order(page, ["si_peer", "si_old", "si_newer"])
    expect(page.locator('[data-sprint-item-workspace="si_direct"]')).to_have_count(1)
    page.reload()
    order(page, ["si_peer", "si_old", "si_newer"])
    source_row = page.locator('[data-sprint-item="si_peer"] [data-ticket-id]').first
    source_row.evaluate(
        "e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;}"
    )
    page.mouse.move(1200, 850)
    source_row.focus()
    panel = page.locator("[data-rail-hover-panel]").last
    expect(panel).to_have_attribute("aria-hidden", "false")
    page.evaluate("window.__startHoverWait()")
    page.wait_for_function("window.__hoverWaitStatus==='included'")
    page.clock.pause_at((page.evaluate("Date.now()") + 100) / 1000)
    deleted_id = source_row.get_attribute("data-ticket-id")
    source_row.evaluate("e=>e.remove()")
    for column in board["columns"]:
        column["cards"] = [row for row in column["cards"] if row["id"] != deleted_id]
    for summary in board["sprint_items"]:
        summary["tickets"] = [row for row in summary["tickets"] if row["id"] != deleted_id]
    page.evaluate("window.__refresh()")
    page.clock.run_for(32)
    expect(panel).to_have_attribute("aria-hidden", "false")
    page.evaluate("window.__startHoverWait()")
    # The detached source cannot certify a publication while its popup still exists.
    page.clock.run_for(32)
    hover_wait_status = page.evaluate("window.__hoverWaitStatus")
    assert hover_wait_status == "pending", hover_wait_status
    expect(panel).to_have_attribute("aria-hidden", "false")
    # Normal blur dismissal then closes the popup and removes its observer.
    page.clock.run_for(200)
    expect(panel).to_have_attribute("aria-hidden", "true")
    page.wait_for_function("window.__hoverWaitStatus==='unknown'")
    page.clock.resume()
    workers["chief_of_staff"]["conversation_signal"] = {"working": True, "message_for_owner": False}
    page.evaluate("window.__refresh()")
    page.goto([__file__, *urls][1] + "#/workspace/chief-of-staff")
    expect(
        page.locator(".chief-workspace .sprint-workspace-title [data-conversation-signal]")
    ).to_have_attribute("data-conversation-signal", "working")
    page.locator(
        ".chief-workspace [data-conversation-rest-bar], .chief-workspace [data-conversation-input]"
    ).first.wait_for()
    rest = page.locator(".chief-workspace [data-conversation-rest-bar]")
    if rest.count() and rest.is_visible():
        rest.click()
    expect(page.locator(".chief-workspace [data-conversation-input]")).to_be_visible()
    assert page.evaluate("window.__waitChief()")["status"] == "included"
    page.locator(".chief-workspace [data-conversation-pane]").evaluate(
        "node=>node.dataset.conversationId='wrong-pane'"
    )
    page.evaluate("window.__refresh()")
    assert page.evaluate("window.__waitChief()")["status"] == "unknown"
    page.locator(".chief-workspace [data-conversation-pane]").evaluate(
        "node=>node.dataset.conversationId='fixture-chief'"
    )
    page.evaluate("window.__refresh()")
    assert page.evaluate("window.__waitChief()")["status"] == "included"
    snapshot_failure = True
    page.evaluate("window.__recover()")
    expect(
        page.locator(".chief-workspace .sprint-workspace-title [data-conversation-signal]")
    ).to_have_count(0)
    expect(page.locator("[data-chief-conversation-retry]")).to_have_count(1)
    expect(page.locator(".error-line")).to_have_count(1)
    snapshot_failure = False
    page.locator("[data-chief-conversation-retry]").click()
    expect(page.locator(".error-line")).to_have_count(0)
    expect(
        page.locator(".chief-workspace .sprint-workspace-title [data-conversation-signal]")
    ).to_have_attribute("data-conversation-signal", "working")
    workers["chief_of_staff"]["conversation_signal"] = {"working": True, "message_for_owner": True}
    page.evaluate("window.__refresh()")
    snapshot_failure = True
    page.evaluate("window.__recover()")
    expect(
        page.locator(".chief-workspace .sprint-workspace-title [data-conversation-signal]")
    ).to_have_attribute("data-conversation-signal", "message")
    snapshot_failure = False
    page.locator("[data-chief-conversation-retry]").click()
    expect(page.locator("[data-chief-conversation-retry]")).to_have_count(0)
    composer = page.locator(".chief-workspace [data-conversation-input]")
    composer.fill("Chief continuation")
    composer.press("Enter")
    for _ in range(100):
        if "chief-delivered" in conversation_reads:
            break
        page.wait_for_timeout(10)
    assert chief_sends and "chief-delivered" in conversation_reads
    evidence = page.evaluate("window.__navigation.commandEvidence.at(-1)")
    assert evidence["status"] == "unknown" and evidence["reason"] == "missing_or_invalid_fence"
    assert evidence["impact"]["complete"] is True
    assert evidence["impact"]["records"][0]["kind"] == "chief"
    assert any(identity.startswith("chief-conversation:") for identity in evidence["observers"])
    assert not any(
        identity.startswith("orchestrator-section:") for identity in evidence["observers"]
    )
    assert evidence["domainCompletedAt"] >= evidence["startedAt"]
    old_reads = conversation_reads.count("fixture-chief")
    page.evaluate("window.__refresh()")
    assert conversation_reads.count("fixture-chief") == old_reads
    assert page.evaluate("window.__waitChief()")["status"] == "unknown"
    workers["chief_of_staff"]["conversation_id"] = "chief-delivered"
    page.evaluate("window.__refresh()")
    assert page.evaluate("window.__waitChief()")["status"] == "included"
    composer = page.locator(".chief-workspace [data-conversation-input]")
    composer.click()
    page.locator('.chief-workspace [aria-label="Conversation options"]').click()
    page.locator("[data-conversation-new-arm]").click()
    page.locator("[data-conversation-new-confirm]").click()
    for _ in range(100):
        if chief_resets:
            break
        page.wait_for_timeout(10)
    delivered_reads = conversation_reads.count("chief-delivered")
    page.evaluate("window.__refresh()")
    assert chief_resets and conversation_reads.count("chief-delivered") == delivered_reads
    assert page.evaluate("window.__waitChief()")["status"] == "unknown"
    workers["chief_of_staff"]["conversation_id"] = None
    page.evaluate("window.__refresh()")
    assert page.evaluate("window.__waitChief()")["status"] == "included"
    assert not errors, errors
    pass
