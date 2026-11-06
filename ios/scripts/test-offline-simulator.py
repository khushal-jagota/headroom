#!/usr/bin/env python3
"""One local HTTP stub and the existing UI test host; never operates simulator lifecycle."""

import copy
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
REVIEW_COMPLETION = os.environ.get("PANELS_REVIEW_COMPLETION") == "1"
REACHABILITY_SMOKE = os.environ.get("PANELS_REACHABILITY_SMOKE") == "1"
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
GOALS_SMOKE = os.environ.get("PANELS_GOALS_SMOKE") == "1"
REVIEW_MESSAGES = os.environ.get("PANELS_REVIEW_MESSAGES") == "1" or GOALS_SMOKE


def main() -> int:
    if "--locked" not in sys.argv:
        return subprocess.call(["lockf", LANE_LOCK, sys.executable, __file__, "--locked"])
    output = Path(os.environ["PANELS_OFFLINE_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    devices = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"]))
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
    # Goals reuse this host and the exact authored chart catalogue from the web.
    catalogue_source = (ROOT / "web/src/lib/dev/goalComponentExamples.ts").read_text()
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
            conversation_id="stub",
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
    state: dict[str, Any] = {
        "offline": False,
        "slow": not REVIEW_COMPLETION and not GOALS_SMOKE and not REACHABILITY_SMOKE,
        "writes": [],
        "messages": [],
        "recording": None,
    }
    state.update(
        read_through=1,
        historical_read=False,
        new_message=False,
        refused=False,
        review_owner="chief",
    )
    if REVIEW_MESSAGES:
        state["slow"] = False
        tickets = {"offline-1": tickets["offline-1"]}
        tickets["offline-1"]["pending_proposal"]["created_at"] = 2
    lock = threading.RLock()

    def stop_clip() -> None:
        recording = state["recording"]
        if recording:
            recording.send_signal(signal.SIGINT)
            recording.communicate(timeout=15)
            state["recording"] = None

    def row(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "row",
            "key": message["sender_message_id"],
            "sender_message_id": message["sender_message_id"],
            "body": {"kind": "inline", "value": {"kind": "prompt", "content": message["content"]}},
        }

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
                if name.startswith("review-owner/"):
                    state["review_owner"] = name.split("/")[-1]
                elif name == "new-message":
                    state["new_message"] = True
                elif name == "offline":
                    state["offline"] = True
                elif name == "online":
                    state["offline"] = False
                    state["slow"] = False
                elif name == "fast":
                    state["slow"] = False
                elif name == "writes":
                    self.respond(state["writes"])
                    return
                elif name.startswith("start/"):
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
            if path == "/api/changes" or path.endswith("/view/tail"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while not state["offline"]:
                        self.wfile.write(b"data: change\n\n")
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
            if GOALS_SMOKE and path == "/api/goals":
                self.respond(
                    dict(
                        goals=list(goals.values()),
                        status_date="2026-11-04",
                        next_refresh_at=time.time() + 3600,
                    )
                )
                return
            if GOALS_SMOKE and path.startswith("/api/goals/"):
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
            if GOALS_SMOKE and path == "/api/measures/words/manual-state":
                response = copy.deepcopy(manual)
                response["date"] = query.get("date", [manual["date"]])[0]
                self.respond(response)
                return
            if GOALS_SMOKE and path == "/api/projects":
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
            if path == "/api/managers":
                value = {"managers": [], "page": page}
            elif path == "/api/tickets":
                value = tickets[query["id"][0]] if "id" in query else {"tickets": [], "page": page}
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
            elif path == "/api/review":
                value = {
                    "items": [
                        {
                            "review_item_type": "proposal",
                            "ticket_id": t["id"],
                            "field": "wireframe",
                            "title": t["title"],
                            "waiting_since": t["pending_proposal"]["created_at"],
                        }
                        for t in tickets.values()
                        if t["pending_proposal"]
                    ],
                    "running_worker_count": 7 if REVIEW_COMPLETION else 0,
                }
                if GOALS_SMOKE:
                    value["items"] = []
                if REVIEW_MESSAGES:
                    unread = state["read_through"] < (4 if state["new_message"] else 3)
                    if unread:
                        value["items"].append(
                            {
                                "review_item_type": "message",
                                "conversation_id": "stub",
                                "address_id": "chief",
                                "owner_kind": state["review_owner"],
                                "owner_id": {
                                    "chief": "chief",
                                    "ticket": "offline-1",
                                    "manager": "review-manager",
                                    "goal": "words",
                                }[state["review_owner"]],
                                "title": "Unread conversation",
                                "waiting_since": 1 if state["read_through"] < 3 else 3,
                            }
                        )
                    if not GOALS_SMOKE and not state["historical_read"]:
                        value["items"].append(
                            {
                                "review_item_type": "message",
                                "conversation_id": "history",
                                "address_id": "chief-history",
                                "owner_kind": "chief",
                                "owner_id": "chief",
                                "title": "Earlier conversation",
                                "waiting_since": 5,
                            }
                        )
                    value["items"].sort(key=lambda item: item["waiting_since"])
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
                    "chief_of_staff": {
                        "label": "Chief of Staff",
                        "recap": "Saved workspace",
                        "conversation_signal": {"working": False, "message_for_owner": False},
                    }
                }
            elif path == "/api/managers/review-manager/workspace":
                value = json.loads((ROOT / "ios/PanelsTests/Fixtures/manager.json").read_text())
                value.update(
                    id="review-manager", title="Review orchestrator", tickets=[], artifacts=[]
                )
                value["manager"]["conversation_id"] = "stub"
            elif path == "/api/chief/conversation":
                value = {"conversation_id": "stub"}
            elif path == "/api/deployment-status":
                value = {"state": "idle"}
            elif path.endswith("/manifest"):
                value = manifest
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
                value = {
                    "page": {"items": [row(m) for m in state["messages"]], "older": None},
                    "status": {
                        "authority": "current",
                        "turn_running": False,
                        "rest": {"plan_progress": {}},
                    },
                }
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
            elif (
                path.endswith("/conversations/stub")
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
            if not GOALS_SMOKE or not path.startswith("/api/goals/"):
                self.respond({}, 404)
                return
            with lock:
                goal = goals[path.split("/")[3]]
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
                state["writes"].append(dict(path=path, body=body))
                (output / "operations.json").write_text(json.dumps(state["writes"]))
                self.respond(goal)

        def do_POST(self) -> None:
            value: dict[str, Any]
            if self.disconnected():
                return
            path = urlparse(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            with lock:
                if GOALS_SMOKE and path == "/api/measures/words/manual-writes":
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
        env["TEST_RUNNER_PANELS_REACHABILITY_CONTROL_URL"] = f"http://127.0.0.1:{control.server_port}"
    try:
        return subprocess.call(
            [
                "xcodebuild",
                "-project",
                str(ROOT / "ios/Panels.xcodeproj"),
                "-scheme",
                "PanelsNotifications",
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
                "-only-testing:PanelsNotificationUITests/"
                + (
                    os.environ.get("PANELS_OFFLINE_TEST")
                    or (
                        "ReviewMessagesTests/testCompletionAndRevisit"
                        if REVIEW_COMPLETION
                        else "ReviewMessagesTests"
                        if REVIEW_MESSAGES
                        else "OfflineOperationTests"
                    )
                ),
                *(
                    [
                        "-only-testing:PanelsTests/ReviewDecisionTests/testSkipIsLocalAndNewProposalIdentityReturns"
                    ]
                    if REVIEW_COMPLETION
                    else []
                ),
                "test",
            ],
            env=env,
        )
    finally:
        stop_clip()
        subprocess.run(["xcrun", "simctl", "terminate", DEVICE, BUNDLE], check=False)
        server.shutdown()
        server.server_close()
        if control:
            control.shutdown()
            control.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
