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
DEVICE = "5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6"
BUNDLE = os.environ.get("PANELS_OFFLINE_BUNDLE", "app.panels.ios.pios-offline")
REVIEW_MESSAGES = os.environ.get("PANELS_REVIEW_MESSAGES") == "1"


def main() -> int:
    if "--locked" not in sys.argv:
        return subprocess.call(
            ["lockf", "/tmp/panels-PanelsLaneA.lock", sys.executable, __file__, "--locked"]
        )
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
                "stages": [
                    {"id": base_ticket["stage"], "label": "Current"},
                    {"id": "done", "label": "Done"},
                ],
                "advance": {base_ticket["stage"]: "done"},
                "fields": [{"id": "wireframe", "label": "Proposal"}],
                "ceiling_range": ["done"],
            }
        ]
    }
    state: dict[str, Any] = {
        "offline": False,
        "slow": True,
        "writes": [],
        "messages": [],
        "recording": None,
    }
    state.update(read_through=1, historical_read=False, new_message=False, refused=False)
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
                if name == "new-message":
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
                    "running_worker_count": 0,
                }
                if REVIEW_MESSAGES:
                    unread = state["read_through"] < (4 if state["new_message"] else 3)
                    if unread:
                        value["items"].append(
                            {
                                "review_item_type": "message",
                                "conversation_id": "stub",
                                "address_id": "chief",
                                "owner_kind": "chief",
                                "owner_id": "chief",
                                "title": "Unread conversation",
                                "waiting_since": 1 if state["read_through"] < 3 else 3,
                            }
                        )
                    if not state["historical_read"]:
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
                + ("ReviewMessagesTests" if REVIEW_MESSAGES else "OfflineOperationTests"),
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
