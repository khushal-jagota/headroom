#!/usr/bin/env python3
"""One local HTTP stub and the existing UI test host; never operates simulator lifecycle."""

import copy
import hashlib
import http.server
import json
import os
import plistlib
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DESIGN_REVIEW = os.environ.get("PANELS_DESIGN_REVIEW") == "1"
ARTIFACT_LINKS_PROOF = os.environ.get("PANELS_ARTIFACT_LINKS_PROOF") == "1"
REVIEW_COMPLETION = os.environ.get("PANELS_REVIEW_COMPLETION") == "1"
REACHABILITY_SMOKE = os.environ.get("PANELS_REACHABILITY_SMOKE") == "1"
UPDATES_SMOKE = os.environ.get("PANELS_UPDATES_SMOKE") == "1"
DEVICE = os.environ.get("PANELS_OFFLINE_DEVICE") or (
    "566D051B-B6BB-4046-9038-66C05CCC2B96"
    if REVIEW_COMPLETION
    else "5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6"
)
LANE_LOCK = (
    "/tmp/panels-"
    + os.environ.get("PANELS_OFFLINE_LANE", "PanelsLaneB" if REVIEW_COMPLETION else "PanelsLaneA")
    + ".lock"
)
BUNDLE = os.environ.get("PANELS_OFFLINE_BUNDLE", "app.panels.ios.pios-offline")
TABLE_SCROLL_SMOKE = os.environ.get("PANELS_TABLE_SCROLL_SMOKE") == "1"
GOALS_SMOKE = os.environ.get("PANELS_GOALS_SMOKE") == "1" or TABLE_SCROLL_SMOKE
REPLACEMENT_SMOKE = os.environ.get("PANELS_REPLACEMENT_SMOKE") == "1"
REVIEW_MESSAGES = (
    os.environ.get("PANELS_REVIEW_MESSAGES") == "1" or GOALS_SMOKE or REPLACEMENT_SMOKE
)


