# ruff: noqa: E501
"""Shared browser case with canonical navigation-source coverage."""


def run(browser, urls):
    import runpy
    from pathlib import Path

    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
    )
    navigation_response = fixture["navigation_response"]
    scoped_navigation_response = fixture["scoped_navigation_response"]
    import hashlib
    import json
    import os
    from pathlib import Path
    from urllib.parse import parse_qs

    from playwright.sync_api import expect

    def worker_manifest(worker_type, label, stages):
        fields = [{"id": field, "label": stage_label} for _, stage_label, field in stages if field]
        stage_rows = [
            {
                "id": stage,
                "label": stage_label,
                "gating_field": field,
                "is_terminal": field is None,
                "ownership_mode": "worker" if field else None,
            }
            for stage, stage_label, field in stages
        ]
        return {
            "worker_type": worker_type,
            "label": label,
            "stages": stage_rows,
            "advance": {stages[i][0]: stages[i + 1][0] for i in range(len(stages) - 1)},
            "ceiling_range": [stage for stage, _, _ in stages],
            "default_ceiling": stages[1][0],
            "worker_profile_id": f"panels-worker-{worker_type}",
            "default_backend": "codex",
            "default_model": None,
            "default_reasoning_effort": None,
            "fields": fields,
        }

    coding = worker_manifest(
        "coding",
        "Coding",
        [
            ("needs_brief", "Brief", "brief"),
            ("needs_success_condition", "Success Condition", "success_condition"),
            ("needs_what_changes", "What Changes", "what_changes"),
            ("needs_plan", "Plan", "plan"),
            ("needs_implementation", "Implementation", "implementation"),
            ("needs_consequences", "Consequences", "consequences"),
            ("done", "Done", None),
        ],
    )
    exploration = worker_manifest(
        "exploration",
        "Exploration",
        [
            ("needs_brief", "Brief", "brief"),
            ("needs_understanding", "Understanding", "understanding"),
            ("needs_research_plan", "Research Plan", "research_plan"),
            ("needs_findings", "Findings", "findings"),
            ("needs_answer", "Answer", "answer"),
            ("needs_proposed_follow_up", "Proposed Follow-up", "proposed_follow_up"),
            ("needs_consequences", "Consequences", "consequences"),
            ("done", "Done", None),
        ],
    )
    manifest = {"worker_types": [coding, exploration]}

    def ticket(ticket_id, worker_type, stage, field, body, created_at):
        return {
            "project_id": "project_one",
            "project": "Panels",
            "sprint_id": None,
            "sprint_item_id": "item_one",
            "sprint_item_title": "Outcome",
            "id": ticket_id,
            "title": "Restore compact approval",
            "worker_type": worker_type,
            "employee_backend": "codex",
            "employee_launch_model": None,
            "employee_launch_reasoning_effort": None,
            "employee_configuration_editable": False,
            "stage": stage,
            "ceiling": stage,
            "ticket_status": "awaiting_approval",
            "ceiling_holder": {"kind": "sprint_item", "id": "item_one"},
            "conversation_id": None,
            "conversation_history": [],
            "day_ids": [],
            "blocked": False,
            "is_backlogged": False,
            "blocker_summary": {"blocked_by": []},
            "recap": "Orientation",
            "field_values": {"brief": "Request"},
            "pending_proposal_revision": 1,
            "pending_proposal": {
                "field": field,
                "body": body,
                "proposed_by": "agent",
                "created_at": created_at,
                "revision": 1,
            },
        }

    tickets = {
        "t_coding": ticket(
            "t_coding", "coding", "needs_success_condition", "success_condition", "Coding result", 1
        ),
        "t_findings": ticket(
            "t_findings", "exploration", "needs_findings", "findings", "Evidence", 2
        ),
        "t_consequences": ticket(
            "t_consequences", "exploration", "needs_consequences", "consequences", "Applied", 3
        ),
        "t_brief": ticket("t_brief", "coding", "needs_brief", "brief", "Kickoff request", 5),
        "t_review_replace": ticket(
            "t_review_replace", "coding", "needs_plan", "plan", "Original Review plan", 7
        ),
        "t_stale": ticket("t_stale", "coding", "needs_plan", "plan", "Original stale plan", 6),
        "t_failure": ticket(
            "t_failure", "coding", "needs_what_changes", "what_changes", "Retry me", 4
        ),
    }
    approvals = []
    scope_writes = []
    accept_attempts = []
    read_count = 0
    lifecycle_writes = []
    refuse_resume = True

    def navigation_snapshot():
        parent = {
            "id": "item_one",
            "title": "Outcome",
            "created_at": 1,
            "project_id": "project_one",
            "project": "Panels",
            "conversation_id": None,
            "tickets": [],
        }
        response = navigation_response(
            {
                "columns": [{"cards": list(tickets.values())}],
                "sprint_items": [
                    {**parent, "id": identity}
                    for identity in {
                        "item_one",
                        *(
                            row["sprint_item_id"]
                            for row in tickets.values()
                            if row["sprint_item_id"]
                        ),
                    }
                ],
            },
            {"chief_of_staff": {"label": "Chief of Staff", "conversation_id": None}},
        )
        for record in response["snapshot"]["records"]:
            if record["kind"] == "ticket":
                row = tickets[record["id"]]
                proposal = row["pending_proposal"]
                definition = next(
                    worker
                    for worker in manifest["worker_types"]
                    if worker["worker_type"] == row["worker_type"]
                )
                stage = next(stage for stage in definition["stages"] if stage["id"] == row["stage"])
                record["value"].update(
                    ceiling=row["ceiling"],
                    ceiling_holder=row["ceiling_holder"],
                    gating_field=stage["gating_field"],
                    stage_ownership=stage["ownership_mode"],
                    terminal=stage["is_terminal"],
                    proposal_field=None if proposal is None else proposal["field"],
                    proposal_created_at=None if proposal is None else proposal["created_at"],
                    proposal_revision=row["pending_proposal_revision"]
                    if proposal is None
                    else proposal["revision"],
                )
        return response

    def resource_revision(key):
        parts = key.split("/")
        value = tickets.get(parts[3], {}) if key.startswith("/api/tickets/") else manifest
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def workspace_ticket(ticket_id):
        source = tickets[ticket_id]
        definition = coding if source["worker_type"] == "coding" else exploration
        gate = next(
            row["gating_field"] for row in definition["stages"] if row["id"] == source["stage"]
        )
        return {
            **source,
            "locale": "en-US",
            "orchestrator": {"kind": "sprint_item", "id": "item_one"},
            "record_sha256": source.get("record_sha256", "record-initial"),
            "field_values": {gate: source["field_values"].get(gate, "")} if gate else {},
            "field_summaries": {
                field: {"revision": "1", "byte_count": len(value.encode())}
                for field, value in source["field_values"].items()
            },
            "field_approvals": source.get("field_approvals", {}),
        }

    def respond(route):
        nonlocal read_count, refuse_resume
        path = route.request.url.split("/api/", 1)[1]
        resource_key = None
        if path.startswith("resource-body?"):
            resource_key = parse_qs(path.split("?", 1)[1])["key"][0]
            path = resource_key.removeprefix("/api/")
        if path.startswith("resource-versions?"):
            keys = json.loads(parse_qs(path.split("?", 1)[1])["keys"][0])
            route.fulfill(
                json={
                    "epoch": "fixture",
                    "resources": [{"key": key, "revision": resource_revision(key)} for key in keys],
                }
            )
            return
        if path.startswith("tickets?"):
            path = "tickets/" + parse_qs(path.split("?", 1)[1])["id"][0]
        if path.split("?")[0] in {"navigation/snapshot", "navigation/view/snapshot"}:
            result = navigation_snapshot()
            if path.startswith("navigation/view/"):
                result = scoped_navigation_response(
                    result, json.loads(parse_qs(path.split("?", 1)[1])["view"][0])
                )
        elif path.startswith("artifact-catalog/"):
            result = {"revision": "empty", "directory": "", "offset": 0, "total": 0, "entries": []}
        elif path.startswith("conversation-history/"):
            result = {
                "revision": "empty",
                "offset": 0,
                "total": 0,
                "active_index": None,
                "entries": [],
            }
        elif path.startswith("tickets/") and path.endswith("/workspace"):
            read_count += 1
            result = workspace_ticket(path.split("/")[1])
        elif path.startswith("tickets/") and "/workspace/fields/" in path:
            _, ticket_id, _, _, field = path.split("/")
            result = {
                "ticket_id": ticket_id,
                "field": field,
                "revision": "1",
                "value": tickets[ticket_id]["field_values"].get(field, ""),
            }
        elif path == "review":
            raise AssertionError("competing Review summary read")
        elif path.endswith("/backlog") or path.endswith("/resume"):
            _, ticket_id, action = path.split("/")
            lifecycle_writes.append({"action": action, "body": route.request.post_data_json})
            if action == "resume" and refuse_resume:
                refuse_resume = False
                route.fulfill(
                    status=409,
                    json={
                        "error": {
                            "code": "stale_record",
                            "message": "Resume refused. Retry with this record.",
                        }
                    },
                )
                return
            tickets[ticket_id]["is_backlogged"] = action == "backlog"
            tickets[ticket_id]["record_sha256"] = "record-after-" + action
            result = tickets[ticket_id]
        elif (
            path.startswith("tickets/")
            and path.count("/") == 1
            and (route.request.method == "PATCH")
        ):
            scope_writes.append(route.request.post_data_json)
            target = tickets[path.split("/")[1]]
            change = route.request.post_data_json
            if (
                "ceiling_holder" in change
                and change["ceiling_holder"] != target["ceiling_holder"]
                and (target["pending_proposal"] is not None)
            ):
                target["pending_proposal"]["revision"] += 1
                target["pending_proposal_revision"] = target["pending_proposal"]["revision"]
            target.update(change)
            result = tickets[path.split("/")[1]]
        elif path == "test/replace-coding":
            tickets["t_coding"]["pending_proposal"] = {
                "field": "success_condition",
                "body": "Replacement result",
                "proposed_by": "agent",
                "created_at": 1,
                "revision": tickets["t_coding"]["pending_proposal"]["revision"] + 1,
            }
            result = {}
        elif path == "test/replace-review":
            tickets["t_review_replace"]["pending_proposal"].update(
                body="Replacement Review plan", revision=2
            )
            result = {}
        elif path == "test/state":
            result = {
                "read_count": read_count,
                "created_at": tickets["t_coding"]["pending_proposal"]["created_at"],
                "revision": tickets["t_coding"]["pending_proposal"]["revision"],
                "failure_attempts": len(
                    [item for item in accept_attempts if item["ticket_id"] == "t_failure"]
                ),
            }
        elif path.startswith("worker-types/") and path.endswith("/manifest"):
            worker_type = path.split("/")[1]
            matches = [row for row in manifest["worker_types"] if row["worker_type"] == worker_type]
            if not matches:
                route.fulfill(status=404, json={"error": {"code": "unknown_worker_type", "message": f"Unknown worker type: {worker_type}"}})
                return
            result = {"worker_types": matches}
        elif path == "worker-types":
            result = manifest
        elif path.startswith("tickets/") and "/accept/" in path:
            _, ticket_id, _, field = path.split("/", 3)
            body = route.request.post_data_json
            attempt = {"ticket_id": ticket_id, "field": field, "body": body}
            accept_attempts.append(attempt)
            if ticket_id == "t_stale" and body["expected_proposal_revision"] == 1:
                tickets[ticket_id]["pending_proposal"].update(body="New plan revision", revision=2)
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {"error": {"code": "stale_proposal", "message": "proposal changed"}}
                    ),
                )
                return
            if (
                ticket_id == "t_failure"
                and len([item for item in accept_attempts if item["ticket_id"] == ticket_id]) == 1
            ):
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body=json.dumps(
                        {"error": {"code": "test_failure", "message": "approval failed"}}
                    ),
                )
                return
            approvals.append(attempt)
            tickets[ticket_id].update(
                pending_proposal_revision=tickets[ticket_id]["pending_proposal"]["revision"],
                pending_proposal=None,
                ticket_status="empty",
            )
            result = tickets[ticket_id]
        elif path.endswith("/conversation/start-values"):
            result = {}
        elif path.startswith("tickets/") and path.count("/") == 1:
            ticket_id = path.split("/", 1)[1]
            read_count += 1
            result = tickets[ticket_id]
        elif path == "conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        if resource_key is not None:
            result = {
                "epoch": "fixture",
                "key": resource_key,
                "revision": resource_revision(resource_key),
                "body": result,
            }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    # Protect the real Workspace shell and its separate lazy-import/resource boundaries.
    if len(urls) > 1:
        proof = runpy.run_path(str(Path(__file__).with_name("workspace-skeleton-proof.py")))
        proof["run"](browser, urls[1], respond)

    page = browser.new_page()
    page.on("pageerror", lambda error: print(str(error), flush=True))
    page.route("**/api/**", respond)
    page.goto([__file__, *urls][1])
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def block():
        result = page.locator("[data-approval-block]")
        expect(result).to_have_count(1)
        return result

    def open_approval():
        block().locator("[data-accept]").click()
        expect(page.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")

    def choose(stage, reviewer="owner"):
        open_approval()
        page.locator(f'[data-ceiling-picker-choice="{stage}"]').click()
        expect(page.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
        page.locator(f'[data-ceiling-picker-choice="{reviewer}"]').click()

    def wait_count(items, count):
        for _ in range(100):
            if len(items) == count:
                return
            page.wait_for_timeout(20)
        assert len(items) == count, items

    expect(block().locator("[data-reviewer-heading]")).to_have_text("Orchestrator")
    assert (
        block().locator("[data-scope-ceiling], [data-ceiling-effect], .approves-mark").count() == 0
    )
    tickets["t_coding"]["ceiling_holder"] = {"kind": "owner", "id": "owner"}
    page.evaluate("window.__refresh()")
    artifact_dir = os.environ.get("PANELS_APPROVAL_ARTIFACT_DIR")
    evidence = []
    for host_kind in ["ticket", "review"]:
        if host_kind == "review":
            page.evaluate("window.__showReview('t_coding', 'success_condition')")
        expect(block().locator("[data-reviewer-heading]")).to_have_text("User")
        expect(block().locator("[contenteditable]")).to_have_text("Coding result")
        for width, label in [(1280, "desktop"), (390, "compact")]:
            page.set_viewport_size({"width": width, "height": 1000})
            page.emulate_media(reduced_motion="reduce")
            if host_kind == "ticket":
                expect(page.locator("[data-ticket-identity]")).to_have_text("Panels · Coding")
                page.locator("[data-stage-fold]").evaluate("(element) => element.open = true")
            assert page.evaluate(
                "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth + 1"
            )
            if artifact_dir:
                Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                path = f"{host_kind}-{label}-closed.png"
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            open_approval()
            panel = page.locator("[data-ceiling-picker-panel]")
            box = panel.bounding_box()
            assert box and box["x"] >= -1 and (box["x"] + box["width"] <= width + 1)
            assert (
                panel.locator("[data-ceiling-effect], .approves-mark, .holder-detail").count() == 0
            )
            if artifact_dir:
                path = f"{host_kind}-{label}-open.png"
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            page.locator('[data-ceiling-part="holder"]').click()
            expect(page.get_by_role("option", name="Orchestrator", exact=True)).to_have_count(1)
            if artifact_dir:
                path = f"{host_kind}-{label}-reviewers.png"
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            page.get_by_role("listbox").press("Escape")
            assert approvals == [] and scope_writes == []
    hash_before = page.evaluate("location.hash")
    open_approval()
    page.get_by_role("listbox").press("s")
    expect(page.locator("[data-listbox-picker-active]")).to_have_attribute(
        "data-ceiling-picker-choice", "needs_success_condition"
    )
    expect(page.locator("[data-review-card]")).to_have_attribute("data-ticket-id", "t_coding")
    expect(page.get_by_role("listbox")).to_be_focused()
    page.locator('[data-ceiling-part="holder"]').click()
    page.get_by_role("listbox").press("o")
    expect(page.locator("[data-listbox-picker-active]")).to_have_attribute(
        "data-ceiling-picker-choice", "item_one"
    )
    expect(page.get_by_role("listbox")).to_be_focused()
    assert page.evaluate("location.hash") == hash_before
    assert approvals == [] and scope_writes == []
    page.get_by_role("listbox").press("Escape")
    tickets["t_worker_holder"] = ticket(
        "t_worker_holder", "coding", "needs_plan", "plan", "Worker-held plan", 10
    )
    tickets["t_worker_holder"]["ceiling_holder"] = {"kind": "ticket", "id": "t_holder"}
    page.evaluate("window.__showTicket('t_worker_holder')")
    expect(block().locator("[data-reviewer-heading]")).to_have_text("Worker")
    block().locator("[data-reviewer-heading]").click()
    expect(page.get_by_role("option", name="Worker", exact=True)).to_have_attribute(
        "data-ceiling-picker-choice", "t_holder"
    )
    expect(page.get_by_role("option", name="Orchestrator", exact=True)).to_have_attribute(
        "data-ceiling-picker-choice", "item_one"
    )
    page.get_by_role("listbox").press("Escape")
    assert approvals == [] and scope_writes == []
    page.evaluate("window.__showTicket('t_coding')")
    block().locator("[data-reviewer-heading]").click()
    expect(page.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    page.locator('[data-ceiling-picker-choice="chief"]').click()
    wait_count(scope_writes, 1)
    page.evaluate("window.__refresh()")
    assert scope_writes[-1] == {"ceiling_holder": {"kind": "chief", "id": "chief"}}
    assert approvals == []
    open_approval()
    page.locator('[data-ceiling-part="holder"]').click()
    page.locator('[data-ceiling-picker-choice="owner"]').click()
    wait_count(scope_writes, 2)
    page.evaluate("window.__refresh()")
    assert scope_writes[-1] == {"ceiling_holder": {"kind": "owner", "id": "owner"}}
    assert approvals == []
    for index, stage in enumerate(["needs_success_condition", "needs_brief"], start=3):
        choose(stage)
        wait_count(scope_writes, index)
        page.evaluate("window.__refresh()")
    wait_count(scope_writes, 4)
    assert scope_writes[-1] == {
        "ceiling": "needs_brief",
        "ceiling_holder": {"kind": "owner", "id": "owner"},
    }
    assert approvals == []
    expect(block().locator("[data-accept]")).to_be_enabled()
    block().locator("[data-accept]").focus()
    block().locator("[data-accept]").press("ArrowDown")
    expect(page.get_by_role("listbox")).to_have_attribute("aria-label", "Ceiling stage")
    page.get_by_role("listbox").press("End")
    page.get_by_role("listbox").press("Enter")
    expect(page.get_by_role("listbox")).to_have_attribute("aria-label", "Who holds the ceiling")
    page.get_by_role("listbox").press("c")
    page.get_by_role("listbox").press("Escape")
    expect(block().locator("[data-accept]")).to_be_focused()
    open_approval()
    page.locator('[data-ceiling-part="holder"]').click()
    page.locator('[data-ceiling-part="ceiling"]').click()
    page.locator("[data-ticket-identity]").click()
    expect(page.get_by_role("listbox")).to_have_count(0)
    assert len(scope_writes) == 4 and approvals == []
    proposal = block().locator("[contenteditable]")
    proposal.fill("Local edit")
    proposal.press("Tab")
    page.evaluate("window.__refresh()")
    expect(proposal).to_have_text("Local edit")
    open_approval()
    page.locator('[data-ceiling-picker-choice="done"]').click()
    page.evaluate("fetch('/api/test/replace-coding', {method: 'POST'})")
    page.evaluate("window.__refresh()")
    expect(proposal).to_have_text("Replacement result")
    expect(page.get_by_role("listbox")).to_have_count(0)
    assert approvals == []
    expected_revision = 4
    assert tickets["t_coding"]["pending_proposal"]["revision"] == expected_revision
    choose("needs_what_changes")
    wait_count(approvals, 1)
    assert approvals[-1]["body"] == {
        "next_ceiling": "needs_what_changes",
        "next_holder": {"kind": "owner", "id": "owner"},
        "expected_proposal_revision": expected_revision,
    }
    tickets["t_findings"]["ceiling_holder"] = {"kind": "owner", "id": "owner"}
    page.evaluate("window.__showReview('t_findings', 'findings')")
    expect(block()).to_have_attribute("data-field", "findings")
    choose("needs_answer")
    wait_count(approvals, 2)
    assert approvals[-1]["body"]["expected_proposal_revision"] == 1
    assert approvals[-1]["body"]["next_ceiling"] == "needs_answer"
    page.evaluate("window.__showTicket('t_consequences')")
    expect(block()).to_have_attribute("data-field", "consequences")
    choose("done")
    wait_count(approvals, 3)
    assert approvals[-1]["body"]["next_ceiling"] == "done"
    page.evaluate("window.__showTicket('t_failure')")
    expect(block()).to_have_attribute("data-field", "what_changes")
    proposal = block().locator("[contenteditable]")
    proposal.fill("Edited retry")
    proposal.press("Tab")
    open_approval()
    page.locator('[data-ceiling-picker-choice="needs_plan"]').click()
    page.locator('[data-ceiling-picker-choice="owner"]').evaluate(
        "(button) => { button.click(); button.click(); }"
    )
    expect(block()).to_contain_text("approval failed")
    assert len([item for item in accept_attempts if item["ticket_id"] == "t_failure"]) == 1
    choose("needs_plan")
    wait_count(approvals, 4)
    assert approvals[-1]["body"] == {
        "next_ceiling": "needs_plan",
        "next_holder": {"kind": "owner", "id": "owner"},
        "expected_proposal_revision": 1,
        "edited_body": "Edited retry",
    }
    page.evaluate("window.__showTicket('t_stale')")
    expect(block()).to_have_attribute("data-field", "plan")
    choose("needs_implementation")
    expect(block()).to_contain_text("proposal changed")
    assert len(approvals) == 4
    page.evaluate("window.__refresh()")
    expect(block().locator("[contenteditable]")).to_have_text("New plan revision")
    choose("needs_implementation")
    wait_count(approvals, 5)
    assert approvals[-1]["body"]["expected_proposal_revision"] == 2
    tickets["t_review_replace"]["ceiling_holder"] = {"kind": "owner", "id": "owner"}
    page.evaluate("window.__showReview('t_review_replace', 'plan')")
    expect(block().locator("[contenteditable]")).to_have_text("Original Review plan")
    open_approval()
    page.locator('[data-ceiling-picker-choice="done"]').click()
    page.evaluate("fetch('/api/test/replace-review', {method: 'POST'})")
    page.evaluate("window.__refresh()")
    expect(block().locator("[contenteditable]")).to_have_text("Replacement Review plan")
    expect(page.get_by_role("listbox")).to_have_count(0)
    assert len(approvals) == 5
    step_states = []
    for status, expected in [
        ("agent", "current-running"),
        ("assigned", "current-assigned"),
        ("awaiting_approval", "current-awaiting-approval"),
        ("errored", "errored"),
    ]:
        state_id = "t_steps_" + status
        tickets[state_id] = ticket(
            state_id, "coding", "needs_what_changes", "what_changes", "Steps state proof", 20
        )
        tickets[state_id]["ticket_status"] = status
        if status != "awaiting_approval":
            tickets[state_id]["pending_proposal"] = None
        page.evaluate("(id) => window.__showTicket(id)", state_id)
        page.locator("[data-stage-fold]").evaluate("(element) => element.open = true")
        mark = page.locator(f"[data-stage-fold] .stage-mark--{expected}").first
        expect(mark).to_be_visible()
        page.emulate_media(reduced_motion="reduce")
        computed = mark.evaluate(
            "(element) => { const s = getComputedStyle(element); return {width:s.width, height:s.height, radius:s.borderTopLeftRadius, animation:s.animationName}; }"
        )
        assert computed["width"] == computed["height"]
        assert (
            float(computed["radius"].replace("px", ""))
            >= float(computed["width"].replace("px", "")) / 2
        )
        assert computed["animation"] == "none"
        step_states.append({"status": status, "computed": computed})
        if artifact_dir:
            path = f"steps-{status}-reduced-motion.png"
            page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
            evidence.append(path)
        if status == "agent":
            page.emulate_media(reduced_motion="no-preference")
            assert mark.evaluate("(element) => getComputedStyle(element).animationName") != "none"
            page.emulate_media(reduced_motion="reduce")
    tickets["t_brief"]["ceiling_holder"] = {"kind": "owner", "id": "owner"}
    page.evaluate("window.__showReview('t_brief', 'brief')")
    expect(block()).to_have_attribute("data-field", "brief")
    expect(block().locator("[data-send-back]")).to_have_count(0)
    expect(block().locator("[data-accept]")).to_be_enabled()
    assert len(approvals) == 5
    for index, principal in enumerate(
        [
            {"kind": "owner", "id": "owner"},
            {"kind": "chief", "id": "chief"},
            {"kind": "sprint_item", "id": "si_assigned"},
            {"kind": "sprint_item", "id": "si_foreign"},
            {"kind": "ticket", "id": "t_foreign"},
        ]
    ):
        identity = "t_principal_" + str(index)
        value = ticket(
            identity, "coding", "needs_plan", "plan", "Literal principal proposal", 100 + index
        )
        value["sprint_item_id"] = "si_assigned"
        value["ceiling_holder"] = principal
        tickets[identity] = value
        page.evaluate("(id) => window.__showTicket(id)", identity)
        expect(block().locator("[contenteditable]")).to_have_text("Literal principal proposal")
        count = len(approvals)
        choose("needs_implementation", principal["id"])
        wait_count(approvals, count + 1)
        assert approvals[-1]["body"] == {
            "next_ceiling": "needs_implementation",
            "next_holder": principal,
            "expected_proposal_revision": 1,
        }
    lifecycle = ticket("t_lifecycle", "coding", "needs_plan", "plan", "Retained proposal body", 200)
    lifecycle.update(is_backlogged=False, record_sha256="record-before-backlog")
    lifecycle["pending_proposal"]["revision"] = 7
    tickets["t_lifecycle"] = lifecycle
    page.evaluate("window.__showTicket('t_lifecycle')")
    page.get_by_role("button", name="Backlog ticket", exact=True).click()
    page.evaluate("window.__refresh()")
    expect(page.get_by_role("button", name="Resume ticket", exact=True)).to_be_visible()
    assert lifecycle_writes[-1] == {
        "action": "backlog",
        "body": {"expected_record_sha256": "record-before-backlog"},
    }
    page.get_by_role("button", name="Resume ticket", exact=True).click()
    expect(page.get_by_text("Resume refused. Retry with this record.", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Resume ticket", exact=True)).to_be_enabled()
    assert lifecycle["is_backlogged"] is True
    expect(block().locator("[contenteditable]")).to_have_text("Retained proposal body")
    assert lifecycle["pending_proposal"]["revision"] == 7
    page.get_by_role("button", name="Resume ticket", exact=True).click()
    page.evaluate("window.__refresh()")
    expect(page.get_by_role("button", name="Backlog ticket", exact=True)).to_be_visible()
    assert lifecycle_writes[-1] == {
        "action": "resume",
        "body": {"expected_record_sha256": "record-after-backlog"},
    }
    expect(block().locator("[contenteditable]")).to_have_text("Retained proposal body")
    assert lifecycle["pending_proposal"] == {
        "field": "plan",
        "body": "Retained proposal body",
        "proposed_by": "agent",
        "created_at": 200,
        "revision": 7,
    }
    count = len(approvals)
    choose("needs_implementation", "owner")
    wait_count(approvals, count + 1)
    assert approvals[-1]["body"] == {
        "next_ceiling": "needs_implementation",
        "next_holder": {"kind": "owner", "id": "owner"},
        "expected_proposal_revision": 7,
    }
    lifecycle.update(stage="done", pending_proposal=None)
    page.evaluate("window.__refresh()")
    expect(page.locator("[data-ticket-backlog-action]")).to_have_count(0)
    print("approval complete literal principals and guarded backlog/resume preservation passed")
    for window_size in (5, 60):
        scroll = page.evaluate("size => window.__scrollWindowProof(size)", window_size)
        assert scroll["forward"]["moves"] == 1, scroll
        assert scroll["forward"]["start"] == window_size // 2, scroll
        assert abs(scroll["forward"]["top"]) <= 1, scroll
        assert scroll["forward"]["height"] == scroll["height"], scroll
        assert scroll["forward"]["rows"] == window_size, scroll
        assert scroll["backward"]["start"] == 0, scroll
        assert abs(scroll["backward"]["top"]) <= 1, scroll
        assert scroll["backward"]["rows"] == window_size, scroll
    for window_size in (1, 5):
        scroll = page.evaluate("size => window.__scrollWindowProof(size, true)", window_size)
        assert scroll["initialMoves"] == 0, scroll
        assert scroll["forward"]["moves"] == 1, scroll
        assert scroll["forward"]["start"] == max(1, window_size // 2), scroll
        assert scroll["backward"]["moves"] == 2, scroll
        assert scroll["backward"]["start"] == 0, scroll
        assert scroll["backward"]["rows"] == window_size, scroll
    activated = page.evaluate("() => window.__scrollWindowProof(1, true, true)")
    assert activated["activations"] == 1, activated
    assert activated["initialMoves"] == 0, activated
    assert activated["forward"]["start"] == 1, activated
    assert activated["backward"]["start"] == 0, activated
    assert errors == [], errors
    if artifact_dir:
        (Path(artifact_dir) / "frontend-assertions.json").write_text(
            json.dumps(
                {
                    "hosts": ["TicketRoute", "ReviewRoute"],
                    "same_proposal_revision": 1,
                    "same_reviewer": "User",
                    "preceding_ticket_reviewer": "Orchestrator",
                    "screenshots": evidence,
                    "browser_errors": errors,
                    "steps": step_states,
                    "approval_transactions": approvals,
                    "scope_transactions": scope_writes,
                },
                indent=2,
            )
        )
    pass
