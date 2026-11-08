#!/usr/bin/env python3
"""One local HTTP stub and the existing UI test host; never operates simulator lifecycle."""

import copy
import hashlib
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
REVIEW_MESSAGES = os.environ.get("PANELS_REVIEW_MESSAGES") == "1"


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
    state: dict[str, Any] = {
        "offline": False,
        "slow": not REVIEW_COMPLETION,
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

    def ticket_record(ticket: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "kind": "ticket",
            "id": ticket["id"],
            "value": {
                "title": ticket["title"],
                "recap": ticket["recap"],
                "recap_preview": ticket["recap"],
                "recap_reference": None,
                "worker_type": ticket["worker_type"],
                "stage_label": "Current",
                "workflow_label": "Product design",
                "stage": ticket["stage"],
                "ticket_status": "awaiting_approval" if proposal else "empty",
                "blocked": False,
                "created_at": 1,
                "updated_at": 1,
                "worker_step_claim_out": False,
                "deadline": None,
                "project_id": None,
                "manager_kind": "chief",
                "manager_id": None,
                "conversation_id": ticket["conversation_id"],
                "is_backlogged": False,
                "ceiling": ticket["stage"],
                "ceiling_holder": {"kind": "owner", "id": "owner"},
                "gating_field": "wireframe",
                "proposal_field": "wireframe" if proposal else None,
                "proposal_revision": proposal["revision"] if proposal else 0,
                "proposal_created_at": proposal["created_at"] if proposal else None,
                "stage_ownership": "worker",
                "terminal": False,
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
                "value": {"label": "Chief of Staff", "conversation_id": "stub"},
            }
        }
        cards: dict[tuple[str, str], int] = {}
        owner = state["review_owner"] if REVIEW_MESSAGES else None
        for ticket in tickets.values():
            # A Ticket that owns the unread conversation is a message-only card here,
            # so its conversation link opens first, as the connected case expects.
            message_owner = owner == "ticket" and ticket["id"] == "offline-1"
            proposal = None if message_owner else ticket["pending_proposal"]
            records[("ticket", ticket["id"])] = ticket_record(ticket, proposal)
            if proposal:
                cards[("ticket", ticket["id"])] = proposal["created_at"]
        conversations = [("stub", state["read_through"], None)]
        if REVIEW_MESSAGES:
            unread = state["read_through"] < (4 if state["new_message"] else 3)
            stub_owner = {"chief": "chief", "ticket": "offline-1", "manager": "review-manager"}
            addresses = [
                (
                    "chief",
                    owner,
                    stub_owner[owner],
                    "stub",
                    True,
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
        order = sorted(
            cards,
            key=lambda key: (cards[key], "message" if key[0] == "address" else "ticket", key[1]),
        )
        windows = []
        for window in request["windows"]:
            members = order[: window["limit"]] if window["section"] == "review" else []
            windows.append(
                {
                    "window_id": json.dumps([window["section"], window["owner"]]),
                    "request": window,
                    "members": [{"kind": kind, "id": identity} for kind, identity in members],
                    "total": len(order) if window["section"] == "review" else 0,
                    "previous": None,
                    "next": None,
                    "forward_overlap": None,
                    "backward_overlap": None,
                }
            )
        content = {
            "records": list(records.values()),
            "windows": windows,
            "counts": {
                "running_conversations": 7 if REVIEW_COMPLETION else 0,
                "out_claims": 0,
                "review": len(order),
                "unread": 0,
                "settled": 0,
            },
            "review_context": [],
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
                "schema_version": 10,
                "semantics": "attention-knowledge-v10-ticket-workflows",
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
            if path in ("/api/changes", "/api/navigation/view/events") or path.endswith("/view/tail"):
                # The navigation view and conversation tail send a fresh snapshot frame each
                # second, so the app follows fixture changes without a change-driven read.
                view = json.loads(query["view"][0]) if "view" in query else None
                # A cold slow load holds the view's first frame as it held the snapshot read.
                while view is not None and state["slow"]:
                    time.sleep(0.1)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while not state["offline"]:
                        with lock:
                            if path == "/api/changes":
                                frame = b"data: change\n\n"
                            elif view is not None:
                                frame = b"event: snapshot\ndata: " + json.dumps(view_snapshot(view)).encode() + b"\n\n"
                            else:
                                frame = (
                                    b"event: snapshot\ndata: "
                                    + json.dumps(conversation_view(path.removesuffix("/tail"))).encode()
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
                value = conversation_view(path)
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

        def do_POST(self) -> None:
            value: dict[str, Any]
            if self.disconnected():
                return
            path = urlparse(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            with lock:
                if REVIEW_MESSAGES and path.endswith("/owner-read"):
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


if __name__ == "__main__":
    raise SystemExit(main())
