# ruff: noqa: E501
"""Ticket and Review interactions, including stale response field suppression."""


def run(browser, urls):
    import runpy
    from pathlib import Path

    from planner.navigation.contracts import SCHEMA_VERSION, SUPPORTED_SEMANTICS
    from planner.navigation.delivery_contracts import encode_cursor

    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
    )
    navigation_response = fixture["navigation_response"]
    scoped_navigation_response = fixture["scoped_navigation_response"]
    import json
    import os
    import time
    from urllib.parse import parse_qs

    from playwright.sync_api import expect

    ticket = dict(
        project_id="project_one",
        project="One",
        manager_id="outcome_kept",
        manager_title="Kept outcome",
        id="t_review",
        title="Review ticket",
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model=None,
        employee_launch_reasoning_effort=None,
        employee_configuration_editable=False,
        stage="needs_success_condition",
        ceiling="needs_success_condition",
        ticket_status="awaiting_approval",
        ceiling_holder={"kind": "chief", "id": "chief"},
        conversation_id=None,
        conversation_history=[],
        blocked=False,
        is_backlogged=False,
        blocker_summary={"blocked_by": []},
        recap="Orientation",
        # A stale response must not reintroduce the retired Ticket field.
        guidance="Retired guidance sentinel",
        field_values={"brief": "Request"},
        pending_proposal={
            "field": "success_condition",
            "body": "A result",
            "proposed_by": "agent",
            "created_at": 1,
            "revision": 1,
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
    canonical_worker_types = json.loads(json.dumps(manifest["worker_types"]))
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
    requests = []
    writes = []
    placement_writes = []
    ceiling_writes = []
    approvals = []
    backlog_writes = []
    held_details = []
    held_field_saves = []
    fail_field_save = False
    hold_field_save = False
    hold_detail = False
    snapshot_unavailable = True
    runtime_working = False
    server_revision = None
    navigation_proposal = None
    source_deleted = False
    retained_conversation = None
    parent_project = {"id": "project_one", "name": "One"}
    variant_seed = json.loads(json.dumps(ticket))
    fixture_overrides = {}
    revisions = []
    session_revised = False

    def fixture_ticket(identity):
        if identity in fixture_overrides:
            return fixture_overrides[identity]
        if identity == "t_personal_pending":
            return {
                **variant_seed,
                "ticket_status": "awaiting_approval",
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
                    "created_at": 2,
                },
            }
        if identity == "t_artifacts":
            return {
                **variant_seed,
                "id": "t_artifacts",
                "title": "Artifact links in place",
                "conversation_id": "artifact-conversation",
                "conversation_history": [
                    {"conversation_id": "past-artifact-conversation", "created_at": 1},
                    {"conversation_id": "artifact-conversation", "created_at": 2},
                ],
                "field_values": {
                    "brief": "Saved [HTML plan](/files/managers/si_artifacts/artifacts/plan.html?mode=read#summary). [Ticket document](/files/tickets/t_artifacts/artifacts/plan.md) [Receipt](/files/tickets/t_artifacts/artifacts/receipt.json) [Raw receipt](/files/managers/si_artifacts/artifacts/receipt.json) [Unsupported](/files/managers/si_artifacts/artifacts/archive.zip) [External](https://example.test/files/managers/si_artifacts/plan.html) [Dev preview](/dev/tickets/t_artifacts/8791/) [Malformed](/files/managers/si_artifacts/artifacts/%252e%252e/plan.html) [Explicit receipt](#/preview?source=ticket&ticket=t_artifacts&path=artifacts%2Freceipt.json)"
                },
                "pending_proposal": {
                    "field": "success_condition",
                    "body": "Proposed [Markdown plan](/files/managers/si_artifacts/artifacts/plan.md).",
                    "proposed_by": "agent",
                    "created_at": 1,
                    "revision": 1,
                },
            }
        if identity == "t_personal":
            return {
                **variant_seed,
                "id": identity,
                "worker_type": "personal",
                "stage": "needs_outcome",
                "ceiling": "needs_outcome",
                "field_values": {"brief": "Context"},
                "pending_proposal": None,
                "ticket_status": "empty",
                "assigned": True,
            }
        if identity == "t_kickoff":
            return {
                **variant_seed,
                "id": identity,
                "stage": "needs_brief",
                "ceiling": "needs_brief",
                "ceiling_holder": {"kind": "chief", "id": "chief"},
                "manager_id": None,
                "manager_title": None,
                "pending_proposal": {
                    "field": "brief",
                    "body": "Opened for somebody else",
                    "proposed_by": "chief",
                    "created_at": 1,
                    "revision": 1,
                },
                "field_values": {},
            }
        if identity == "t_with_session":
            return {**variant_seed, "id": identity, "conversation_id": "existing-worker-session"}
        if identity == "t_open":
            return {
                **variant_seed,
                "id": identity,
                "ticket_status": "empty",
                "pending_proposal": None,
                "ceiling_holder": {"kind": "owner", "id": "owner"},
            }
        if identity == "t_done":
            return {
                **variant_seed,
                "id": identity,
                "stage": "done",
                "ticket_status": "empty",
                "pending_proposal": None,
            }
        return ticket

    source_revision = 0
    last_records = None

    def review_snapshot(*, force_publication=False):
        nonlocal retained_conversation, source_revision, last_records
        source = [
            fixture_ticket(identity)
            for identity in [
                "t_review",
                "t_personal",
                "t_personal_pending",
                "t_artifacts",
                "t_kickoff",
                "t_with_session",
                "t_open",
                "t_done",
            ]
            if identity != "t_review" or not source_deleted
        ]
        parent = {
            "id": "outcome_kept",
            "title": "Kept outcome",
            "created_at": 1,
            "project_id": parent_project["id"],
            "project": parent_project["name"],
            "conversation_id": None,
            "tickets": [],
        }
        response = navigation_response(
            {"columns": [{"cards": source}], "managers": [parent]},
            {"chief_of_staff": {"label": "Chief of Staff", "conversation_id": None}},
        )
        rows = {row["id"]: row for row in source}
        for record in response["snapshot"]["records"]:
            if record["kind"] == "ticket":
                row = rows[record["id"]]
                proposal = (
                    navigation_proposal
                    if row["id"] == "t_review" and navigation_proposal
                    else row["pending_proposal"]
                )
                definition = next(

                        worker
                        for worker in canonical_worker_types
                        if worker["worker_type"] == row["worker_type"]

                )
                stage = next(
                    stage for stage in definition["stages"] if stage["id"] == row["stage"]
                )
                record["value"].update(
                    ceiling=row["ceiling"],
                    ceiling_holder=row["ceiling_holder"],
                    gating_field=stage["gating_field"],
                    stage_ownership=stage["ownership_mode"],
                    terminal=stage["is_terminal"],
                    proposal_field=proposal["field"] if proposal else None,
                    proposal_created_at=proposal["created_at"] if proposal else None,
                    proposal_revision=proposal["revision"] if proposal else 0,
                )
            elif record["kind"] == "conversation" and record["id"] == ticket["conversation_id"]:
                record["value"]["attention"]["current"]["signal"]["working"] = runtime_working
                retained_conversation = json.loads(json.dumps(record))
        if source_deleted and retained_conversation is not None:
            response["snapshot"]["records"].append(retained_conversation)
        authored = json.dumps(response["snapshot"]["records"], sort_keys=True)
        if force_publication or authored != last_records:
            source_revision += 1
            last_records = authored
        response["snapshot"]["revision"] = str(source_revision)
        navigation_response.__globals__["_navigation_revision"] = source_revision
        response["cursor"] = encode_cursor(response["snapshot"])
        return response

    def finish_read(route, result):
        original = route.request.url.split("/api/", 1)[1]
        if original.startswith("resource-body?"):
            key = parse_qs(original.split("?", 1)[1])["key"][0]
            if key.endswith("/workspace"):
                definition = next(worker for worker in manifest["worker_types"] if worker["worker_type"] == result["worker_type"])
                stage = next(row for row in definition["stages"] if row["id"] == result["stage"])
                fields = result["field_values"]
                result = {**result, "field_values": {name: value for name, value in fields.items() if name == stage["gating_field"]},
                    "field_summaries": {name: {"revision": value, "byte_count": len(value.encode())} for name, value in fields.items()}}
                result.pop("conversation_history", None)
            result = {"epoch": "fixture", "key": key, "revision": str(source_revision), "body": result}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    def respond(route):
        nonlocal navigation_proposal, session_revised, fail_field_save
        path = route.request.url.split("/api/", 1)[1]
        requests.append(path)
        if path.startswith("resource-body?"):
            path = parse_qs(path.split("?", 1)[1])["key"][0].removeprefix("/api/")
            requests.append(path)
        if path.startswith("tickets/") and path.endswith("/workspace"):
            path = path.removesuffix("/workspace")
        if path.startswith("tickets?"):
            path = "tickets/" + parse_qs(path.split("?", 1)[1])["id"][0]
        if path.split("?")[0] == "test/navigation-publication":
            assert route.request.method == "POST"
            result = review_snapshot(force_publication=True)
            if "?view=" in path:
                result = scoped_navigation_response(result, json.loads(parse_qs(path.split("?", 1)[1])["view"][0]))
        elif path.split("?")[0] in {"navigation/snapshot", "navigation/view/snapshot"}:
            if snapshot_unavailable:
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
            result = review_snapshot()
            if path.startswith("navigation/view/"):
                result = scoped_navigation_response(result, json.loads(parse_qs(path.split("?", 1)[1])["view"][0]))
        elif path.startswith("resource-versions?"):
            keys = json.loads(parse_qs(path.split("?", 1)[1])["keys"][0])
            result = {"epoch": "fixture", "resources": [{"key": key, "revision": None} for key in keys]}
        elif "/workspace/fields/" in path:
            identity, field = path.split("/")[1], path.rsplit("/", 1)[1]
            value = (ticket if identity == "t_review" else fixture_ticket(identity))["field_values"].get(field, "")
            result = {"ticket_id": identity, "field": field, "value": value, "revision": value}
        elif path.startswith("artifact-catalog/"):
            result = {"revision": "fixture", "directory": "", "offset": 0, "total": 0, "entries": []}
        elif path.startswith("conversation-history/ticket/"):
            identity = path.split("?", 1)[0].rsplit("/", 1)[1]
            history = (ticket if identity == "t_review" else fixture_ticket(identity)).get("conversation_history", [])
            result = {"revision": "fixture", "offset": 0, "total": len(history), "active_index": None,
                      "entries": [{**row, "label": str(row["created_at"])} for row in history]}
        elif path.startswith("conversation/conversations/") and "artifact-conversation" not in path:
            identity = path.split("/")[2]
            if path.split("?")[0].endswith("/events"):
                result = {"events": []}
            elif path.endswith("/owner-read"):
                result = {"owner_read_through_sequence": 0}
            else:
                result = {
                    "conversation_id": identity,
                    "backend_key": "codex",
                    "supports_steer": True,
                    "model": "gpt-test",
                    "reasoning_effort": None,
                    "workspace_folder": "/tmp",
                    "access": "full",
                    "role_text": None,
                    "identity_environment_variable_names": [],
                    "latest_sequence": 0,
                    "owner_read_through_sequence": 0,
                    "is_running": False,
                    "held_prompts": [],
                    "pending_permission_ask": None,
                    "pending_user_input": None,
                    "composer_catalog": [],
                }
        elif path == "tickets/t_review/conversation/send":
            result = {"conversation_id": "ticket-delivered-local", "fate": "started"}
        elif path == "tickets/t_review/conversation/reset":
            result = {}
        elif path.endswith("/conversation/start-values"):
            result = {"backend_key": "codex", "model": None, "reasoning_effort": None}
        elif path == "review":
            raise AssertionError("competing review summary query remains")
        elif path.startswith("navigation/events"):
            return
        elif path == "tickets/t_review":
            if hold_detail and route.request.method == "GET":
                held_details.append(route)
                return
            if route.request.method == "PATCH":
                body = route.request.post_data_json
                assert not {"project_id", "sprint_id", "manager_id"} & set(body)
                assert "guidance" not in body
                if "field_values" in body:
                    if fail_field_save:
                        fail_field_save = False
                        route.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"code": "unavailable", "message": "Save unavailable", "detail": None}}))
                        return
                    writes.append({"value": body["field_values"]})
                    ticket["field_values"].update(body["field_values"])
                    if hold_field_save:
                        held_field_saves.append(route)
                        return
                elif "ceiling" in body or "ceiling_holder" in body:
                    ceiling_writes.append(body)
                    if (
                        "ceiling_holder" in body
                        and body["ceiling_holder"] != ticket["ceiling_holder"]
                        and (ticket["pending_proposal"] is not None)
                    ):
                        ticket["pending_proposal"]["revision"] += 1
                    ticket.update(body)
                else:
                    placement_writes.append(body)
                    ticket.update(body)
            result = ticket
            if route.request.method == "PATCH" and "title" in route.request.post_data_json:
                fence = {
                    "version": 1,
                    "epoch": "fixture-navigation",
                    "scope": "fixture-navigation-scope",
                    "schema_version": SCHEMA_VERSION,
                    "semantics": SUPPORTED_SEMANTICS,
                    "revision": str(navigation_response.__globals__["_navigation_revision"] + 1),
                    "coverage": "complete",
                    "witnesses": ["a" * 64],
                    "signature": "synthetic",
                }
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"Panels-Navigation-Fence": json.dumps(fence)},
                    body=json.dumps(result),
                )
                return
        elif path in ["tickets/t_review/backlog", "tickets/t_review/resume"]:
            assert route.request.method == "POST"
            backlog_writes.append(
                {"action": path.rsplit("/", 1)[1], "body": route.request.post_data_json}
            )
            ticket["is_backlogged"] = path.endswith("/backlog")
            fence = {
                "version": 1,
                "epoch": "fixture-navigation",
                "scope": "fixture-navigation-scope",
                "schema_version": SCHEMA_VERSION,
                "semantics": SUPPORTED_SEMANTICS,
                "revision": str(navigation_response.__globals__["_navigation_revision"] + 1),
                "coverage": "complete",
                "witnesses": ["a" * 64],
                "signature": "synthetic",
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"Panels-Navigation-Fence": json.dumps(fence)},
                body=json.dumps(ticket),
            )
            return
        elif path == "tickets/t_open" and route.request.method == "PATCH":
            body = route.request.post_data_json
            ceiling_writes.append(body)
            result = {**fixture_ticket("t_open"), **body}
            fixture_overrides["t_open"] = result
        elif path == "tickets/t_personal/complete/outcome":
            assert route.request.method == "POST"
            body = route.request.post_data_json
            writes.append({"personal_outcome": body})
            result = {
                **fixture_ticket("t_personal"),
                "stage": "needs_consequences",
                "ceiling": "needs_consequences",
                "field_values": {"brief": "Context", "outcome": body["body"]},
                "pending_proposal": None,
                "ticket_status": "empty",
                "assigned": False,
            }
            fixture_overrides["t_personal"] = result
        elif path == "tickets/t_personal_pending":
            result = fixture_ticket("t_personal_pending")
        elif path == "tickets/t_artifacts":
            result = fixture_ticket("t_artifacts")
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
                            "text": "Conversation [Audio plan](/files/managers/si_artifacts/artifacts/plan.wav).",
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
            fixture_overrides["t_with_session"] = {
                **fixture_ticket("t_with_session"),
                "pending_proposal": None,
            }
            result = fixture_overrides["t_with_session"]
        elif path in [
            "tickets/t_personal",
            "tickets/t_kickoff",
            "tickets/t_with_session",
            "tickets/t_open",
            "tickets/t_done",
        ]:
            result = fixture_ticket(path.split("/")[1])
        elif path.endswith("/accept/success_condition"):
            assert route.request.method == "POST"
            approval = route.request.post_data_json
            if (
                server_revision is not None
                and approval["expected_proposal_revision"] != server_revision
            ):
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "code": "validation",
                                "message": "the visible proposal changed; read it again before accepting",
                                "detail": None,
                            }
                        }
                    ),
                )
                return
            approvals.append(approval)
            ticket.update(pending_proposal=None, ticket_status="empty")
            navigation_proposal = None
            result = ticket
        elif path.startswith("worker-types/") and path.endswith("/manifest"):
            worker_type = path.split("/")[1]
            matches = [row for row in manifest["worker_types"] if row["worker_type"] == worker_type]
            if not matches:
                route.fulfill(status=404, json={"error": {"code": "unknown_worker_type", "message": f"Unknown worker type: {worker_type}"}})
                return
            result = {"worker_types": matches}
        elif path == "worker-types":
            result = manifest
        elif path == "workers/coding":
            result = worker_detail
        elif path == "conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        finish_read(route, result)

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
                body="# Markdown plan\n\nRead [Nested image](/files/managers/si_artifacts/artifacts/image.svg).",
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
    expect(page.locator(".error-line")).to_have_count(1)
    expect(page.locator(".error-code")).to_have_text("unavailable")
    expect(page.locator(".quiet-line", has_text="Loading ticket")).to_have_count(0)
    snapshot_unavailable = False
    page.evaluate("window.__refresh()")
    expect(page.locator(".error-line")).to_have_count(0)
    assert page.evaluate("window.__badgeProof()") == dict(
        emptyHidden=True,
        emptyVisible=False,
        nonemptyHidden=False,
        positiveVisible=True,
        wrongCount=False,
        positiveHidden=False,
        absent=False,
    )
    expect(page.locator("[data-project-fact]")).to_have_text("One")
    expect(page.locator("[data-ticket-identity]")).to_have_text("One · Coding")
    assert page.evaluate("window.__wait()")["status"] == "included"
    snapshot_unavailable = True
    page.evaluate("window.__refresh()")
    expect(page.locator(".error-line")).to_have_count(1)
    expect(page.locator(".error-code")).to_have_text("unavailable")
    expect(page.locator(".ticket-title [contenteditable]")).to_have_text("Review ticket")
    snapshot_unavailable = False
    page.evaluate("window.__refresh()")
    expect(page.locator(".error-line")).to_have_count(0)
    expect(page.locator("[data-ticket-backlog-action]")).to_have_text("Backlog ticket")
    page.locator("[data-ticket-backlog-action]").click()
    page.wait_for_function("window.__commandWait==='pending'")
    assert backlog_writes == [{"action": "backlog", "body": {}}]
    expect(page.locator("[data-ticket-backlog-action]")).to_have_text("Backlog ticket")
    page.evaluate("window.__batch()")
    expect(page.locator("[data-ticket-backlogged]")).to_have_text("Backlogged")
    expect(page.locator("[data-ticket-backlog-action]")).to_have_text("Resume ticket")
    page.wait_for_function("window.__commandWait==='included'")
    page.locator("[data-ticket-backlog-action]").click()
    page.wait_for_function("window.__commandWait==='pending'")
    assert backlog_writes[-1] == {"action": "resume", "body": {}}
    page.evaluate(
        "() => {\n      const control=document.querySelector('[data-ticket-lifecycle]');\n      window.__heldLifecycle={control,parent:control.parentNode,next:control.nextSibling};control.remove();\n    }"
    )
    page.evaluate("window.__batch()")
    assert page.evaluate("window.__wait()")["status"] == "unknown", (
        "detached lifecycle controls cannot certify section paint"
    )
    page.evaluate(
        "() => {\n      const held=window.__heldLifecycle;held.parent.insertBefore(held.control,held.next);\n    }"
    )
    page.evaluate("window.__batch()")
    expect(page.locator("[data-ticket-backlogged]")).to_have_count(0)
    expect(page.locator("[data-ticket-backlog-action]")).to_have_text("Backlog ticket")
    page.wait_for_function("window.__commandWait==='included'")
    page.locator("[data-stage-fold]").evaluate("(element)=>element.open=true")
    page.locator("[data-stage-fold] [data-field=brief]").evaluate("(element)=>element.open=true")
    original = dict(ticket)
    hold_detail = True
    ticket.update(
        title="Canonical masthead",
        worker_type="personal",
        stage="needs_consequences",
        ceiling="needs_consequences",
        ticket_status="empty",
        field_values={"brief": "Request", "outcome": "Canonical user outcome"},
        pending_proposal=None,
        conversation_id="masthead-current",
        conversation_signal={"working": True, "message_for_owner": True},
    )
    parent_project = {"id": "project_current", "name": "Current Project"}
    runtime_working = True
    page.evaluate("window.__batch()")
    expect(page.locator(".ticket-title [contenteditable]")).to_have_text("Canonical masthead")
    expect(page.locator("[data-ticket-identity]")).to_have_text("Current Project · Personal")
    expect(page.locator(".ticket-title [data-conversation-id]")).to_have_attribute(
        "data-conversation-id", "masthead-current"
    )
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_attribute(
        "data-conversation-signal", "message"
    )
    expect(page.locator("[data-approval-block] [contenteditable]")).to_have_text("A result")
    assert page.evaluate("window.__wait()")["status"] == "unknown", (
        "a new header cannot certify the retained old section"
    )
    expect(page.locator("[data-ticket-section]")).to_have_attribute(
        "data-section-stage", "needs_success_condition"
    )
    expect(page.locator("[data-worker-live-card] h2")).to_contain_text("Success")
    assert page.locator("[data-stage-fold]").evaluate("(element)=>element.open")
    assert page.locator("[data-stage-fold] [data-field=brief]").evaluate("(element)=>element.open")
    page.locator("[data-reviewer-heading]").click()
    page.locator("[data-ceiling-picker-choice=owner]").click()
    expect(page.locator("[data-approval-block] .error-line")).to_contain_text(
        "the Ticket changed; read it again before this action"
    )
    expect(page.locator("[data-approval-block] .error-code")).to_have_text("validation")
    assert ceiling_writes == [] and approvals == []
    for _ in range(100):
        if held_details:
            break
        page.wait_for_timeout(10)
    assert held_details
    for stage in manifest["worker_types"][1]["stages"]:
        if stage["id"] == "needs_consequences":
            stage["gating_field"] = "outcome"
    page.evaluate("window.__manifest()")
    hold_detail = False
    for route in held_details:
        if route.request.failure is None:
            finish_read(route, ticket)
    held_details.clear()
    expect(page.locator("[data-ticket-section]")).to_have_attribute(
        "data-section-stage", "needs_success_condition"
    )
    expect(page.locator("[data-approval-block] [contenteditable]")).to_have_text("A result")
    for stage in manifest["worker_types"][1]["stages"]:
        if stage["id"] == "needs_consequences":
            stage["gating_field"] = "consequences"
    page.evaluate("window.__manifest()")
    expect(page.locator("[data-ticket-section]")).to_have_attribute(
        "data-section-stage", "needs_consequences"
    )
    expect(page.locator("[data-worker-live-card] h2")).to_have_text("Currently working on Closeout")
    expect(page.locator("[data-approval-block]")).to_have_count(0)
    assert page.locator("[data-stage-fold]").evaluate("(element)=>element.open")
    assert page.locator("[data-stage-fold] [data-field=brief]").evaluate("(element)=>element.open")
    assert page.evaluate("window.__wait()")["status"] == "included"
    ticket["conversation_signal"]["message_for_owner"] = False
    page.evaluate("window.__batch()")
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_attribute(
        "data-conversation-signal", "working"
    )
    source_deleted = True
    page.evaluate("window.__batch()")
    expect(page.locator(".ticket-title [contenteditable]")).to_have_text("Canonical masthead")
    assert page.evaluate("window.__wait()")["status"] == "unknown", (
        "retained deleted header cannot certify current paint"
    )
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_count(0)
    snapshot_unavailable = True
    page.evaluate("window.__refresh()")
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_count(0)
    expect(page.locator("[data-worker-live-card] h2")).to_have_text("Waiting at Closeout")
    expect(page.locator(".ticket-title [contenteditable]")).to_have_text("Canonical masthead")
    retained_conversation["value"]["attention"]["current"]["signal"]["message_for_owner"] = True
    retained_conversation["value"]["attention"]["durable"]["message_for_owner"] = True
    retained_conversation["value"]["latest_sequence"] = 21
    snapshot_unavailable = False
    page.evaluate("window.__refresh()")
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_count(0)
    snapshot_unavailable = True
    page.evaluate("window.__refresh()")
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_count(0)
    # A deleted owner has no address that authorizes an orphan conversation signal.
    expect(page.locator(".ticket-title [data-conversation-id]")).to_have_count(0)
    retained_conversation = None
    snapshot_unavailable = False
    page.evaluate("window.__refresh()")
    expect(page.locator(".ticket-title [data-conversation-signal]")).to_have_count(0)
    assert not page.evaluate(
        "window.__navigation.selectors.effective.conversations.has('masthead-current')"
    )
    source_deleted = False
    ticket.clear()
    ticket.update(original)
    hold_detail = False
    for route in held_details:
        if route.request.failure is None:
            finish_read(route, ticket)
    held_details.clear()
    parent_project = {"id": "project_one", "name": "One"}
    runtime_working = False
    page.evaluate("window.__batch()")
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
    expect(page.locator("body")).not_to_contain_text("Retired guidance sentinel")
    assert page.get_by_text("Old unapproved draft").count() == 0
    expect(page.locator("[data-send-back]")).to_be_disabled()
    expect(page.locator("[data-return-unavailable]")).to_contain_text(
        "No existing Worker conversation"
    )
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
    page.evaluate("window.__batch()")
    assert approvals == []
    page.evaluate("window.__showTicket('t_open')")
    page.locator('[data-ticket-id="t_open"]').wait_for()
    leash = page.locator("[data-leash]")
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
    page.evaluate("window.__batch()")
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
    page.evaluate("window.__batch()")
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
        == "Approved until Done, then User reviews"
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
            "() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1"
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
            "() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1"
        )
        leash.get_by_role("listbox").press("Escape")

    assert_ticket_layout(1280)
    assert_ticket_layout(390)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.evaluate("window.__showTicket('t_review')")
    title_editor = page.locator(".ticket-title [contenteditable]")
    title_editor.fill("Local masthead edit")
    page.evaluate("window.__batch()")
    assert page.evaluate("window.__wait()")["status"] == "unknown"
    title_editor.press("Enter")
    page.wait_for_function("window.__commandWait==='pending'")
    assert len(page.evaluate("window.__commandObservers.map(observer=>observer.id)")) == 1
    page.evaluate("window.__batch()")
    page.wait_for_function("window.__commandWait==='included'")
    expect(title_editor).to_have_text("Local masthead edit")
    composer = page.locator("[data-conversation-input]")
    composer.fill("Ticket pointer continuation")
    composer.press("Enter")
    page.wait_for_function(
        "() => [...window.__openStreams].some(url=>url.includes('ticket-delivered-local'))"
    )
    assert "conversation/conversations/ticket-delivered-local" in requests
    expect(page.locator("[data-conversation-pane]")).to_have_attribute(
        "data-conversation-id", "ticket-delivered-local"
    )
    page.evaluate("window.__detail()")
    page.evaluate("window.__batch()")
    assert page.evaluate(
        "[...window.__openStreams].some(url=>url.includes('ticket-delivered-local'))"
    )
    ticket["conversation_id"] = "ticket-canonical-next"
    page.evaluate("window.__batch()")
    page.wait_for_function(
        "() => [...window.__openStreams].some(url=>url.includes('ticket-canonical-next'))"
    )
    composer.click()
    page.locator(".chat-overflow > .chat-overflow-btn").click()
    page.locator("[data-conversation-new-arm]").click()
    page.locator("[data-conversation-new-confirm]").click()
    page.wait_for_function(
        "() => ![...window.__openStreams].some(url=>url.includes('ticket-canonical-next'))"
    )
    page.evaluate("window.__detail()")
    page.evaluate("window.__batch()")
    assert not page.evaluate(
        "[...window.__openStreams].some(url=>url.includes('ticket-canonical-next'))"
    ), "unchanged canonical pointer cannot undo local reset"
    ticket["conversation_id"] = None
    page.evaluate("window.__batch()")
    page.locator('[data-ticket-id="t_review"]').wait_for()
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
    assert writes == [{"value": {"brief": "Edited saved kickoff"}}]
    # Closing a lazy stage must preserve failed and pending edits across remounts.
    expect(saved).to_have_attribute("contenteditable", "true")
    fail_field_save = True
    saved.click()
    saved.fill("Draft survives a closed stage")
    saved.press("Tab")
    expect(page.locator('[data-field="brief"] .error-line')).to_contain_text("Save unavailable")
    page.locator("[data-stage-fold]").evaluate("node => { node.open = false; }")
    expect(saved).to_have_count(0)
    page.locator("[data-stage-fold]").evaluate("node => { node.open = true; }")
    page.locator('[data-stage-fold] [data-field="brief"]').evaluate("node => { node.open = true; }")
    expect(saved).to_have_text("Draft survives a closed stage")
    expect(page.locator('[data-field="brief"] .error-line')).to_contain_text("Save unavailable")
    hold_field_save = True
    saved.click()
    saved.press("Tab")
    page.wait_for_timeout(50)
    assert len(held_field_saves) == 1
    page.locator("[data-stage-fold]").evaluate("node => { node.open = false; }")
    expect(saved).to_have_count(0)
    page.locator("[data-stage-fold]").evaluate("node => { node.open = true; }")
    page.locator('[data-stage-fold] [data-field="brief"]').evaluate("node => { node.open = true; }")
    expect(saved).to_have_attribute("contenteditable", "false")
    hold_field_save = False
    finish_read(held_field_saves.pop(), ticket)
    expect(saved).to_have_attribute("contenteditable", "true")
    expect(page.locator('[data-field="brief"] .error-line')).to_have_count(0)
    expect(saved).to_have_text("Draft survives a closed stage")
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
    for _ in range(100):
        if revisions:
            break
        page.wait_for_timeout(10)
    assert revisions == [{"message": exact_revision}]
    page.evaluate("window.__batch()")
    expect(
        page.locator('[data-ticket-id="t_with_session"] [data-approval-block][data-mode="pending"]')
    ).to_have_count(0)
    assert revisions == [{"message": exact_revision}]
    page.evaluate("window.__showTicket('t_done')")
    page.locator('[data-ticket-id="t_done"]').wait_for()
    expect(page.locator("[data-ticket-backlog-action], [data-ticket-backlogged]")).to_have_count(0)
    assert page.evaluate("window.__wait()")["status"] == "included"
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
    ticket["conversation_id"] = "review-worker"
    ticket["ceiling_holder"] = {"kind": "owner", "id": "owner"}
    page.evaluate("window.__refresh()")
    page.evaluate("window.__showReview()")
    review = page.locator('[data-review-card][data-ticket-id="t_review"]')
    expect(review).to_be_visible()
    expect(review.locator("[data-ticket-guidance]")).to_have_count(0)
    expect(review).not_to_contain_text("Retired guidance sentinel")
    if os.environ.get("PANELS_REVIEW_SCREENSHOT"):
        page.screenshot(path=os.environ["PANELS_REVIEW_SCREENSHOT"], full_page=True, animations="disabled")
    # Review shows what the author wrote. The edit on the Ticket page was never saved
    # over it, because an edited proposal is approved as the edit or it is nothing.
    expect(page.locator('[data-approval-block][data-field="success_condition"]')).to_contain_text(
        "A result"
    )
    assert page.locator("[data-accept]").count() == 1
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
    expect(approve_row.get_by_role("option", name="Manager", exact=True)).to_have_count(1)
    approve_row.get_by_role("listbox").press("Escape")
    retained_editor = page.locator("[data-approval-block] [data-edit]")
    retained_editor.click()
    retained_editor.fill("Retained local draft")
    retained_editor.press("Tab")
    page.evaluate("window.__batch()")
    expect(retained_editor).to_contain_text("Retained local draft")
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
    assert approvals[0]["expected_proposal_revision"] == 2
    expect(page.locator('[data-review-card][data-ticket-id="t_review"]')).to_have_count(0)
    remaining = page.locator('[data-review-card][data-ticket-id="t_personal_pending"]')
    expect(remaining).to_have_count(1)
    remaining.locator("[data-skip]").click()
    expect(page.locator("[data-review-card]")).to_have_count(0)
    ticket.update(
        ticket_status="awaiting_approval",
        pending_proposal={
            "field": "success_condition",
            "body": "A result",
            "proposed_by": "agent",
            "created_at": 2,
            "revision": 2,
        },
    )
    page.evaluate("window.__refresh()")
    expect(page.locator("[data-review-card]")).to_have_count(1)
    navigation_proposal = dict(ticket["pending_proposal"])
    ticket["title"] = "Canonical Review title"
    page.evaluate("window.__refresh()")
    try:
        expect(page.locator(".review-ticket-title [contenteditable]")).to_have_text(
            "Canonical Review title"
        )
    except AssertionError:
        print(
            "REVIEW_TITLE_DIAGNOSTIC",
            json.dumps(
                page.evaluate(
                    "() => ({source:window.__navigation.state.effective?.publication.records.filter(row=>row.kind==='ticket'), cards:[...document.querySelectorAll('[data-review-card]')].map(node=>({id:node.dataset.ticketId,title:node.querySelector('.review-ticket-title')?.textContent}))})"
                )
            ),
        )
        raise
    for stage in manifest["worker_types"][0]["stages"]:
        if stage["id"] == "needs_success_condition":
            stage["gating_field"] = "plan"
    page.evaluate("window.__manifest()")
    expect(page.locator("[data-review-card]")).to_have_count(1)
    navigation_proposal = {
        **ticket["pending_proposal"],
        "created_at": 3,
        "revision": 3,
        "body": "Replacement body",
    }
    hold_detail = True
    page.evaluate("window.__refresh()")
    expect(page.locator("[data-review-card]")).to_have_count(0)
    for _ in range(100):
        if held_details:
            break
        page.wait_for_timeout(10)
    assert held_details
    finish_read(held_details.pop(0), ticket)
    expect(page.locator("[data-review-card]")).to_have_count(0)
    ticket["pending_proposal"] = navigation_proposal
    hold_detail = False
    for route in held_details:
        finish_read(route, ticket)
    held_details.clear()
    expect(page.locator("[data-approval-block]")).to_contain_text("Replacement body")
    expect(page.locator("[data-approval-block]")).not_to_contain_text("A result")
    proof = page.evaluate("window.__wait()")
    assert proof["status"] == "included", proof
    server_revision = 4
    approve_row = page.locator("[data-approval-block]")
    approve_row.locator("[data-accept]").click()
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    expect(page.locator(".error-message")).to_contain_text("visible proposal changed")
    assert len(approvals) == 1
    server_revision = 3
    proposal_editor = approve_row.locator("[data-edit]")
    proposal_editor.click()
    proposal_editor.fill("Edited replacement body")
    proposal_editor.press("Tab")
    approve_row.locator("[data-accept]").click()
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    for _ in range(100):
        if len(approvals) == 2:
            break
        page.wait_for_timeout(10)
    assert approvals[1]["expected_proposal_revision"] == 3
    assert approvals[1]["edited_body"] == "Edited replacement body"
    expect(page.locator("[data-review-card]")).to_have_count(0)
    runtime_working = True
    page.evaluate("window.__refresh()")
    expect(page.locator("[data-shell-presence]")).to_have_text("1 working")
    expect(page.locator(".review-empty-meta")).to_have_text("1 agent in progress")
    snapshot_unavailable = True
    # A failed replacement read does not revoke the still-valid active stream.
    # Exercise actual active-stream loss for this fail-closed runtime assertion.
    page.evaluate("window.__navigationSource.onerror?.(); window.__refresh()")
    expect(page.locator("[data-shell-presence]")).to_have_count(0)
    assert "working" not in page.locator("[data-shell-status]").get_attribute("aria-label")
    expect(page.locator("[data-review-empty]")).to_contain_text(
        "There is nothing to review right now."
    )
    expect(page.locator(".review-empty-meta")).to_have_count(0)
    snapshot_unavailable = False
    runtime_working = False
    page.evaluate("window.__refresh()")
    expect(page.locator("[data-shell-presence]")).to_have_text("0 working")
    expect(page.locator(".review-empty-meta")).to_have_text("0 agents in progress")
    artifact_started = time.monotonic()
    # End the deliberate incompatible-manifest scenario before the next Ticket visit.
    manifest["worker_types"] = json.loads(json.dumps(canonical_worker_types))
    page.evaluate("window.__manifest()")
    page.evaluate("window.__showTicket('t_artifacts')")
    page.locator('[data-ticket-id="t_artifacts"]').wait_for()
    page.locator("[data-conversation-rest-bar], [data-conversation-input]").first.wait_for()
    rest = page.locator("[data-conversation-rest-bar]")
    if rest.count() and rest.is_visible():
        rest.click()
    page.locator("[data-conversation-input]").click()
    expand = page.locator("[data-conversation-expand]")
    if expand.count() and expand.is_visible():
        expand.click()
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
    assert html_link.get_attribute("href").startswith("/files/managers/")
    original_url = page.url

    def click_was_claimed(link, **options):
        return link.evaluate(
            "(anchor, options) => {\n          let claimed;\n          document.addEventListener('click', event => {\n            claimed = event.defaultPrevented;\n            event.preventDefault();\n          }, {once:true});\n          anchor.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, ...options}));\n          return claimed;\n        }",
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
    # The opened conversation owns the full pane. Return to the document before a real click.
    for _ in range(2):
        if page.locator('[data-conversation-pane][data-conversation-state="rest"]').count():
            break
        page.keyboard.press("Escape")
    expect(page.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-state", "rest")
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
        "src", "/files/managers/si_artifacts/artifacts/plan.wav"
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
    pass
