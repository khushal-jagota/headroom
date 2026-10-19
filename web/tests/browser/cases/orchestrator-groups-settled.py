# ruff: noqa: E501
"""Shared browser case with canonical navigation-source coverage."""


def run(browser, urls):
    import runpy
    from pathlib import Path

    from planner.navigation.contracts import SCHEMA_VERSION, SUPPORTED_SEMANTICS

    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
    )
    navigation_response = fixture["navigation_response"]
    import json
    import os
    import pathlib
    import time
    from urllib.parse import parse_qs, urlsplit

    from playwright.sync_api import expect

    quiet = dict(
        awaiting_reply=False,
        awaiting_answer=False,
        awaiting_approval=False,
        awaiting_agent_approval=False,
        assigned=False,
        agent_state="idle",
    )

    def ticket(id, parent="si_focus", **overrides):
        row = dict(
            id=id,
            title=id.replace("_", " "),
            deadline=None,
            project_id=None,
            project=None,
            group_project_id=None,
            group_project=None,
            activity_at=1,
            has_pending_proposal=False,
            ticket_status="agent",
            worker_type="coding",
            employee_backend="codex",
            stage="needs_implementation",
            stage_label="Implementation",
            gating_field="implementation",
            gating_field_label="Implementation",
            is_done=False,
            blocked=False,
            conversation_id="conv-" + id,
            waiting_to_closeout=False,
            sprint_item_id=parent,
            sprint_item_title=parent,
            day_ids=[],
            sprint_id=None,
            sprint_name=None,
            is_backlogged=False,
            **quiet,
        )
        row.update(overrides)
        return row

    focus_tickets = [
        ticket("t_approval", awaiting_approval=True),
        ticket("t_answer", awaiting_answer=True),
        ticket("t_yours", assigned=True),
        ticket("t_error", agent_state="errored"),
        ticket("t_empty", ticket_status=""),
        ticket("t_blocked", ticket_status="blocked"),
        ticket(
            "t_agent",
            awaiting_reply=True,
            conversation_signal=dict(working=False, message_for_owner=True),
        ),
        ticket(
            "t_done",
            stage="done",
            ticket_status="",
            is_done=True,
            awaiting_reply=True,
            conversation_signal=dict(working=False, message_for_owner=True),
        ),
    ]

    def item(id, title, rows=None, **overrides):
        row = dict(
            id=id,
            title=title,
            body="Shared work and owner decisions",
            project_id=None,
            project=None,
            created_at=1,
            updated_at=1,
            settled=False,
            conversation_id=None,
            conversation_history=[],
            tickets=rows or [],
            artifacts=[],
            ticket_rollup=dict(quiet),
            supervisor=dict(
                agent_key="sprint_item:" + id,
                conversation_id=None,
                launch_configuration=dict(
                    employee_backend="codex",
                    employee_launch_model=None,
                    employee_launch_reasoning_effort=None,
                ),
            ),
            **quiet,
        )
        row.update(overrides)
        return row

    items = [item("si_focus", "Product polish", focus_tickets)]
    for i in range(24):
        id = f"si_active_{i:02d}"
        items.append(
            item(id, f"Active work {i:02d}", [ticket(f"t_work_{i:02d}", id, assigned=True)])
        )
    for i in range(24):
        id = f"si_settled_{i:02d}"
        rows = (
            [
                ticket(
                    "t_archived_message",
                    id,
                    awaiting_reply=True,
                    conversation_signal=dict(working=False, message_for_owner=True),
                )
            ]
            if i == 0
            else []
        )
        items.append(
            item(
                id,
                f"Settled work {i:02d}",
                rows,
                settled=True,
                conversation_signal=dict(working=False, message_for_owner=i == 0),
                awaiting_reply=i == 0,
                conversation_id="conv-settled" if i == 0 else None,
            )
        )
    board = dict(
        columns=[
            dict(stage="needs_implementation", cards=[t for i in items for t in i["tickets"]])
        ],
        sprint_items=items,
    )
    workers = dict(
        chief_of_staff=dict(
            label="Chief of Staff",
            needs_me=False,
            awaiting_reply=False,
            agent_working=False,
            latest_turn_ended_sequence=0,
            recap="Keep our shared work clear.",
            conversation_id="fixture-chief",
        ),
        workers=[],
    )
    chief_rows = [
        ticket("t_chief_active", None),
        ticket("t_chief_saved", None, is_backlogged=True),
        ticket("t_chief_done", None, stage="done", is_done=True),
    ]
    recap_writes = []
    requests = []
    delivered_pointer = "delivered-local"
    content_failure = False
    mutations = []
    body_writes = []

    def respond(route):
        path = route.request.url.split("/api/", 1)[1].split("?")[0]
        requests.append(path)
        if route.request.method not in ("GET", "HEAD"):
            mutations.append((path, route.request.method))
        if path == "navigation/snapshot":
            wire_board = {
                **board,
                "columns": [{"cards": board["columns"][0]["cards"] + chief_rows}],
            }
            result = navigation_response(wire_board, workers)
        elif path == "board":
            raise AssertionError("competing board summary read")

        elif path == "workers/chief-of-staff/settings":
            result = workers["chief_of_staff"]
        elif path == "workers":
            assert any(
                selection in route.request.frame.url
                for selection in ("#/workspace/utility/settings", "#/workspace/utility/usage")
            ), ("competing roster read", route.request.frame.url)
            result = workers
        elif path == "workers/chief-of-staff/recap":
            recap_writes.append(route.request.post_data_json)
            workers["chief_of_staff"]["recap"] = route.request.post_data_json["recap"]
            result = workers["chief_of_staff"]
        elif path == "items/si_focus" and route.request.method == "PATCH":
            if "body" in route.request.post_data_json:
                body_writes.append(route.request.post_data_json)
            items[0].update(route.request.post_data_json)
            fence = {
                "version": 1,
                "scope": "fixture-navigation-scope",
                "epoch": "fixture-navigation",
                "schema_version": SCHEMA_VERSION,
                "semantics": SUPPORTED_SEMANTICS,
                "revision": str(navigation_response.__globals__["_navigation_revision"] + 1),
                "coverage": "complete",
                "witnesses": ["a" * 64],
                "signature": "synthetic-proof",
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"Panels-Navigation-Fence": json.dumps(fence)},
                body="{}",
            )
            return
        elif path.startswith("items/") and path.endswith("/conversation/send"):
            result = {"conversation_id": delivered_pointer, "fate": "started"}
        elif path.startswith("conversation/conversations/"):
            conversation_id = path.split("/")[2]
            if path.endswith("/events"):
                result = {
                    "events": [
                        {
                            "conversation_id": conversation_id,
                            "sequence": 1,
                            "kind": "agent_message",
                            "created_at": 1,
                            "payload": {
                                "text": "Past supervisor record"
                                if conversation_id == "past-supervisor"
                                else "Current supervisor record"
                            },
                        }
                    ]
                }
            elif path.endswith("/owner-read"):
                result = {"owner_read_through_sequence": 1}
            else:
                result = dict(
                    conversation_id=conversation_id,
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
        elif path.startswith("items/") and path.endswith("/workspace-content"):
            if content_failure:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":{"code":"content_unavailable","message":"Content unavailable"}}',
                )
                return
            result = {
                key: next(i for i in items if i["id"] == path.split("/")[1])[key]
                for key in ["body", "artifacts", "conversation_history"]
            }
        elif path == "conversation/backends":
            result = {"backends": []}
        elif path.startswith("conversation/backends/") and path.endswith("/sign-in"):
            result = {"sign_in": None}

        elif path == "worker-types":
            result = dict(worker_types=[])
        elif path == "conversation/backends":
            result = dict(backends=[])
        elif path == "chief/conversation":
            result = dict(conversation_id=None)
        elif path.endswith("start-values"):
            result = dict(backend_key="codex", model=None, reasoning_effort=None)
        elif path == "tickets" and "detail=summary" in route.request.url:
            query = parse_qs(urlsplit(route.request.url).query)
            rows = [
                row
                for row in chief_rows
                if (
                    row["is_backlogged"]
                    if query.get("is_backlogged") == ["true"]
                    else row["stage"] == "done"
                    if query.get("stage") == ["done"]
                    else not row["is_backlogged"] and row["stage"] != "done"
                )
            ]
            result = dict(
                tickets=rows,
                page=dict(
                    match_count=len(rows),
                    return_count=len(rows),
                    limit=30,
                    offset=0,
                    omitted_before=0,
                    omitted_after=0,
                    complete=True,
                    next_offset=None,
                ),
            )
        elif path == "tickets":
            route.fulfill(
                status=404, content_type="application/json", body='{"detail":"Ticket not found"}'
            )
            return
        else:
            result = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    def opened(page, key):
        return page.locator('[data-workspace-group="' + key + '"]').evaluate("n => n.open")

    def bottom(box):
        return box["y"] + box["height"]

    def screenshot(page, name):
        directory = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR") or os.environ.get(
            "PANELS_UI_PROOF_DIR"
        )
        if directory:
            pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(pathlib.Path(directory) / (name + ".png")), full_page=True)

    def motion_idle(page):
        page.wait_for_function(
            "() => !document.querySelector('.approved-rail').hasAttribute('data-rail-motion-busy') && ![...document.querySelector('.approved-rail').getAnimations({subtree:true})]\n            .some(a => a.playState === 'running' && a.effect.getTiming().iterations !== Infinity)"
        )
        page.wait_for_timeout(40)

    def motion_frames(page, name, action="void window.__refresh()"):
        page.evaluate(
            "action => {\n          window.__settledFrames = []; const start = performance.now();\n          const sample = () => {\n            const find = s => document.querySelector(s);\n            const opacity = n => n ? Number(getComputedStyle(n).opacity) : null;\n            const body = find('[data-settled-scroll]');\n            const card = find('[data-sprint-item=\"si_active_00\"]');\n            const row = find('[data-settled-item=\"si_active_00\"]');\n            const child = find('[data-message-conversation=\"conv-t_archived_message\"]');\n            const below = find('[data-settled-item=\"si_settled_01\"]');\n            window.__settledFrames.push({ms:performance.now()-start,\n              card:opacity(card),row:opacity(row),child:opacity(child),\n              body:body ? opacity(body)*opacity(body.querySelector('[data-settled-item]')) : null,height:find('.settled')?.getBoundingClientRect().height ?? 0,\n              below:below?.getBoundingClientRect().y ?? null,\n              chevron:getComputedStyle(find('.settled-chevron')).transform,\n              count:find('[data-settled-count]')?.textContent,\n              countOpacity:opacity(find('[data-settled-count]'))});\n            if(performance.now()-start<850) requestAnimationFrame(sample);\n          }; requestAnimationFrame(sample); (new Function(action))();\n        }",
            action,
        )
        page.wait_for_timeout(90)
        if name in ["settle", "reopen", "close"]:
            screenshot(page, "settled-motion-" + name + "-early")
        page.wait_for_timeout(180)
        if name in ["settle", "reopen", "open"]:
            screenshot(page, "settled-motion-" + name + "-late")
        page.wait_for_timeout(650)
        motion_idle(page)
        values = page.evaluate("window.__settledFrames")
        directory = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
        if directory:
            pathlib.Path(directory, "settled-motion-" + name + ".json").write_text(
                json.dumps(values, indent=2)
            )
        return values

    def overflow(locator):
        return locator.evaluate(
            "n => ({height:n.clientHeight, full:n.scrollHeight, top:n.scrollTop})"
        )

    errors = []
    page = browser.new_page(viewport=dict(width=1440, height=1000))
    page.on("pageerror", lambda error: errors.append(error.stack))
    page.route("**/api/**", respond)
    page.goto([__file__, *urls][1] + "#/workspace/item/si_focus")
    expect(page.locator('[data-workspace-group="agent"]')).to_be_visible()
    page.evaluate("window.__startAllWait()")
    page.wait_for_function("window.__allWaitStatus==='included'")
    selected = page.locator('[data-sprint-item="si_focus"]')

    def selected_style():
        return selected.evaluate(
            "n => ({border: getComputedStyle(n).borderTopColor, background: getComputedStyle(n).backgroundColor})"
        )

    selected.hover()
    page.wait_for_timeout(180)
    hover_style = selected_style()
    hovered_border = hover_style["border"]
    page.mouse.move(1430, 10)
    border_frames = selected.evaluate(
        "n => new Promise(resolve => {\n      const frames = [];\n      function sample() {\n        const style = getComputedStyle(n);\n        frames.push({border: style.borderTopColor, focused: n.matches(':focus-within'), hovered: n.matches(':hover')});\n        if (frames.length < 18) requestAnimationFrame(sample); else resolve(frames);\n      }\n      requestAnimationFrame(sample);\n    })"
    )
    print(
        "Selected card pointer-exit borders:",
        json.dumps(dict(hover=hovered_border, frames=border_frames)),
    )
    colors = selected.evaluate(
        "n => {\n      const probe = document.createElement('span'); n.append(probe);\n      probe.style.borderColor = 'var(--accent-bright)'; const accent = getComputedStyle(probe).borderTopColor;\n      probe.style.borderColor = 'var(--border-color)'; const neutral = getComputedStyle(probe).borderTopColor;\n      probe.remove(); return {accent, neutral};\n    }"
    )
    assert hovered_border == colors["accent"], hovered_border
    assert all(frame["border"] == colors["accent"] for frame in border_frames), border_frames
    assert (
        page.locator('[data-sprint-item="si_active_00"]').evaluate(
            "n => getComputedStyle(n).borderTopColor"
        )
        == colors["neutral"]
    )
    assert selected.evaluate("n => getComputedStyle(n).backgroundColor") == page.locator(
        '[data-sprint-item="si_active_00"]'
    ).evaluate("n => getComputedStyle(n).backgroundColor")
    expect(selected.locator(".item-head")).to_have_attribute("aria-current", "page")
    selected.locator(".item-head").click()
    page.mouse.move(1430, 10)
    assert selected.evaluate("n => getComputedStyle(n).borderTopColor") == colors["accent"]
    selected.locator(".item-head").blur()
    selected.locator(".item-head").focus()
    page.keyboard.press("Tab")
    focused = page.locator(":focus")
    assert focused.evaluate("n => getComputedStyle(n).outlineStyle") != "none"
    page.keyboard.press("Shift+Tab")
    assert selected.locator(".item-head").evaluate("n => n.matches(':focus-visible')")
    assert (
        selected.locator(".item-head").evaluate("n => getComputedStyle(n).outlineStyle") != "none"
    )
    assert selected.evaluate("n => getComputedStyle(n).borderTopColor") == colors["accent"]
    focus_style = selected_style()
    selected.locator(".item-head").blur()
    page.wait_for_function(
        "[...document.querySelectorAll('[data-rail-hover-panel]')].every(n => getComputedStyle(n).opacity === '0')"
    )
    normal_style = selected_style()
    neutral_background = page.locator('[data-sprint-item="si_active_00"]').evaluate(
        "n => getComputedStyle(n).backgroundColor"
    )
    selection_styles = dict(
        normal=normal_style,
        hover=hover_style,
        focus=focus_style,
        accent=colors["accent"],
        neutral_background=neutral_background,
    )
    print("Selected card computed styles:", json.dumps(selection_styles))
    assert all(
        state["border"] == colors["accent"] and state["background"] == neutral_background
        for state in [normal_style, hover_style, focus_style]
    )
    screenshot(page, "selected-outline")
    unfinished = [
        "awaiting_approval",
        "awaiting_answer",
        "assigned",
        "errored",
        "empty",
        "blocked",
        "agent",
    ]
    for key in unfinished:
        assert opened(page, key), key + " unfinished group did not open by default"
    assert not opened(page, "done")
    assert not opened(page, "backlog")
    expect(page.get_by_label("Worker state")).to_have_count(0)
    for key in unfinished:
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    row = page.locator('[data-sprint-ticket-id="t_agent"]')
    expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
    expect(row.locator('.stage-mark[aria-label="Unread message"]')).to_have_count(1)
    expect(row).to_have_attribute("href", "#/workspace/item/si_focus/t_agent")
    page.locator('[data-workspace-group="done"] summary').click()
    row = page.locator('[data-sprint-ticket-id="t_done"]')
    expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
    expect(row).to_have_attribute("href", "#/workspace/item/si_focus/t_done")
    assert opened(page, "done")
    expect(page.locator('[data-sprint-ticket-id="t_yours"] .stage-mark')).to_have_count(0)
    screenshot(page, "worker-groups-defaults")
    page.locator('[data-workspace-group="agent"] summary').click()
    focus_tickets[6]["title"] = "Updated Worker title"
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .ticket-row-title')).to_have_text(
        "Updated Worker title"
    )
    assert not opened(page, "agent")
    for key in ["done", "assigned"]:
        assert opened(page, key)
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    focus_tickets[6]["awaiting_reply"] = False
    focus_tickets[6]["conversation_signal"] = dict(working=False, message_for_owner=False)
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .stage-mark')).to_have_count(0)
    expect(
        page.locator('[data-workspace-group="agent"] summary .sprint-workspace-count')
    ).to_have_text("1")
    composer = page.locator("[data-conversation-input]")
    composer.fill("Pointer continuation proof")
    composer.press("Enter")
    page.wait_for_function(
        "() => [...window.__openStreams].some(url=>url.includes('delivered-local'))"
    )
    assert "conversation/conversations/delivered-local" in requests
    page.evaluate("window.__refresh()")
    assert page.evaluate("[...window.__openStreams].some(url=>url.includes('delivered-local'))")
    items[0]["conversation_id"] = "canonical-next"
    page.evaluate("window.__refresh()")
    page.wait_for_function(
        "() => [...window.__openStreams].some(url=>url.includes('canonical-next'))"
    )
    assert "conversation/conversations/canonical-next" in requests
    composer.click()
    page.locator(".chat-overflow > .chat-overflow-btn").click()
    page.locator("[data-conversation-new-arm]").click()
    page.locator("[data-conversation-new-confirm]").click()
    page.wait_for_function(
        "() => ![...window.__openStreams].some(url=>url.includes('canonical-next'))"
    )
    page.evaluate("window.__refresh()")
    assert not page.evaluate(
        "[...window.__openStreams].some(url=>url.includes('canonical-next'))"
    ), "unchanged old pointer must not undo local reset"
    items[0]["conversation_id"] = None
    page.evaluate("window.__refresh()")
    title_edit = page.locator(".sprint-workspace-title [contenteditable]")
    title_edit.fill("Updated orchestrator title")
    title_edit.press("Enter")
    page.wait_for_function("window.__commandWaitStatus==='pending'")
    required = page.evaluate("window.__commandRequired.map(observer=>observer.id)")
    assert any(identity.startswith("workspace-rail:") for identity in required), required
    assert any(identity.startswith("orchestrator-header:") for identity in required), required
    assert not any(identity.startswith("orchestrator-section:") for identity in required), required
    page.evaluate("window.__refresh()")
    page.wait_for_function("window.__commandWaitStatus==='included'")
    expect(title_edit).to_have_text("Updated orchestrator title")
    expect(page.locator('[data-sprint-item="si_focus"] .item-title')).to_have_text(
        "Updated orchestrator title"
    )
    content_failure = True
    page.evaluate("window.__refresh()")
    expect(page.locator(".sprint-item-doc .error-code")).to_have_text("content_unavailable")
    expect(page.locator(".sprint-workspace-title")).to_contain_text("Updated orchestrator title")
    expect(page.locator("[data-sprint-item-brief]")).to_contain_text(
        "Shared work and owner decisions"
    )
    content_failure = False
    page.evaluate("window.__refresh()")
    expect(page.locator(".sprint-item-doc .error-line")).to_have_count(0)
    motion_started = time.monotonic()
    all_cards = board["columns"][0]["cards"]
    motion_ids = {"si_active_00", "si_settled_00", "si_settled_01"}
    board["sprint_items"] = [i for i in items if i["id"] in motion_ids]
    board["columns"][0]["cards"] = [t for t in all_cards if t["sprint_item_id"] in motion_ids]
    page.evaluate("location.hash = '#/workspace?view=items'")
    page.evaluate("window.__refresh()")
    head = page.locator(".settled-head")
    motion_idle(page)
    opened_frames = motion_frames(page, "open", "document.querySelector('.settled-head').click()")
    assert any(0 < x["body"] < 1 for x in opened_frames if x["body"] is not None), opened_frames
    heights = [x["height"] for x in opened_frames]
    assert any(1 < h < heights[-1] - 1 for h in heights), heights
    assert len(set(x["chevron"] for x in opened_frames)) > 2
    assert head.get_attribute("aria-expanded") == "true"
    stable = motion_frames(page, "unchanged-refresh")
    assert all(x["below"] is not None for x in stable)
    assert (
        page.locator('[data-settled-item="si_settled_01"]').evaluate(
            "n => getComputedStyle(n).opacity"
        )
        == "1"
    )
    assert not page.locator(".settled .rail-moving").count()
    moving_item = next(i for i in items if i["id"] == "si_active_00")
    moving_item["settled"] = True
    settled_frames = motion_frames(page, "settle")
    exiting = [x for x in settled_frames if x["card"] is not None and 0 < x["card"] < 1]
    arriving = [x for x in settled_frames if x["row"] is not None and 0 < x["row"] < 1]
    assert exiting and arriving, settled_frames
    assert max(x["ms"] for x in exiting) < min(x["ms"] for x in arriving)
    assert all(x["row"] is None for x in exiting), "Destination appeared before source departure"
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(1)
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(0)
    expect(head).to_have_accessible_name("Settled (3)")
    assert any(0 < x["countOpacity"] < 1 for x in settled_frames if x["countOpacity"] is not None)
    moving_item["settled"] = False
    reopened_frames = motion_frames(page, "reopen")
    exiting = [x for x in reopened_frames if x["row"] is not None and 0 < x["row"] < 1]
    arriving = [x for x in reopened_frames if x["card"] is not None and 0 < x["card"] < 1]
    assert exiting and arriving, reopened_frames
    assert max(x["ms"] for x in exiting) < min(x["ms"] for x in arriving)
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(0)
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name("Settled (2)")
    child_ticket = next(t for i in items for t in i["tickets"] if t["id"] == "t_archived_message")
    page.locator('[data-ticket-id="t_work_00"]').hover()
    child_ticket["awaiting_reply"] = False
    child_ticket["conversation_signal"] = dict(working=False, message_for_owner=False)
    page.evaluate("window.__refresh()")
    child = page.locator('[data-message-conversation="conv-t_archived_message"]')
    expect(child).to_have_count(1)
    expect(child.locator(".stage-mark")).to_have_count(0)
    page.mouse.move(1430, 10)
    child_exit = motion_frames(page, "child-exit", "")
    assert any(x["child"] is not None and 0 < x["child"] < 1 for x in child_exit)
    assert child_exit[-1]["child"] is None
    positions = [x["below"] for x in child_exit if x["below"] is not None]
    assert max(positions) - min(positions) < 2, positions
    child_ticket["awaiting_reply"] = True
    child_ticket["conversation_signal"] = dict(working=False, message_for_owner=True)
    child_enter = motion_frames(page, "child-enter")
    assert any(x["child"] is not None and 0 < x["child"] < 1 for x in child_enter)
    expect(page.locator('[data-message-conversation="conv-t_archived_message"]')).to_have_count(1)
    assert not any((path.endswith("/settled") for path, method in mutations)), mutations
    expect(page.locator("[data-settled-count]")).to_have_text("(2)")
    page.locator('[data-settled-item="si_settled_00"] .settled-title').focus()
    closed_frames = motion_frames(page, "close", "document.querySelector('.settled-head').click()")
    assert any(x["body"] is not None and 0 < x["body"] < 1 for x in closed_frames)
    assert any(1 < x["height"] < closed_frames[0]["height"] - 1 for x in closed_frames)
    expect(head).to_be_focused()
    expect(page.locator("[data-settled-item]")).to_have_count(0)
    moving_item["settled"] = True
    motion_frames(
        page,
        "rapid-refresh",
        "const head=document.querySelector('.settled-head');\n      head.click(); setTimeout(()=>{head.click(); void window.__refresh();},40);\n      setTimeout(()=>head.click(),80);",
    )
    expect(head).to_have_attribute("aria-expanded", "true")
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name("Settled (3)")
    assert not page.locator(".settled .rail-moving").count()
    page.evaluate("document.querySelector('.settled-head').click()")
    page.wait_for_function("document.querySelector('.settled .rail-moving') !== null")
    page.emulate_media(reduced_motion="reduce")
    motion_idle(page)
    expect(page.locator("[data-settled-item]")).to_have_count(0)
    assert page.locator(".settled").evaluate(
        "n => n.style.overflow === '' && n.getAnimations({subtree:true}).length === 0"
    )
    page.evaluate("document.querySelector('.settled-head').click()")
    expect(page.locator("[data-settled-item]")).to_have_count(3)
    moving_item["settled"] = False
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name("Settled (2)")
    page.evaluate("document.querySelector('.settled-head').click()")
    expect(page.locator("[data-settled-item]")).to_have_count(0)
    assert page.locator(".settled").evaluate("n => n.getAnimations({subtree:true}).length") == 0
    page.emulate_media(reduced_motion="no-preference")
    board["sprint_items"] = items
    board["columns"][0]["cards"] = all_cards
    print(
        "Added Settled connected motion sequence seconds:",
        round(time.monotonic() - motion_started, 2),
    )

    for label, width, height in [("tall", 1440, 1000), ("short", 1280, 480), ("compact", 390, 560)]:
        page.set_viewport_size(dict(width=width, height=height))
        page.goto([__file__, *urls][1] + "#/workspace?view=items")
        page.reload()
        head = page.locator(".settled-head")
        rail = page.locator(".board-workspace-left")
        active = page.locator("[data-active-work-scroll]")
        settled = page.locator(".settled")
        expect(head).to_have_attribute("aria-expanded", "false")
        expect(page.locator("[data-settled-count]")).to_have_text("(24)")
        expect(page.locator("[data-settled-item]")).to_have_count(0)
        footer = page.get_by_role("navigation", name="Workspace utilities")
        expect(footer).to_be_in_viewport()
        config = footer.get_by_role("link", name="Settings", exact=True)
        backends = footer.get_by_role("link", name="Usage and Backends", exact=True)
        expect(config).to_have_attribute("href", "#/workspace/utility/settings?view=items")
        expect(backends).to_have_attribute("href", "#/workspace/utility/usage?view=items")
        expect(head).to_have_text("Settled (24)")
        expect(head.locator("svg[aria-hidden=true]")).to_have_count(1)
        rail_box, head_box = (rail.bounding_box(), head.bounding_box())
        assert head_box["y"] > rail_box["y"] + rail_box["height"] * 0.7
        assert bottom(head_box) <= bottom(rail_box) + 1
        assert abs(bottom(head_box) - footer.bounding_box()["y"]) < 2, (label, rail_box, head_box)
        footer_box = footer.bounding_box()
        screenshot(page, "settled-" + label + "-collapsed")
        head.click()
        expect(head).to_have_attribute("aria-expanded", "true")
        motion_idle(page)
        expect(page.locator("[data-settled-item]")).to_have_count(24)
        body = page.locator("[data-settled-scroll]")
        rail_box, settled_box = (rail.bounding_box(), settled.bounding_box())
        padding = rail.evaluate(
            "n => parseFloat(getComputedStyle(n).paddingTop) + parseFloat(getComputedStyle(n).paddingBottom)"
        )
        usable = rail_box["height"] - padding - footer_box["height"]
        assert settled_box["height"] <= usable * 0.5 + 1, (label, usable, settled_box)
        assert bottom(settled_box) <= bottom(rail_box) + 1
        assert overflow(active)["full"] > overflow(active)["height"], (
            label + " active fixture did not overflow"
        )
        assert overflow(body)["full"] > overflow(body)["height"], (
            label + " settled fixture did not overflow"
        )
        active_box, head_box, body_box = (
            active.bounding_box(),
            head.bounding_box(),
            body.bounding_box(),
        )
        assert bottom(active_box) <= head_box["y"] + 1
        assert bottom(head_box) <= body_box["y"] + 1
        assert head_box["y"] >= rail_box["y"]
        screenshot(page, "settled-" + label + "-expanded")
        active.evaluate("n => n.scrollTop = n.scrollHeight")
        expect(active.locator(".item-head, [data-ticket-id]").last).to_be_in_viewport()
        assert overflow(body)["top"] == 0
        active_top = overflow(active)["top"]
        body.evaluate("n => n.scrollTop = n.scrollHeight")
        expect(body.locator("[data-settled-item] .settled-title").last).to_be_in_viewport()
        assert overflow(active)["top"] == active_top
        assert head.bounding_box() == head_box
        assert footer.bounding_box() == footer_box
        expect(config).to_be_in_viewport()
        expect(backends).to_be_in_viewport()
        screenshot(page, "settled-" + label + "-last-rows")
        head.click()
        expect(head).to_have_attribute("aria-expanded", "false")
        motion_idle(page)
        assert abs(head.bounding_box()["y"] - (bottom(settled_box) - head_box["height"])) < 2
        config.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("location.hash === '#/workspace/utility/settings?view=items'")
        backends.click()
        page.wait_for_function("location.hash === '#/workspace/utility/usage?view=items'")
        page.evaluate("location.hash = '#/workspace?view=items'")
        head.click()
        motion_idle(page)
        body = page.locator("[data-settled-scroll]")
        body.evaluate("n => n.scrollTop = 0")
        expect(
            page.locator('[data-settled-item="si_settled_00"] .settled-title .stage-mark')
        ).to_have_attribute("aria-label", "Unread message")
        message = page.locator('[data-message-conversation="conv-t_archived_message"]')
        expect(message.locator(".stage-mark")).to_have_attribute("aria-label", "Unread message")
        expect(message).to_have_attribute(
            "href", "#/workspace/item/si_settled_00/t_archived_message"
        )
        message.click()
        page.wait_for_function(
            "location.hash === '#/workspace/item/si_settled_00/t_archived_message'"
        )
    page.set_viewport_size(dict(width=1440, height=1000))
    workers["chief_of_staff"]["conversation_signal"] = dict(working=True, message_for_owner=False)
    page.evaluate("window.__refresh()")
    page.evaluate("location.hash = '#/workspace/chief-of-staff'")
    chief = page.locator(".chief-workspace")
    expect(chief.get_by_role("heading", level=1)).to_be_visible()
    expect(chief.get_by_role("button", name="Settle Chief of Staff")).to_have_count(0)
    expect(chief.locator('h1 [data-conversation-signal="working"]')).to_have_count(1)
    recap = chief.get_by_role("textbox", name="Orchestrator recap")
    expect(recap).to_have_text("Keep our shared work clear.")
    recap.fill("Keep the Chief and orchestrators consistent.")
    with page.expect_response("**/api/workers/chief-of-staff/recap"):
        chief.get_by_role("heading", level=1).click()
    expect(recap).to_have_text("Keep the Chief and orchestrators consistent.")
    assert recap_writes == [dict(recap="Keep the Chief and orchestrators consistent.")], (
        recap_writes
    )
    page.evaluate("window.__refresh()")
    expect(recap).to_have_text("Keep the Chief and orchestrators consistent.")
    pane = chief.locator("[data-conversation-pane]")
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    document_column = chief.locator(".sprint-item-column").bounding_box()
    conversation_column = chief.locator(".conversation-column").bounding_box()
    assert abs(document_column["x"] - conversation_column["x"]) < 1
    assert abs(document_column["width"] - conversation_column["width"]) < 1
    page.mouse.move(1430, 10)
    expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
    page.wait_for_function(
        "[...document.querySelectorAll('[data-rail-hover-panel]')].every(n => getComputedStyle(n).opacity === '0')"
    )
    screenshot(page, "chief-desktop-rest")
    composer = chief.locator("[data-conversation-input]")
    composer.click()
    composer.fill("Draft that must survive conversation controls")
    expect(pane).to_have_attribute("data-conversation-state", "peeked")
    screenshot(page, "chief-desktop-peeked")
    chief.locator("[data-conversation-expand]").click()
    expect(pane).to_have_attribute("data-conversation-state", "opened")
    screenshot(page, "chief-desktop-opened")
    chief.locator("[data-conversation-collapse]").click()
    expect(pane).to_have_attribute("data-conversation-state", "peeked")
    composer.focus()
    page.keyboard.press("Escape")
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    expect(composer).to_have_value("Draft that must survive conversation controls")
    composer.click()
    chief.get_by_role("heading", level=1).click()
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    expect(composer).to_have_value("Draft that must survive conversation controls")
    page.evaluate("window.__refresh()")
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    page.set_viewport_size(dict(width=390, height=560))
    for kind, address in [
        ("item", "#/workspace/item/si_focus"),
        ("chief", "#/workspace/chief-of-staff"),
        ("ticket", "#/workspace/item/si_focus/t_agent"),
    ]:
        page.evaluate("hash => location.hash = hash", address)
        inspector = page.locator(".board-workspace-right--" + kind)
        expect(inspector).to_be_visible()
        expect(page.locator(".board-workspace-left")).to_be_hidden()
        expect(
            page.get_by_role("navigation", name="Workspace utilities", include_hidden=True)
        ).to_be_hidden()
        expect(page.locator(".rail-grip")).to_be_hidden()
        shell_box = page.locator(".board-workspace-shell").bounding_box()
        inspector_box = inspector.bounding_box()
        if kind in ["chief", "item"]:
            assert inspector.evaluate("n => getComputedStyle(n).padding") == "0px"
        assert abs(inspector_box["y"] - shell_box["y"]) < 1, (kind, shell_box, inspector_box)
        assert abs(inspector_box["height"] - shell_box["height"]) < 1, (
            kind,
            shell_box,
            inspector_box,
        )
        assert inspector_box["x"] >= 0 and inspector_box["x"] + inspector_box["width"] <= 391
        assert bottom(inspector_box) <= 560
        screenshot(page, "compact-selected-" + kind)
        if kind == "chief":
            pane = inspector.locator("[data-conversation-pane]")
            expect(pane).to_have_attribute("data-conversation-state", "rest")
            inspector.locator("[data-conversation-input]").click()
            expect(pane).to_have_attribute("data-conversation-state", "peeked")
            inspector.locator("[data-conversation-expand]").click()
            expect(pane).to_have_attribute("data-conversation-state", "opened")
            assert pane.bounding_box()["width"] <= 390
            assert bottom(pane.bounding_box()) <= 560
            screenshot(page, "chief-compact-opened")
            inspector.locator("[data-conversation-collapse]").click()
            expect(pane).to_have_attribute("data-conversation-state", "peeked")
            page.keyboard.press("Escape")
            expect(pane).to_have_attribute("data-conversation-state", "rest")
    page.evaluate("location.hash = '#/workspace?view=items'")
    expect(page.locator(".board-workspace-left")).to_be_visible()
    expect(page.get_by_role("navigation", name="Workspace utilities")).to_be_in_viewport()
    expect(page.locator(".board-workspace-right")).to_be_hidden()
    page.set_viewport_size(dict(width=1440, height=1000))
    items[0]["supervisor"]["conversation_id"] = "current-supervisor"
    items[0]["conversation_id"] = "current-supervisor"
    items[0]["artifacts"] = [{"name": "notes.md", "opens": "artifacts/notes.md", "children": []}]
    items[0]["conversation_history"] = [
        {"conversation_id": "past-supervisor", "created_at": 1},
        {"conversation_id": "current-supervisor", "created_at": 2},
    ]
    page.evaluate("location.hash = '#/workspace/item/si_focus'")
    page.evaluate("window.__refresh()")
    workspace = page.locator('[data-sprint-item-workspace="si_focus"]')
    recap = workspace.get_by_role("textbox", name="Orchestrator recap")
    replacement = "Recap preserves the exact agreed scope and prior decisions."
    recap.fill(replacement)
    with page.expect_response("**/api/items/si_focus"):
        recap.press("Control+Enter")
    assert body_writes == [{"body": replacement}], body_writes
    page.reload()
    expect(recap).to_contain_text("Recap preserves")
    page.route(
        "**/files/sprint-items/si_focus/artifacts/notes.md",
        lambda route: route.fulfill(
            status=200,
            content_type="text/markdown",
            body="# Archived notes\n\nRetain the original scope.",
        ),
    )
    artifacts = workspace.locator("[data-artifacts-band]")
    artifacts.locator(":scope > summary").click()
    artifacts.locator('[data-artifact-chip="artifacts/notes.md"]').click()
    expect(page.locator("[data-ticket-artifact]")).to_contain_text("Retain the original scope.")
    page.locator("[data-ticket-artifact-close]").click()
    rest = workspace.locator("[data-conversation-rest-bar]")
    rest.click()
    options = workspace.get_by_role("button", name="Conversation options")
    options.focus()
    page.keyboard.press("Enter")
    history = workspace.get_by_label("Orchestrator conversation")
    history.select_option("past-supervisor")
    workspace.locator('[data-conversation-lens-choice="full"]').click()
    expect(workspace.get_by_text("Past supervisor record", exact=True)).to_be_visible()
    expect(workspace.locator('[data-conversation-read-only-boundary="true"]')).to_have_count(1)
    expect(workspace.locator("[data-conversation-input]")).to_have_count(0)
    options.click()
    history.select_option("__current__")
    expect(workspace.get_by_text("Current supervisor record", exact=True)).to_be_visible()
    expect(workspace.locator('[data-conversation-read-only-boundary="true"]')).to_have_count(0)
    expect(workspace.locator("[data-conversation-input]")).to_be_visible()
    assert (
        workspace.locator("[data-conversation-pane]").get_attribute("data-conversation-lens")
        == "full"
    )
    assert not errors, errors
    pass
    print(
        "orchestrator defaults, stable choices, counts/dots/navigation, and independent Settled scrolls passed"
    )