def main() -> int:
    server_only = "--server-only" in sys.argv
    review = "--review" in sys.argv
    if review and not server_only and "/" not in os.environ.get("PANELS_OFFLINE_TEST", ""):
        raise SystemExit("--review requires one explicit class/method in PANELS_OFFLINE_TEST")
    if not server_only and "--locked" not in sys.argv:
        return subprocess.call(
            ["lockf", LANE_LOCK, sys.executable, __file__, *sys.argv[1:], "--locked"]
        )
    output = Path(os.environ["PANELS_OFFLINE_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not server_only:
        devices = json.loads(
            subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"])
        )
        assert any(
            d["udid"] == DEVICE and d["state"] == "Booted"
            for group in devices["devices"].values()
            for d in group
        )
    base_ticket = json.loads((ROOT / "ios/PanelsTests/Fixtures/ticket-full.json").read_text())
    tickets = {}
    for index in range(1, 4):
        ticket = copy.deepcopy(base_ticket)
        ticket.update(
            id=f"offline-{index}",
            title=["Approve offline", "Send back offline", "Record offline"][index - 1],
            recap="Offline recovery proof",
            conversation_id="stub",
            field_values={},
            field_summaries={},
            artifacts=[],
        )
        ticket["pending_proposal"].update(
            body="A short proposal to check on the train.", revision=1, created_at=index
        )
        tickets[ticket["id"]] = ticket
    manifest = {
        "worker_types": [
            {
                "worker_type": base_ticket["worker_type"],
                "label": "Product design",
                "stages": [
                    {"id": base_ticket["stage"], "label": "Current", "is_terminal": False},
                    {"id": "done", "label": "Done", "is_terminal": True},
                ],
                "advance": {base_ticket["stage"]: "done"},
                "fields": [{"id": "wireframe", "label": "Proposal"}],
                "ceiling_range": ["done"],
            }
        ]
    }
    if DESIGN_REVIEW:
        workflow = dict(
            worker_type="coding",
            label="Coding",
            stages=[
                dict(id="needs_brief", label="Brief", is_terminal=False, gating_field="brief"),
                dict(
                    id="needs_what_changes", label="What Changes", is_terminal=False,
                    gating_field="what_changes",
                ),
                dict(
                    id="needs_implementation", label="Implementation", is_terminal=False,
                    gating_field="implementation",
                ),
                dict(
                    id="needs_consequences", label="Consequences", is_terminal=False,
                    gating_field="consequences",
                ),
                dict(id="done", label="Done", is_terminal=True),
            ],
            advance=dict(
                needs_brief="needs_what_changes",
                needs_what_changes="needs_implementation",
                needs_implementation="needs_consequences",
                needs_consequences="done",
            ),
            fields=[
                dict(id=x, label=y)
                for x, y in [
                    ("brief", "Brief"), ("what_changes", "What Changes"),
                    ("implementation", "Implementation"), ("consequences", "Consequences"),
                ]
            ],
            ceiling_range=[
                "needs_brief", "needs_what_changes", "needs_implementation",
                "needs_consequences", "done",
            ],
        )
        manifest = {"worker_types": [workflow]}
        for ticket in tickets.values():
            ticket.update(
                worker_type="coding", workflow=workflow, stage="needs_what_changes",
                ceiling="needs_what_changes", manager_id=None,
                title="Match the iPhone app to the web",
                recap=(
                    "The approved design brings the web sidebar cards, focused work and one "
                    "decision area to the phone. Every proposal keeps its own note until its "
                    "decision lands. The existing protected route carries each decision."
                ),
                field_values={"brief": "Match the phone to the approved web design."},
                field_approvals={
                    "brief": dict(
                        method="owner", principal=dict(kind="owner", id="owner"),
                        display_label="Approved by User",
                    )
                },
            )
            ticket["pending_proposal"].update(
                field="what_changes",
                body=(
                    "Make the work that needs your attention easy to read.\n\n"
                    "## One decision area\n\nRead the proposal, add an optional note, then "
                    "choose **Approve**. Use `Send it back` with a note when the work needs "
                    "a revision.\n\n| Surface | Change |\n| --- | --- |\n"
                    "| Workspace | Focused sidebar cards |\n| Review | Pinned decisions |\n\n"
                    "```swift\nlet focus = true\n```"
                ),
            )

    # Goals reuse this host and the exact authored chart catalogue from the web.
    catalogue_source = (ROOT / "ios/scripts/goal-component-examples.ts").read_text()
    catalogue = (
        json.loads(
            catalogue_source.split(
                "export const goalComponentExamples: GoalComponentExample[] = ", 1
            )[1]
            .strip()
            .removesuffix(";")
        )
        if GOALS_SMOKE
        else []
    )
    words_chart = dict(
        type="chart",
        id="words",
        placement="primary",
        kind="bar",
        title="Words written",
        unit="words",
        measure_id="words",
        target=1000,
        manual=dict(model="daily_quota", increments=[100, 250]),
    )
    checklist = dict(
        type="checklist",
        id="release-checklist",
        title="Before release",
        items=[
            dict(
                id="first",
                label="Check the build",
                state="open",
                detail="Read the evidence before release.",
                action="Checked",
                deadline="2026-11-04",
            ),
            dict(
                id="next",
                label="Prepare release notes",
                state="blocked",
                detail="Explain the change.",
            ),
        ],
    )

    def goal_record(
        key: str,
        title: str,
        kind: str,
        primary: object = None,
        blocks: object = None,
        selected: object = None,
    ) -> dict[str, Any]:
        return dict(
            id=key,
            project_id="project_personal",
            title=title,
            why="Keep the next step clear.",
            kind=kind,
            active=True,
            completed=False,
            primary_chart=primary,
            primary_checklist_block_id=selected,
            deadline="2026-11-04",
            page=dict(
                kind="headroom.page", version=1, title=title, summary="", blocks=blocks or []
            ),
            revision=1,
            created_at=1,
            updated_at=1,
            employee_backend="codex",
            employee_launch_model="sol",
            employee_launch_reasoning_effort=None,
            holder_kind="chief",
            holder_id=None,
            status="attention",
            status_date="2026-11-04",
            next_refresh_at=time.time() + 3600,
        )

    kit_blocks = [copy.deepcopy(item["samples"][0]["block"]) for item in catalogue]
    kit_blocks += [
        dict(
            type="text", id="prose", text="Read the [release evidence](/files/goals/kit/proof.md)."
        ),
        dict(
            type="grid",
            id="grid",
            blocks=[
                dict(
                    type="metrics",
                    id="metric",
                    items=[dict(label="Activation", value="35%", change="+2 pp", tone="good")],
                ),
                dict(
                    type="properties", id="properties", items=[dict(label="Owner", value="Chief")]
                ),
            ],
        ),
        dict(
            type="section",
            id="section",
            title="Next steps",
            summary="Use the same page kit.",
            blocks=[
                dict(
                    type="callout",
                    id="callout",
                    tone="attention",
                    title="Check the evidence",
                    text="Source history is partial.",
                )
            ],
        ),
        dict(
            type="segmented",
            id="views",
            views=[
                dict(
                    id="plan",
                    label="Plan",
                    blocks=[
                        dict(
                            type="rows",
                            id="rows",
                            items=[
                                dict(
                                    title="Investigate",
                                    detail="Read the report",
                                    status="In progress",
                                    tone="attention",
                                    meta="Today",
                                )
                            ],
                        )
                    ],
                ),
                dict(
                    id="status",
                    label="Status",
                    blocks=[dict(type="status", id="status", label="Ready", tone="good")],
                ),
            ],
        ),
        dict(
            type="chart",
            id="number",
            kind="number",
            placement="primary",
            title="Reading only",
            target=10,
            series=[dict(name="Current", points=[dict(label="Today", value=8)])],
        ),
    ]
    goals = {
        g["id"]: g
        for g in [
            goal_record("words", "Write 1,000 words", "quantitative", words_chart),
            goal_record(
                "release", "Release the app", "binary", blocks=[checklist], selected=checklist["id"]
            ),
            goal_record("kit", "Explore the page kit", "binary", blocks=kit_blocks),
        ]
    }
    if TABLE_SCROLL_SMOKE:
        tickets["offline-1"]["pending_proposal"]["body"] = (
            "[Open table evidence](/files/tickets/offline-1/table-proof.md)"
        )
    table_proof = dict(
        type="table", id="scroll-proof", caption="Table scroll proof",
        columns=["Track", "Owner", "Reading", "Evidence"],
        rows=[
            ["First shared row", "Chief of Staff", "120",
             "A wide evidence cell that stays in its column"],
            ["Second shared row", "Release manager", "240",
             "Another wide evidence cell with its original row"],
        ],
        cell_tones=[
            ["neutral", "good", "good", "neutral"],
            ["neutral", "attention", "good", "neutral"],
        ],
    )
    manual: dict[str, Any] = dict(
        measure_id="words",
        definition_revision=1,
        write_revision=1,
        timezone="UTC",
        current_date="2026-11-04",
        date="2026-11-04",
        observation=dict(value=400),
        latest_observation=dict(value=400),
    )
    manual_receipts: dict[str, Any] = {}
    decision_receipts: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {
        "offline": False,
        "slow": (
            not REVIEW_COMPLETION and not GOALS_SMOKE
            and not REACHABILITY_SMOKE and not DESIGN_REVIEW
        ),
        "writes": [],
        "messages": [],
        "recording": None,
        "goal_patch_attempts": 0,
        "chief_conversation": "stub",
        "tail_requests": [],
    }
    state.update(
        read_through=3 if TABLE_SCROLL_SMOKE else 1,
        historical_read=TABLE_SCROLL_SMOKE,
        new_message=False,
        refused=False,
        review_owner="chief",
    )
    if UPDATES_SMOKE:
        state.update(slow=False, update_phase="offer", update_retry_count=0, update_reads=0)

    def update_status() -> dict[str, Any]:
        # One-off native review data only. These are the existing update API fields.
        phase = state.get("update_phase", "offer")
        offer = phase in ("offer", "refused")
        updating = phase in ("updating", "restart")
        failed = phase == "failed"
        snapshot = dict(stops_agents=True, agents=["Release reviewer"], machine_ids=["machine-active"])
        def machine(identity: str, name: str, row_state: str, present: bool = True,
                    attempt: str | None = None, retry: bool = False) -> dict[str, Any]:
            return dict(machine_id=identity, name=name, platform="darwin-arm64", present=present,
                        build_id="old-build", target_build_id="new-build", changed=row_state != "current",
                        state=row_state, attempt_id=attempt, request_id="request-" + identity if attempt else None,
                        detail="The machine kept its previous version." if row_state == "failed" else None,
                        can_retry=retry)
        retried = state["update_retry_count"] > 1
        machines = [
            machine("machine-active", "Office", "updating" if updating else "current", attempt="server-attempt" if updating else None),
            machine("machine-away", "Travel", "waiting", present=False, attempt="previous-attempt"),
            machine("machine-failed", "Studio", "updating" if retried else "failed", attempt="previous-attempt", retry=not retried),
            machine("machine-current", "Home", "current"),
            machine("machine-unavailable", "Workshop", "unavailable"),
        ]
        available = copy.deepcopy(machines)
        for row in available:
            row.update(attempt_id=None, request_id=None, can_retry=False)
            if row["machine_id"] == "machine-active": row["state"] = "available"
        attempt = dict(attempt_id="server-attempt", from_sha="running-release", to_sha="offered-release",
                       mode="now", state="installing" if updating else "failed" if failed else "done",
                       detail="The server kept its previous version." if failed else None,
                       deadline_at=state.get("update_deadline", "2099-01-01T00:00:00Z") if updating else None,
                       interruption=snapshot)
        return dict(
            state="available" if offer else "updating" if updating else "failed" if failed else "up_to_date",
            allowed_actions=["update"] if offer else ["retry"] if failed else [],
            activity="busy", available=dict(app_sha="offered-release", compatibility=dict(
                ios=dict(minimum_build=10), desktop_shell=dict(minimum_bridge_version=3),
                machine=dict(minimum_protocol_version=3, maximum_protocol_version=5), outbox_format=2
            )) if offer else None,
            attempt=attempt, app_sha="running-release", detail=attempt["detail"],
            deadline_at=attempt["deadline_at"], mac_details=None,
            machines=machines, available_machines=available if offer else [],
            # Progress must use the retained press snapshot, not a later candidate.
            interruption=snapshot if offer else dict(stops_agents=False, agents=[], machine_ids=[]),
            client_versions=[dict(kind="ios", device_id="review-phone", version="0.1", build=9),
                dict(kind="desktop", device_id="review-desktop", version="1", bridge_version=2),
                dict(kind="machine", device_id="machine-unavailable", version="1", protocol_version=2, outbox_format=1)]
        )

    if REVIEW_MESSAGES:
        state["slow"] = False
        tickets = {"offline-1": tickets["offline-1"]}
        tickets["offline-1"]["pending_proposal"]["created_at"] = 2
    if ARTIFACT_LINKS_PROOF:
        state["slow"] = False
        state["artifact_reads"] = {"text": 0, "photo": 0, "html": 0}
        tickets = {"offline-1": tickets["offline-1"]}
        tickets["offline-1"]["title"] = "Artifact links stay inline"
        tickets["offline-1"]["pending_proposal"]["body"] = (
            "Read [Migration verification]"
            "(/files/tickets/t_8rmd1ebk/artifacts/migration-verification.txt) before approval."
            "\n\n[HTML preview](/files/tickets/t_8rmd1ebk/artifacts/merge-preview.html)"
            "\n\n[Review photo](/files/tickets/t_8rmd1ebk/artifacts/review-photo.png)"
        )
    lock = threading.RLock()

    def native_control_allowed() -> bool:
        if not server_only:
            return True
        try:
            return os.environ.get("PANELS_OWNED_UDID") == DEVICE and time.time() < float(
                os.environ["PANELS_WORK_END"]
            )
        except (KeyError, ValueError):
            return False

    def saved_replacement_reference() -> dict[str, Any]:
        """Read the owned test app's actual saved view, without modifying its store."""
        device = os.environ.get("PANELS_REPLACEMENT_DEVICE_ID")
        if not device or "PANELS_OFFLINE_BUNDLE" not in os.environ:
            raise ValueError("Replacement disk proof requires an explicit test device and bundle")
        containers = (
            Path.home()
            / "Library/Developer/CoreSimulator/Devices"
            / device
            / "data/Containers/Data/Application"
        )
        matches = [
            path.parent
            for path in containers.glob("*/.com.apple.mobile_container_manager.metadata.plist")
            if plistlib.loads(path.read_bytes()).get("MCMMetadataIdentifier") == BUNDLE
        ]
        if len(matches) != 1:
            raise ValueError("The owned replacement app has no unique data container")
        store = matches[0] / "Library/Application Support/PanelsPhone/phone.store"
        with sqlite3.connect("file:" + str(store) + "?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT ZKEY, ZPAYLOAD FROM ZLOCALRECORD WHERE ZKIND = 'cache' AND ZSERVER = ?",
                (url,),
            ).fetchall()
        saved = {}
        for key, payload in rows:
            if "/api/navigation/view/snapshot#" not in key:
                continue
            snapshot = json.loads(payload)
            chief = next(
                (
                    record["value"]
                    for record in snapshot["content"]["records"]
                    if record["kind"] == "chief"
                ),
                None,
            )
            if chief is not None:
                saved[key.rsplit("#", 1)[-1]] = chief.get("conversation_id")
        if saved.get("latest") != "stub" or saved.get("workspace") != "stub":
            raise ValueError("The app did not persist its old Chief reference before relaunch")
        proof = {
            "device": device,
            "bundle": BUNDLE,
            "saved_chief_references": saved,
            "store_open_mode": "read-only",
        }
        (output / "replacement-saved-reference.json").write_text(json.dumps(proof, indent=2))
        return proof

    def stop_clip() -> None:
        recording = state["recording"]
        if recording:
            recording.send_signal(signal.SIGINT)
            recording.communicate(timeout=15)
            state["recording"] = None

    def select_workspace_fixture() -> None:
        # Explicit selection leaves all other DESIGN_REVIEW journeys unchanged.
        if not state.get("workspace_fixture"):
            state["workspace_baseline"] = (copy.deepcopy(tickets), copy.deepcopy(goals))
        state.update(
            workspace_fixture=True,
            workspace_moved=False,
            workspace_pages=False,
            workspace_reopened=[],
            writes=[],
            workspace_requests=[],
        )
        tickets.clear()
        for identity, title, parent, status in [
            ("weekly", "Review the weekly summary", None, "approval"),
            ("onboarding", "Check the onboarding flow", "wren", "approval"),
            ("notes", "Choose the release notes", "wren", "assigned"),
            ("build", "Prepare build 9", "wren", "working"),
            ("testers", "Confirm the tester list", "wren", "unread"),
            ("article", "Publish the sleep article", "science", "agent_approval"),
            ("page-two", "Review the second page", "wren", "approval"),
        ]:
            ticket = copy.deepcopy(base_ticket)
            ticket.update(
                id=identity,
                title=title,
                recap="",
                worker_type="coding",
                stage="needs_what_changes",
                ceiling="needs_what_changes",
                ceiling_holder=dict(kind="owner", id="owner"),
                conversation_id="stub",
                pending_proposal=None,
                proof_parent=parent,
                proof_status=status,
            )
            tickets[identity] = ticket
        goals["release"].update(
            holder_kind="manager",
            holder_id="wren",
            status="bad",
            title="Ship Wren",
            deadline=None,
            completed=False,
            page=dict(
                kind="headroom.page",
                version=1,
                title="Ship Wren",
                summary="",
                blocks=[copy.deepcopy(checklist)],
            ),
        )
        goals["words"].update(
            holder_kind="manager",
            holder_id="science",
            status="good",
            title="Publish Science",
            deadline=None,
        )

    def workspace_records() -> dict[tuple[str, str], dict[str, Any]]:
        records = {
            ("chief", "chief"): dict(
                kind="chief", id="chief", value=dict(label="Chief of Staff", conversation_id="stub")
            ),
            ("project", "wren"): dict(kind="project", id="wren", value=dict(name="Wren")),
        }
        for identity, title, settled, created_at in [
            ("wren", "Wren release", False, 1),
            ("science", "Science site", False, 2),
            ("settled-one", "Previous release", True, 3),
            ("settled-two", "Earlier research", True, 4),
        ]:
            records[("manager", identity)] = dict(
                kind="manager",
                id=identity,
                value=dict(
                    title=title,
                    recap="",
                    recap_preview="",
                    recap_reference=None,
                    created_at=created_at,
                    updated_at=1,
                    project_id="wren",
                    settled=settled and identity not in state["workspace_reopened"],
                    conversation_id="stub",
                ),
            )
        for ticket in tickets.values():
            record = ticket_record(ticket, None)
            value = record["value"]
            status = ticket["proof_status"]
            if ticket["id"] == "notes" and state["workspace_moved"]:
                status = "approval"
            value.update(
                manager_kind="manager" if ticket["proof_parent"] else "chief",
                manager_id=ticket["proof_parent"],
                project_id="wren",
                ticket_status="awaiting_approval"
                if status in ("approval", "agent_approval")
                else "agent",
                stage_ownership="user" if status == "assigned" else "worker",
                conversation_id=ticket["id"],
                ceiling_holder=dict(kind="manager", id="science")
                if status == "agent_approval"
                else dict(kind="owner", id="owner"),
            )
            if status == "assigned":
                value["ticket_status"] = "empty"
            records[("ticket", ticket["id"])] = record
            conversation = conversation_record(ticket["id"], 0, 1 if status == "unread" else None)
            conversation["value"]["attention"]["current"]["signal"]["working"] = status == "working"
            records[("conversation", ticket["id"])] = conversation
        records[("conversation", "stub")] = conversation_record("stub", 0, None)
        return records

    def ticket_record(ticket: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
        stage: dict[str, Any] = next((item for item in ticket.get("workflow", {}).get("stages", [])
                      if item["id"] == ticket["stage"]), {})
        return {
            "kind": "ticket",
            "id": ticket["id"],
            "value": {
                "title": ticket["title"],
                "recap": ticket["recap"],
                "recap_preview": ticket["recap"],
                "recap_updated_at": None,
                "recap_stage_changed": False,
                "machine_id": "75a49e78-97de-4e28-8dc5-79646761c46f",
                "recap_reference": None,
                "worker_type": ticket["worker_type"],
                "stage_label": stage.get("label", "Current"),
                "workflow_label": "Product design",
                "stage": ticket["stage"],
                "ticket_status": "awaiting_approval" if proposal else "empty",
                "blocked": False,
                "created_at": 1,
                "updated_at": ticket.get("proof_revision", 1),
                "worker_step_claim_out": False,
                "deadline": None,
                "project_id": None,
                "manager_kind": "chief",
                "manager_id": None,
                "conversation_id": ticket["conversation_id"],
                "is_backlogged": False,
                "ceiling": ticket["ceiling"] if DESIGN_REVIEW else ticket["stage"],
                "ceiling_holder": (
                    ticket["ceiling_holder"] if DESIGN_REVIEW else {"kind": "owner", "id": "owner"}
                ),
                "gating_field": stage.get("gating_field") if DESIGN_REVIEW else "wireframe",
                "proposal_field": proposal["field"] if proposal else None,
                "proposal_revision": proposal["revision"] if proposal else 0,
                "proposal_created_at": proposal["created_at"] if proposal else None,
                "stage_ownership": "worker",
                "terminal": stage.get("is_terminal", False),
                "locale": "vps",
            },
        }

    def conversation_record(identity: str, read: int, unread_at: int | None) -> dict[str, Any]:
        unread = unread_at is not None
        return {
            "kind": "conversation",
            "id": identity,
            "value": {
                "first_unread_owner_message_at": unread_at,
                "owner_read_through_sequence": read,
                "error_acknowledged_through_sequence": 0,
                "attention": {
                    "current": {
                        "signal": {"working": False, "message_for_owner": unread},
                        "awaiting_answer": False,
                        "last_turn_failed": False,
                    },
                    "durable": {"message_for_owner": unread, "last_turn_failed": False},
                },
            },
        }

    def view_snapshot(request: dict[str, Any]) -> dict[str, Any]:
        """The server's scoped view for this fixture: Review cards, owners, counts."""
        records = {
            ("chief", "chief"): {
                "kind": "chief",
                "id": "chief",
                "value": {
                    "label": "Chief of Staff",
                    "conversation_id": state["chief_conversation"],
                },
            }
        }
        if DESIGN_REVIEW:
            for key, project, title in [
                ("design-panels", "Panels", "Get Panels ready for the phone"),
                ("design-wren", "Wren", "Ship Wren to the App Store"),
                ("design-write", "Write", "Write 1,000 words a day"),
            ]:
                records[("project", key)] = dict(kind="project", id=key, value=dict(name=project))
                records[("manager", key)] = dict(
                    kind="manager", id=key,
                    value=dict(
                        title=title, recap="", recap_preview="", recap_reference=None,
                        created_at=1, updated_at=1, project_id=key, settled=False,
                        conversation_id=None,
                    ),
                )
        cards: dict[tuple[str, str], int] = {}
        owner = state["review_owner"] if REVIEW_MESSAGES else None
        for ticket in tickets.values():
            # A Ticket that owns the unread conversation is a message-only card here,
            # so its conversation link opens first, as the connected case expects.
            # "ticket-stacked" keeps its proposal: one card with a proposal and a message.
            message_owner = owner == "ticket" and ticket["id"] == "offline-1"
            proposal = None if message_owner else ticket["pending_proposal"]
            records[("ticket", ticket["id"])] = ticket_record(ticket, proposal)
            # The Goals smoke reaches Review for its message card only.
            if proposal and (not GOALS_SMOKE or TABLE_SCROLL_SMOKE):
                cards[("ticket", ticket["id"])] = proposal["created_at"]
        conversations = [("stub", state["read_through"], None)]
        if REVIEW_MESSAGES:
            unread = state["read_through"] < (4 if state["new_message"] else 3)
            stub_owner = {
                "chief": "chief",
                "ticket": "offline-1",
                "ticket-stacked": "offline-1",
                "manager": "review-manager",
            }
            addresses = [
                (
                    "chief",
                    "ticket" if owner == "ticket-stacked" else owner,
                    stub_owner[owner],
                    "stub",
                    state["chief_conversation"] == "stub",
                    (1 if state["read_through"] < 3 else 3) if unread else None,
                ),
                (
                    "chief-history",
                    "chief",
                    "chief",
                    "history",
                    False,
                    None if state["historical_read"] else 5,
                ),
            ]
            conversations = [
                ("stub", state["read_through"], addresses[0][5]),
                ("history", 0, addresses[1][5]),
            ]
            if owner == "manager":
                records[("project", "review")] = {
                    "kind": "project",
                    "id": "review",
                    "value": {"name": "Review"},
                }
                records[("manager", "review-manager")] = {
                    "kind": "manager",
                    "id": "review-manager",
                    "value": {
                        "title": "Review orchestrator",
                        "recap": "",
                        "recap_preview": "",
                        "recap_reference": None,
                        "created_at": 1,
                        "updated_at": 1,
                        "project_id": "review",
                        "settled": False,
                        "conversation_id": "stub",
                    },
                }
            for address_id, kind, identity, conversation, current, waiting in addresses:
                records[("address", address_id)] = {
                    "kind": "address",
                    "id": address_id,
                    "value": {
                        "owner_kind": kind,
                        "owner_id": identity,
                        "conversation_id": conversation,
                        "current": current,
                        "parent_kind": "chief" if kind == "ticket" else None,
                        "parent_id": "chief" if kind == "ticket" else None,
                    },
                }
                if waiting is None:
                    continue
                key = ("ticket", identity) if kind == "ticket" else ("address", address_id)
                cards[key] = min(cards.get(key, waiting), waiting)
        for identity, read, unread_at in conversations:
            records[("conversation", identity)] = conversation_record(identity, read, unread_at)
        if state.get("workspace_fixture"):
            records = workspace_records()
            cards = {}
        order = sorted(
            cards,
            key=lambda key: (cards[key], "message" if key[0] == "address" else "ticket", key[1]),
        )
        windows = []
        for window in request["windows"]:
            members = order[: window["limit"]] if window["section"] == "review" else []
            if (
                DESIGN_REVIEW and window["section"] == "rail_tickets"
                and window["owner"]["kind"] == "chief"
            ):
                members = [("ticket", key) for key in tickets][:window["limit"]]
            if DESIGN_REVIEW and window["section"] == "rail_parents":
                members = [
                    ("manager", key) for key in ["design-panels", "design-wren", "design-write"]
                ]
            previous_anchor = next_anchor = None
            total = (
                len(members)
                if DESIGN_REVIEW
                else (len(order) if window["section"] == "review" else 0)
            )
            if state.get("workspace_fixture"):
                section = window["section"]
                owner_id = (window.get("owner") or {}).get("id")
                members = []
                if section == "rail_parents":
                    members = [
                        ("manager", key)
                        for key in (
                            ["science"] if state["workspace_moved"] else ["wren", "science"]
                        )
                        + state["workspace_reopened"]
                    ]
                elif section == "rail_tickets":
                    ids = [
                        key
                        for key, ticket in tickets.items()
                        if (ticket["proof_parent"] or "chief") == owner_id and key != "page-two"
                    ]
                    if owner_id == "wren" and state["workspace_pages"]:
                        if window.get("anchor") == "workspace-next":
                            ids = ["page-two"]
                            previous_anchor = "workspace-first"
                        else:
                            next_anchor = "workspace-next"
                    members = [("ticket", key) for key in ids]
                elif section == "goals":
                    members = [
                        ("goal", goal["id"])
                        for goal in (goals["release"], goals["words"])
                        if goal["holder_id"] == owner_id
                    ]
                elif section == "settled":
                    members = [
                        ("manager", key)
                        for key in ["settled-one", "settled-two"]
                        if key not in state["workspace_reopened"]
                    ]
                total = len(members) + (1 if next_anchor else 4 if previous_anchor else 0)
                state["workspace_requests"].append(copy.deepcopy(window))
            windows.append(
                {
                    "window_id": json.dumps([window["section"], window["owner"]]),
                    "request": window,
                    "members": [{"kind": kind, "id": identity} for kind, identity in members],
                    "total": total,
                    "previous": previous_anchor,
                    "next": next_anchor,
                    "forward_overlap": None,
                    "backward_overlap": None,
                }
            )
        review_context = []
        for conversation_id in request["review_conversation_ids"]:
            candidates = [
                row for row in records.values()
                if row["kind"] == "address"
                and row["value"]["conversation_id"] == conversation_id
                and (row["value"]["owner_kind"], row["value"]["owner_id"]) in records
            ]
            candidates.sort(key=lambda row: (-int(row["value"]["current"]), row["id"]))
            review_context.append({
                "conversation_id": conversation_id,
                "canonical_address_id": candidates[0]["id"] if candidates else None,
            })
        content = {
            "records": list(records.values()),
            "windows": windows,
            "counts": {
                "running_conversations": 7 if REVIEW_COMPLETION else 0,
                "out_claims": 0,
                "review": len(order),
                "unread": 0,
                "settled": 2 - len(state["workspace_reopened"])
                if state.get("workspace_fixture")
                else 0,
            },
            "review_context": review_context,
            "review_summary": {"skipped": 0, "new": len(order)},
        }
        version = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        return {
            "event": "snapshot",
            "wire_version": 3,
            "projection_version": 7,
            "view_id": "fixture",
            "scope_generation": request["scope_generation"],
            "ordering_generation": "fixture",
            "coverage": {
                "scope": "fixture",
                "schema_version": 11,
                "semantics": "attention-knowledge-v11-recap-freshness",
                "epoch": "fixture",
                "revision": "1",
            },
            "content_version": version,
            "mode": "current",
            "authority": {
                "delivery_session_id": "fixture",
                "availability": {"state": "current", "generation": "1", "reason": None},
                "permit": {
                    "scope": "fixture",
                    "runtime_epoch": "fixture",
                    "installation_id": "fixture",
                    "generation": "1",
                },
            },
            "content": content,
        }

    def row(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "row",
            "key": message["sender_message_id"],
            "sender_message_id": message["sender_message_id"],
            "body": {"kind": "inline", "value": {"kind": "prompt", "content": message["content"]}},
        }

    def conversation_view(path: str) -> dict[str, Any]:
        value: dict[str, Any] = {
            "page": {"items": [row(m) for m in state["messages"]], "older": None},
            "coverage": {"through_sequence": len(state["messages"])},
            "status": {
                "authority": "current",
                "turn_running": False,
                "rest": {"plan_progress": {}},
            },
        }
        if REPLACEMENT_SMOKE and "/conversations/" in path:
            identity = path.split("/conversations/", 1)[1].split("/", 1)[0]
            text = "New Chief transcript" if identity == "replacement" else "Old Chief transcript"
            value["page"]["items"] = [
                {
                    "kind": "row",
                    "key": identity + "-message",
                    "body": {
                        "kind": "inline",
                        "value": {
                            "kind": "agent_message",
                            "content": [{"piece": "text", "text": text}],
                        },
                    },
                }
            ]
            value["coverage"]["through_sequence"] = 8
            value["status"]["rest"]["latest_happened"] = {"kind": "agent_message", "excerpt": text}
        if REVIEW_MESSAGES and "/conversations/history/" in path:
            value["page"]["items"] = [
                {
                    "kind": "row",
                    "key": "history",
                    "body": {
                        "kind": "inline",
                        "value": {
                            "kind": "agent_message",
                            "content": [{"piece": "text", "text": "Historical transcript"}],
                        },
                    },
                }
            ]
        return value

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            with (output / "requests.log").open("a") as f:
                f.write(fmt % args + "\n")

        def respond(self, data: object, status: int = 200) -> None:
            encoded = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def disconnected(self) -> bool:
            if state["offline"]:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return True
            return False

        def do_GET(self) -> None:
            value: dict[str, Any]
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if path.startswith("/proof/"):
                name = path.removeprefix("/proof/")
                if UPDATES_SMOKE and name.startswith("updates/"):
                    phase = name.removeprefix("updates/")
                    if phase not in ("offer", "updating", "restart", "failed", "refused", "complete"):
                        self.respond({"error": "Unknown update fixture phase"}, 400)
                        return
                    with lock:
                        state["update_phase"] = phase
                        if phase == "restart":
                            state["update_deadline"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 15))
                        else:
                            state.pop("update_deadline", None)
                    self.respond({"phase": phase})
                    return
                if UPDATES_SMOKE and name == "update-state":
                    self.respond(dict(phase=state["update_phase"], reads=state["update_reads"],
                                      retry_count=state["update_retry_count"], writes=state["writes"]))
                    return
                if DESIGN_REVIEW and name == "workspace-fixture":
                    with lock:
                        select_workspace_fixture()
                    self.respond({})
                    return
                if (
                    DESIGN_REVIEW
                    and state.get("workspace_fixture")
                    and name.startswith("workspace-")
                ):
                    with lock:
                        if name == "workspace-update":
                            state["workspace_moved"] = True
                        elif name == "workspace-restore":
                            state["workspace_moved"] = False
                        elif name == "workspace-reset":
                            baseline_tickets, baseline_goals = state.pop("workspace_baseline")
                            tickets.clear()
                            tickets.update(baseline_tickets)
                            goals.clear()
                            goals.update(baseline_goals)
                            state["workspace_fixture"] = False
                        elif name == "workspace-pages":
                            state["workspace_pages"] = True
                        elif name == "workspace-requests":
                            self.respond(state["workspace_requests"])
                            return
                        else:
                            self.respond({"error": "Unknown Workspace proof action"}, 400)
                            return
                    self.respond({})
                    return
                if ARTIFACT_LINKS_PROOF and name == "artifact-reads":
                    with lock:
                        self.respond(state["artifact_reads"])
                    return
                if DESIGN_REVIEW and name == "scopes":
                    self.respond(state.get("scope_writes", []))
                    return
                if REPLACEMENT_SMOKE and name.startswith("replacement/"):
                    action = name.split("/", 1)[1]
                    with lock:
                        if action == "old":
                            state["chief_conversation"] = "stub"
                        elif action == "clear":
                            state["chief_conversation"] = None
                        elif action == "new":
                            state["chief_conversation"] = "replacement"
                        elif action == "relaunch-new":
                            try:
                                saved_replacement_reference()
                            except (ValueError, OSError, sqlite3.Error) as error:
                                self.respond({"error": str(error)}, 409)
                                return
                            state["chief_conversation"] = "replacement"
                        elif action == "unread":
                            state["read_through"] = 1
                        elif action == "state":
                            self.respond(
                                {
                                    "current": state["chief_conversation"],
                                    "tails": state["tail_requests"],
                                }
                            )
                            return
                        elif action == "review-observation":
                            (output / "replacement-review-observation.json").write_text(
                                json.dumps(query)
                            )
                        else:
                            self.respond({"error": "Unknown replacement action"}, 400)
                            return
                    self.respond({})
                    return
                if DESIGN_REVIEW and name.startswith("consequences-review/"):
                    action = name.rsplit("/", 1)[-1]
                    with lock:
                        if action == "implementation":
                            ticket = copy.deepcopy(tickets["offline-1"])
                            state["consequences_proposal"] = copy.deepcopy(
                                ticket["pending_proposal"]
                            )
                            tickets.clear()
                            tickets["offline-1"] = ticket
                            state["writes"] = []
                            state["consequences_decisions"] = []
                            ticket.update(
                                stage="needs_implementation", ceiling="needs_implementation"
                            )
                        else:
                            ticket = tickets["offline-1"]
                        if action in {"implementation", "proposal", "revision"}:
                            proposal = copy.deepcopy(state["consequences_proposal"])
                            revision = {
                                "implementation": 101, "proposal": 102, "revision": 103
                            }[action]
                            proposal.update(
                                field="implementation"
                                if action == "implementation" else "consequences",
                                            revision=revision, created_at=revision,
                                            body="Assembled native review " + action)
                            ticket.update(pending_proposal=proposal, proof_revision=revision,
                                          ticket_status="awaiting_approval")
                        elif action == "state":
                            self.respond(
                                dict(ticket=ticket, decisions=state["consequences_decisions"])
                            )
                            return
                        else:
                            self.respond({"error": "Unknown Consequences review action"}, 400)
                            return
                    self.respond({})
                    return
                if DESIGN_REVIEW and name.startswith("design-state/"):
                    mode = name.rsplit("/", 1)[-1]
                    with lock:
                        ticket = tickets["offline-1"]
                        ticket["conversation_id"] = None if mode == "noconv" else "stub"
                        if mode == "final":
                            ticket["stage"] = "needs_consequences"
                            ticket["pending_proposal"]["field"] = "consequences"
                        elif mode == "normal":
                            ticket["stage"] = "needs_what_changes"
                            ticket["pending_proposal"]["field"] = "what_changes"
                        if mode == "refused":
                            ticket["pending_proposal"]["revision"] += 1
                    self.respond({})
                    return
                if name.startswith("review-owner/"):
                    state["review_owner"] = name.split("/")[-1]
                elif name == "review-final":
                    # A local fixture transition clears the queue after Revisit.
                    # It does not issue any product mutation or real conversation send.
                    with lock:
                        state["read_through"] = 4
                        state["historical_read"] = True
                        for ticket in tickets.values():
                            ticket["pending_proposal"] = None
                elif name == "new-message":
                    state["new_message"] = True
                elif name == "offline":
                    state["offline"] = True
                elif name == "online":
                    state["offline"] = False
                    state["slow"] = False
                elif name == "fast":
                    state["slow"] = False
                elif name == "goal-conflict" and GOALS_SMOKE:
                    with lock:
                        goals["words"]["revision"] += 1
                        goals["words"]["why"] = "Newer server explanation."
                elif name == "goal-state" and GOALS_SMOKE:
                    self.respond(dict(goal=goals["words"], attempts=state["goal_patch_attempts"]))
                    return
                elif name == "writes":
                    self.respond(state["writes"])
                    return
                elif name.startswith("start/") and (
                    os.environ.get("PANELS_OFFLINE_RECORD_CLIPS", "0" if server_only else "1")
                    == "1"
                ):
                    if not native_control_allowed():
                        self.respond({"error": "An owned device and active turn are required"}, 409)
                        return
                    stop_clip()
                    state["recording"] = subprocess.Popen(
                        [
                            "xcrun",
                            "simctl",
                            "io",
                            DEVICE,
                            "recordVideo",
                            "--codec=h264",
                            "--force",
                            str(output / (name.split("/")[-1] + ".mp4")),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    while True:
                        line = state["recording"].stderr.readline()
                        if b"Recording started" in line or not line:
                            break
                elif name == "stop":
                    stop_clip()
                self.respond({})
                return
            if self.disconnected():
                return
            if state.get("request_failed"):
                self.respond({"error": {"message": "Request failed on the reachable server."}}, 503)
                return
            if path in ("/api/changes", "/api/navigation/view/events") or path.endswith(
                "/view/tail"
            ):
                # The navigation view and conversation tail send a fresh snapshot frame each
                # second, so the app follows fixture changes without a change-driven read.
                view = json.loads(query["view"][0]) if "view" in query else None
                if REPLACEMENT_SMOKE and path.endswith("/view/tail"):
                    state["tail_requests"].append(
                        path.split("/conversations/", 1)[1].split("/", 1)[0]
                    )
                    (output / "replacement-tail-requests.json").write_text(
                        json.dumps(state["tail_requests"])
                    )
                # A cold slow load holds the view's first frame as it held the snapshot read.
                while view is not None and state["slow"] and not state["offline"]:
                    time.sleep(0.1)
                if state["offline"]:
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while not state["offline"]:
                        with lock:
                            if path == "/api/changes":
                                frame = b"data: change\n\n"
                            elif view is not None:
                                frame = (
                                    b"event: snapshot\ndata: "
                                    + json.dumps(view_snapshot(view)).encode()
                                    + b"\n\n"
                                )
                            else:
                                frame = (
                                    b"event: snapshot\ndata: "
                                    + json.dumps(
                                        conversation_view(path.removesuffix("/tail"))
                                    ).encode()
                                    + b"\n\n"
                                )
                        self.wfile.write(frame)
                        self.wfile.flush()
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            while state["slow"]:
                time.sleep(0.1)
            page = dict(
                match_count=0, return_count=0, limit=30, offset=0, complete=True, next_offset=None
            )
            if DESIGN_REVIEW and not state.get("workspace_fixture") and path == "/api/goals":
                design_goals = []
                for key, status in [("design-panels", "good"), ("design-wren", "bad")]:
                    goal = goal_record(key, "Reach the next milestone", "binary")
                    goal.update(holder_kind="manager", holder_id=key, status=status, deadline=None)
                    design_goals.append(goal)
                self.respond(
                    dict(
                        goals=design_goals, status_date="2026-11-15",
                        next_refresh_at=time.time()+3600,
                    )
                )
                return
            if TABLE_SCROLL_SMOKE and not state.get("workspace_fixture") and path == "/api/goals":
                self.respond(dict(
                    goals=[], status_date="2026-11-04", next_refresh_at=time.time() + 3600
                ))
                return
            if TABLE_SCROLL_SMOKE and path == "/api/chief/page":
                self.respond(dict(
                    page=dict(kind="headroom.page", version=1, blocks=[table_proof]),
                    page_revision=1,
                ))
                return
            if TABLE_SCROLL_SMOKE and path == "/api/chief/page/readings":
                self.respond(dict(
                    goal_id="chief", revision=1, status="good", status_date="2026-11-04",
                    next_refresh_at=time.time() + 3600, values=[], sources=[], charts={},
                ))
                return
            if TABLE_SCROLL_SMOKE and path in (
                "/files/tickets/offline-1/table-proof.md",
                "/files/tickets/offline-1/nested-proof.md",
            ):
                content = (
                    b"# Table artifact proof\n\n[Next artifact](nested-proof.md)"
                    if path.endswith("/table-proof.md") else b"# Nested artifact proof"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if (GOALS_SMOKE or state.get("workspace_fixture")) and path == "/api/goals":
                self.respond(
                    dict(
                        goals=[goals["release"], goals["words"]]
                        if state.get("workspace_fixture")
                        else list(goals.values()),
                        status_date="2026-11-04",
                        next_refresh_at=time.time() + 3600,
                    )
                )
                return
            if (GOALS_SMOKE or state.get("workspace_fixture")) and path.startswith("/api/goals/"):
                key = path.split("/")[3]
                if path.endswith("/readings"):
                    values = (
                        [
                            dict(
                                measure_id="words",
                                date=f"2026-11-{day:02d}",
                                status="value",
                                value=manual["observation"]["value"] if day == 4 else day * 100,
                            )
                            for day in range(1, 5)
                        ]
                        if key == "words"
                        else []
                    )
                    self.respond(
                        dict(
                            goal_id=key,
                            revision=goals[key]["revision"],
                            status=goals[key]["status"],
                            status_date="2026-11-04",
                            next_refresh_at=time.time() + 3600,
                            values=values,
                            sources=[],
                            charts={},
                        )
                    )
                    return
                if key in goals and len(path.split("/")) == 4:
                    self.respond(goals[key])
                    return
            if (
                GOALS_SMOKE or state.get("workspace_fixture")
            ) and path == "/api/measures/words/manual-state":
                response = copy.deepcopy(manual)
                response["date"] = query.get("date", [manual["date"]])[0]
                self.respond(response)
                return
            if (GOALS_SMOKE or state.get("workspace_fixture")) and path == "/api/projects":
                self.respond(dict(projects=[dict(id="project_personal", name="Personal")]))
                return
            if GOALS_SMOKE and path == "/files/goals/kit/proof.md":
                content = b"# Release evidence\nNative goal artifact."
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if state.get("workspace_fixture") and path == "/api/machines":
                self.respond(
                    dict(
                        machines=[
                            dict(
                                machine_id="75a49e78-97de-4e28-8dc5-79646761c46f",
                                name="Home",
                                icon="house",
                                color="blue",
                                is_home=True,
                                available=True,
                                waiting_tickets=0,
                            )
                        ]
                    )
                )
                return
            if state.get("workspace_fixture") and path == "/api/executor/health":
                self.respond(dict(mode="local"))
                return
            if state.get("workspace_fixture") and path == "/api/conversation/backends":
                self.respond(dict(backends=[dict(backend_key="codex", installed=True,
                    available_models=[dict(model_id="sol", display_name="Sol", enabled=True)],
                    reasoning_effort_options=["medium"], default_model_id="sol",
                    default_reasoning_effort="medium")]))
                return
            if state.get("workspace_fixture") and path.startswith("/api/managers/"):
                identity = path.split("/")[3]
                record = workspace_records().get(("manager", identity))
                if record and path.endswith("/conversation"):
                    self.respond(dict(conversation_id="stub"))
                    return
                if record and path.endswith("/workspace-content"):
                    self.respond(dict(goal="", recap=""))
                    return
                if record and path.endswith("/workspace"):
                    manager = json.loads(
                        (ROOT / "ios/PanelsTests/Fixtures/manager.json").read_text()
                    )
                    manager.update(
                        id=identity,
                        title=record["value"]["title"],
                        goal="",
                        recap="",
                        tickets=[],
                        artifacts=[],
                        project="Wren",
                        project_id="wren",
                    )
                    manager["manager"]["conversation_id"] = "stub"
                    self.respond(manager)
                    return
            if ARTIFACT_LINKS_PROOF and path == (
                "/files/tickets/t_8rmd1ebk/artifacts/merge-preview.html"
            ):
                with lock:
                    state["artifact_reads"]["html"] += 1
                    read = state["artifact_reads"]["html"]
                content = (
                    "<!doctype html><html><head><meta name='viewport' "
                    "content='width=device-width, initial-scale=1'>"
                    "</head><body><h1>Merged HTML preview " + str(read) + "</h1></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if ARTIFACT_LINKS_PROOF and path in {
                "/files/tickets/t_8rmd1ebk/artifacts/migration-verification.txt",
                "/files/tickets/t_8rmd1ebk/artifacts/review-photo.png",
            }:
                photo = path.endswith(".png")
                with lock:
                    state["artifact_reads"]["photo" if photo else "text"] += 1
                content = (
                    (
                        ROOT / "ios/Panels/Features/Workspace/Resources/workspace-chief.png"
                    ).read_bytes()
                    if photo
                    else b"Migration verification body must appear only after a tap."
                )
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if photo else "text/plain")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if REPLACEMENT_SMOKE and path == "/api/goals":
                value = {
                    "goals": [],
                    "status_date": "2026-11-14",
                    "next_refresh_at": time.time() + 3600,
                }
            elif (REPLACEMENT_SMOKE or state.get("workspace_fixture")) and path.endswith("/page"):
                value = {"page": {"blocks": []}, "page_revision": 1}
            elif (REPLACEMENT_SMOKE or state.get("workspace_fixture")) and path.endswith(
                "/page/readings"
            ):
                value = {"values": [], "sources": []}
            elif path == "/api/managers":
                value = {"managers": [], "page": page}
            elif path == "/api/tickets":
                value = tickets[query["id"][0]] if "id" in query else {"tickets": [], "page": page}
            elif (
                path.startswith("/api/tickets/")
                and path.endswith("/workspace")
                and path.split("/")[3] in tickets
            ):
                # TicketResources uses the selected workspace document, not the CLI detail read.
                # Both decode the same Ticket controls from this fixture's retained state.
                value = tickets[path.split("/")[3]]
            elif path == "/files/tickets/offline-1/proof.html" and REVIEW_MESSAGES:
                content = b"<!doctype html><html><body><h1>Inline artifact proof</h1></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            elif path.endswith("/events") and REVIEW_MESSAGES:
                history = "/conversations/history/" in path
                entries = (
                    [(8, "Historical message")]
                    if history
                    else [
                        (2, "First unread message"),
                        (
                            3,
                            "Last unread message\n\n"
                            "[proof.html](/files/tickets/offline-1/proof.html)",
                        ),
                    ]
                )
                if state["new_message"] and not history:
                    entries.append((4, "Arrived after display"))
                value = {
                    "events": [
                        {"kind": "message_to_owner", "sequence": seq, "payload": {"text": text}}
                        for seq, text in entries
                    ]
                }
            elif path == "/api/navigation/view/snapshot":
                value = view_snapshot(json.loads(query["view"][0]))
            elif path == "/api/workers/chief-of-staff/settings":
                value = {
                    "employee_id": "chief",
                    "label": "Chief of Staff",
                    "recap": "Saved workspace",
                    "skill": {"name": "chief", "description": "", "markdown_body": ""},
                    "launch_defaults": {"employee_backend": "codex"},
                }
            elif path == "/api/workers":
                value = {
                    "workers": [],
                    "chief_of_staff": {
                        "employee_id": "chief",
                        "label": "Chief of Staff",
                        "recap": "Saved workspace",
                        "skill": {"name": "chief", "description": "", "markdown_body": ""},
                        "launch_defaults": {"employee_backend": "codex"},
                        "conversation_signal": {"working": False, "message_for_owner": False},
                    }
                }
            elif path == "/api/managers/review-manager/workspace-content":
                manager = json.loads((ROOT / "ios/PanelsTests/Fixtures/manager.json").read_text())
                value = {"goal": manager["goal"], "recap": manager["recap"]}
            elif path == "/api/managers/review-manager/workspace":
                value = json.loads((ROOT / "ios/PanelsTests/Fixtures/manager.json").read_text())
                value.update(
                    id="review-manager", title="Review orchestrator", tickets=[], artifacts=[]
                )
                value["manager"]["conversation_id"] = "stub"
            elif path == "/api/chief/conversation":
                value = {"conversation_id": state["chief_conversation"]}
            elif UPDATES_SMOKE and path == "/api/updates":
                with lock:
                    state["update_reads"] += 1
                    value = update_status()
            elif path == "/api/deployment-status":
                value = {"state": "idle"}
            elif path.endswith("/manifest"):
                value = manifest
            elif "/decision-operations/" in path:
                self.respond({"receipt_contract": 1, "receipt": decision_receipts.get(path)})
                return
            elif path.endswith("/view/receipts"):
                ids = json.loads(query["message_ids"][0])
                value = {
                    "receipts": [
                        {"sender_message_id": m["sender_message_id"], "fate": "prompt"}
                        for m in state["messages"]
                        if m["sender_message_id"] in ids
                    ]
                }
            elif path.endswith("/view"):
                value = conversation_view(path)
            elif (
                path.endswith("/conversations/stub")
                or (REPLACEMENT_SMOKE and path.endswith("/conversations/replacement"))
                or path.endswith("/conversations/history")
                or path.endswith("/start-values")
            ):
                value = {
                    "backend_key": "codex",
                    "model": "sol",
                    "held_prompts": [],
                    "owner_read_through_sequence": 0
                    if path.endswith("history")
                    else state["read_through"],
                }
            elif path == "/api/native-push/device-registration":
                value = {}
            else:
                self.respond({"error": {"message": "Unexpected stub route " + path}}, 404)
                return
            self.respond(value)

        def do_PATCH(self) -> None:
            path = urlparse(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            if state.get("workspace_fixture") and path.startswith("/api/managers/"):
                identity = path.split("/")[3]
                if identity in ("settled-one", "settled-two") and body == {"settled": False}:
                    state["workspace_reopened"].append(identity)
                    state["writes"].append(dict(path=path, body=body))
                    (output / "workspace-proof-writes.json").write_text(json.dumps(state["writes"]))
                    self.respond({})
                    return
                self.respond({"error": "Unexpected Manager write"}, 400)
                return
            if DESIGN_REVIEW and path.startswith("/api/tickets/") and path.split("/")[3] in tickets:
                with lock:
                    ticket = tickets[path.split("/")[3]]
                    assert set(body) <= {"ceiling", "ceiling_holder"}
                    ticket.update(body)
                    state.setdefault("scope_writes", []).append(dict(path=path, body=body))
                    self.respond(ticket)
                return
            if not (GOALS_SMOKE or state.get("workspace_fixture")) or not path.startswith(
                "/api/goals/"
            ):
                self.respond({}, 404)
                return
            with lock:
                goal = goals[path.split("/")[3]]
                state["goal_patch_attempts"] += 1
                wire_body = copy.deepcopy(body)
                if body.pop("expected_revision") != goal["revision"]:
                    self.respond(dict(error=dict(message="This goal changed.")), 409)
                    return
                goal.update(body)
                goal["revision"] += 1
                if goal["primary_checklist_block_id"]:
                    items = goal["page"]["blocks"][0]["items"]
                    goal["completed"] = bool(items) and all(
                        item["state"] == "done" for item in items
                    )
                state["writes"].append(
                    dict(path=path, body=wire_body if state.get("workspace_fixture") else body)
                )
                (output / "operations.json").write_text(json.dumps(state["writes"]))
                self.respond(goal)

        def do_POST(self) -> None:
            value: dict[str, Any]
            if self.disconnected():
                return
            path = urlparse(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            with lock:
                if UPDATES_SMOKE and path.startswith("/api/updates/"):
                    state["writes"].append(dict(path=path, body=body,
                        app_sha=self.headers.get("X-Panels-App-SHA"),
                        native_build=self.headers.get("X-Panels-iOS-Build"),
                        client_identity=self.headers.get("X-Panels-Client")))
                    (output / "operations.json").write_text(json.dumps(state["writes"]))
                    if path == "/api/updates/attempts/previous-attempt/machines/machine-failed/retry":
                        state["update_retry_count"] += 1
                        if state["update_retry_count"] == 1:
                            self.respond(dict(error=dict(message="This machine update changed. Read the status and try again.")), 409)
                            return
                        value = dict(attempt=update_status()["attempt"], machine=update_status()["machines"][2])
                    elif path in ("/api/updates/install", "/api/updates/attempts/server-attempt/retry"):
                        if state["update_phase"] == "refused":
                            self.respond(dict(error=dict(message="This update changed. Read the status and try again.")), 409)
                            return
                        state["update_phase"] = "updating"
                        value = dict(attempt=update_status()["attempt"])
                    else:
                        self.respond(dict(error=dict(message="Unexpected update target")), 409)
                        return
                elif GOALS_SMOKE and path == "/api/measures/words/manual-writes":
                    identity = body["request_id"]
                    if identity in manual_receipts:
                        self.respond(manual_receipts[identity])
                        return
                    if (
                        body["operation"] == "replace"
                        and body["expected_write_revision"] != manual["write_revision"]
                    ):
                        self.respond(dict(error=dict(message="This reading changed.")), 409)
                        return
                    value_number = (
                        body["value"]
                        if body["operation"] == "replace"
                        else manual["observation"]["value"] + body["delta"]
                    )
                    manual.update(
                        date=body["date"],
                        observation=dict(value=value_number),
                        latest_observation=dict(value=value_number),
                        write_revision=manual["write_revision"] + 1,
                    )
                    value = dict(
                        receipt=dict(request_id=identity, date=body["date"]),
                        state=copy.deepcopy(manual),
                        replayed=False,
                    )
                    manual_receipts[identity] = value
                    state["writes"].append(dict(path=path, body=body))
                elif REVIEW_MESSAGES and path.endswith("/owner-read"):
                    state["writes"].append(body)
                    if "/history/" in path:
                        state["historical_read"] = True
                    else:
                        state["read_through"] = body["through_sequence"]
                    value = {"owner_read_through_sequence": body["through_sequence"]}
                elif (
                    REVIEW_MESSAGES
                    and path.endswith("/send")
                    and "owner_read_through_sequence" not in body
                ):
                    # Final-page Chief messages use the ordinary conversation path,
                    # without the unread-run boundary that belongs to a Review reply.
                    state["writes"].append(body)
                    state["messages"].append(body)
                    value = {
                        "conversation_id": body.get("conversation_id")
                        or state["chief_conversation"]
                        or "stub",
                        "fate": "started",
                    }
                elif REVIEW_MESSAGES and path.endswith("/send"):
                    state["writes"].append(body)
                    if not state["refused"]:
                        state["refused"] = True
                        value = {"conversation_id": "stub", "fate": "refused"}
                    else:
                        state["read_through"] = body["owner_read_through_sequence"]
                        state["messages"].append(body)
                        value = {"conversation_id": "stub", "fate": "queued"}
                elif path.endswith("/send"):
                    identity = body["sender_message_id"]
                    if not any(m["sender_message_id"] == identity for m in state["messages"]):
                        state["messages"].append(body)
                        state["writes"].append("message")
                    value = {"conversation_id": "stub", "fate": "started"}
                elif "/decision-operations/" in path:
                    if path in decision_receipts:
                        self.respond({"receipt_contract": 1, "receipt": decision_receipts[path]})
                        return
                    ticket = tickets[path.split("/")[3]]
                    proposal = ticket["pending_proposal"]
                    refused = (
                        not proposal
                        or body["expected_proposal_revision"] != proposal["revision"]
                        or body["field"] != proposal["field"]
                    )
                    receipt = dict(
                        operation_id=path.rsplit("/", 1)[-1],
                        ticket_id=ticket["id"],
                        decision=body["decision"],
                        field=body["field"],
                        expected_proposal_revision=body["expected_proposal_revision"],
                        outcome="refused" if refused else "applied",
                        created_at=int(time.time()),
                        transition=(
                            None
                            if refused
                            else dict(
                                stage=(
                                    ticket["stage"] if body["decision"] == "send_back"
                                    else ticket["workflow"]["advance"][ticket["stage"]]
                                    if DESIGN_REVIEW
                                    else "done"
                                ),
                                ceiling=body.get("next_ceiling", ticket["ceiling"]),
                                ceiling_holder=body.get("next_holder", ticket["ceiling_holder"]),
                                ticket_status="ready",
                                pending_proposal_revision=body["expected_proposal_revision"] + 1,
                            )
                        ),
                        error=(
                            dict(code="validation", message="This changed, review again", detail={})
                            if refused
                            else None
                        ),
                    )
                    decision_receipts[path] = receipt
                    if not refused:
                        if DESIGN_REVIEW:
                            ticket.update(receipt["transition"])
                            ticket["proof_revision"] = ticket.get("proof_revision", 1) + 1
                            if "consequences_decisions" in state:
                                state["consequences_decisions"].append(body)
                        ticket["pending_proposal"] = None
                        state["writes"].append(
                            "approve" if body["decision"] == "approve" else "sendBack"
                        )
                    value = {"receipt_contract": 1, "receipt": receipt}
                elif "/accept/" in path:
                    ticket = tickets[path.split("/")[3]]
                    if (
                        not ticket["pending_proposal"]
                        or body["expected_proposal_revision"]
                        != ticket["pending_proposal"]["revision"]
                    ):
                        self.respond({"error": {"message": "This changed, review again"}}, 409)
                        return
                    ticket["pending_proposal"] = None
                    state["writes"].append("approve")
                    value = {}
                elif path.endswith("/reject"):
                    tickets[path.split("/")[3]]["pending_proposal"] = None
                    state["writes"].append("sendBack")
                    value = {}
                elif path.endswith("/voice-transcriptions"):
                    assert len(body["audio"]) > 50
                    state["writes"].append("voice")
                    value = {"transcript": "Recorded guidance restored after reconnect."}
                else:
                    self.respond({}, 404)
                    return
                (output / "operations.json").write_text(json.dumps(state["writes"]))
            self.respond(value)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    (output / "stub-url.txt").write_text(url)
    env = os.environ.copy()
    env["TEST_RUNNER_PANELS_OFFLINE_STUB_URL"] = url
    env["TEST_RUNNER_PANELS_REVIEW_COMPLETION"] = "1" if REVIEW_COMPLETION else "0"
    env["TEST_RUNNER_PANELS_REVIEW_STACKED"] = os.environ.get("PANELS_REVIEW_STACKED", "0")
    control = None
    if REACHABILITY_SMOKE:
        port = server.server_port

        class ConnectionControl(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                nonlocal server
                action = self.path.removeprefix("/")
                if action == "offline" and not state["offline"]:
                    state["offline"] = True
                    server.shutdown()
                    server.server_close()
                elif action == "online":
                    state["request_failed"] = False
                    if state["offline"]:
                        state["offline"] = False
                        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
                        threading.Thread(target=server.serve_forever, daemon=True).start()
                elif action == "request-failed":
                    state["request_failed"] = True
                else:
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        control = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ConnectionControl)
        threading.Thread(target=control.serve_forever, daemon=True).start()
        env["TEST_RUNNER_PANELS_REACHABILITY_CONTROL_URL"] = (
            f"http://127.0.0.1:{control.server_port}"
        )
    try:
        if server_only:
            # The caller owns build, test, device and deadline. This mode starts
            # only the existing loopback fixture, with no native preflight.
            ready = {"PANELS_OFFLINE_STUB_URL": url}
            if control:
                ready["PANELS_REACHABILITY_CONTROL_URL"] = f"http://127.0.0.1:{control.server_port}"
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            (output / "fixture-ready.json").write_text(json.dumps(ready))
            stopped.wait()
            return 0
        return subprocess.call(
            [
                "xcodebuild",
                "-project",
                str(ROOT / "ios/Panels.xcodeproj"),
                "-scheme",
                "PanelsReview" if review else "PanelsNotifications",
                "-configuration",
                "Debug",
                "-destination",
                "id=" + DEVICE,
                "-derivedDataPath",
                str(output / "build"),
                "-resultBundlePath",
                str(output / f"Offline-{int(time.time())}.xcresult"),
                "-jobs",
                "4",
                "-parallel-testing-enabled",
                "NO",
                "-collect-test-diagnostics",
                "never",
                "PANELS_APP_BUNDLE_IDENTIFIER=" + BUNDLE,
                "CODE_SIGNING_ALLOWED=NO",
                (
                    "-only-testing:PanelsReviewUITests/"
                    if review
                    else "-only-testing:PanelsNotificationUITests/"
                )
                + (
                    os.environ.get("PANELS_OFFLINE_TEST")
                    or ("ReviewMessagesTests" if REVIEW_MESSAGES else "OfflineOperationTests")
                ),
                "test",
            ],
            env=env,
        )
    finally:
        stop_clip()
        if not server_only:
            subprocess.run(["xcrun", "simctl", "terminate", DEVICE, BUNDLE], check=False)
        state["offline"] = True
        server.shutdown()
        server.server_close()
        if control:
            control.shutdown()
            control.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
