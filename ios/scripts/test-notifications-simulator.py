#!/usr/bin/env python3
"""One-off loopback notification, reply and preference review on the owned lane."""

import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import live_socket_stub

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = os.environ.get("PANELS_PUSH_BUNDLE", "app.panels.ios.pios-notify")
# The owned lane, by name. Another task names its own booted lane here.
LANE = os.environ.get("PANELS_PUSH_LANE", "PanelsLaneB")
PORT = 18763


def main():
    if "--review" not in sys.argv:
        raise SystemExit("Notification delivery is a one-off check. Run with --review.")
    if "--locked" not in sys.argv:
        return subprocess.call(
            [
                "lockf",
                f"/tmp/panels-{LANE}.lock",
                sys.executable,
                __file__,
                *sys.argv[1:],
                "--locked",
            ]
        )
    devices = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"]))
    lane = [
        device
        for group in devices["devices"].values()
        for device in group
        if device["name"] == LANE
    ]
    if len(lane) != 1 or lane[0]["state"] != "Booted":
        raise SystemExit(f"Exactly one {LANE} must already be booted by its owner.")
    device = lane[0]["udid"]
    output = Path(os.environ["PANELS_PUSH_TEST_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{PORT}"
    lock = threading.RLock()
    state = dict(
        enabled=True,
        preference_writes=[],
        attempts=[],
        accepted=[],
        receipts=[],
        fail_preference=False,
        drop_reply_response=False,
        hide_receipts=False,
    )
    chief = dict(
        employee_id="chief",
        label="Chief of Staff",
        recap="Local notification review",
        skill=dict(name="chief", description="", markdown_body=""),
        launch_defaults=dict(employee_backend="codex"),
    )

    def record():
        (output / "operations.json").write_text(json.dumps(state, indent=2))

    def settings():
        return dict(
            subjects=[
                dict(
                    key="tickets",
                    label="Tickets",
                    types=[
                        dict(
                            id="awaiting_reply",
                            label="Message",
                            description="An unread message needs your reply.",
                            enabled=state["enabled"],
                        )
                    ],
                )
            ]
        )

    def view_snapshot(request):
        # Reuses the minimal navigation contract from the existing offline fixture host.
        content = dict(
            records=[],
            windows=[
                dict(
                    window_id=json.dumps([window["section"], window["owner"]]),
                    request=window,
                    members=[],
                    total=0,
                    previous=None,
                    next=None,
                    forward_overlap=None,
                    backward_overlap=None,
                )
                for window in request["windows"]
            ],
            counts=dict(running_conversations=0, out_claims=0, review=0, unread=0, settled=0),
            review_context=[],
            review_summary=dict(skipped=0, new=0),
        )
        return dict(
            event="snapshot",
            wire_version=3,
            projection_version=7,
            view_id="notification-fixture",
            scope_generation=request["scope_generation"],
            ordering_generation="fixture",
            coverage=dict(
                scope="fixture",
                schema_version=11,
                semantics="attention-knowledge-v11-recap-freshness",
                epoch="fixture",
                revision="1",
            ),
            content_version=hashlib.sha256(
                json.dumps(content, sort_keys=True).encode()
            ).hexdigest(),
            mode="current",
            authority=dict(
                delivery_session_id="fixture",
                availability=dict(state="current", generation="1", reason=None),
                permit=dict(
                    scope="fixture",
                    runtime_epoch="fixture",
                    installation_id="fixture",
                    generation="1",
                ),
            ),
            content=content,
        )

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            with (output / "requests.log").open("a") as stream:
                stream.write(fmt % args + "\n")

        def respond(self, value, status=200):
            encoded = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path.startswith("/push/"):
                name = path.removeprefix("/push/")
                if name not in {
                    "approval",
                    "reply-background",
                    "reply-terminated",
                    "reply-recovery",
                }:
                    self.respond({}, 404)
                    return
                payload = dict(
                    aps={
                        "alert": {
                            "title": "Chief of Staff",
                            "body": "The notification reply stays in this conversation.",
                        },
                        "sound": "default",
                        "thread-id": "panels-chief",
                        "category": "PANELS_MESSAGE_REPLY",
                    },
                    server_url=base,
                    route="/#/agents/chief-of-staff",
                    reply=dict(
                        conversation_id="c_original",
                        message_id="c_original:" + name,
                        recipient_kind="chief",
                        recipient_id="chief",
                    ),
                )
                if name == "approval":
                    payload["aps"]["alert"] = dict(
                        title="Notification improvements",
                        body="What Changes waits for your approval.",
                    )
                    payload["aps"].pop("category")
                    payload.pop("reply")
                file = output / (name + ".apns")
                file.write_text(json.dumps(payload))
                result = subprocess.run(
                    ["xcrun", "simctl", "push", device, BUNDLE, str(file)], capture_output=True
                )
                self.respond(
                    dict(output=(result.stdout + result.stderr).decode()),
                    200 if result.returncode == 0 else 500,
                )
                return
            if path.startswith("/proof/"):
                with lock:
                    if path == "/proof/fail-preference":
                        state["fail_preference"] = True
                    elif path == "/proof/drop-reply-response":
                        state["drop_reply_response"] = True
                        state["hide_receipts"] = True
                    elif path == "/proof/recover":
                        state["hide_receipts"] = False
                    elif path != "/proof/state":
                        self.respond({}, 404)
                        return
                    record()
                    self.respond(state)
                return
            if path == "/api/live":
                live_socket_stub.serve(
                    self,
                    lambda subscription: [
                        ("snapshot", json.dumps(view_snapshot(json.loads(subscription["params"]["view"]))))
                    ],
                    changes=False,
                )
                return
            with lock:
                if path == "/api/notifications/settings":
                    value = settings()
                elif path == "/api/navigation/view/snapshot":
                    value = view_snapshot(json.loads(query["view"][0]))
                elif path == "/api/workers":
                    value = dict(workers=[], chief_of_staff=chief)
                elif path == "/api/skills":
                    value = dict(skills=[])
                elif path == "/api/workers/chief-of-staff/settings":
                    value = chief
                elif path == "/api/chief/conversation":
                    # Deliberately newer than the reply destination.
                    value = dict(conversation_id="c_replacement")
                elif path == "/api/updates":
                    value = dict(state="up_to_date", app_sha="running", allowed_actions=[], machines=[], available_machines=[],
                                 client_versions=[], interruption=dict(stops_agents=False, agents=[], machine_ids=[]))
                elif path == "/api/executor/health":
                    value = dict(
                        mode="local",
                        connection_state="ready",
                        identity=None,
                        last_contact_at_ms=None,
                    )
                elif path == "/api/resource-versions":
                    value = dict(
                        epoch="fixture",
                        resources=[
                            dict(key=key, revision=None) for key in json.loads(query["keys"][0])
                        ],
                    )
                elif path.endswith("/view/receipts"):
                    state["receipts"].append(
                        dict(
                            path=path,
                            ids=json.loads(query["message_ids"][0]),
                            available=not state["hide_receipts"],
                        )
                    )
                    ids = json.loads(query["message_ids"][0])
                    value = dict(
                        receipts=[]
                        if state["hide_receipts"]
                        else [
                            dict(
                                sender_message_id=entry["body"]["sender_message_id"], fate="prompt"
                            )
                            for entry in state["accepted"]
                            if entry["body"]["sender_message_id"] in ids
                        ]
                    )
                    record()
                elif path.endswith("/start-values") or path in {
                    "/api/conversation/conversations/c_original",
                    "/api/conversation/conversations/c_replacement",
                }:
                    value = dict(
                        backend_key="codex",
                        model="sol",
                        held_prompts=[],
                        owner_read_through_sequence=0,
                    )
                elif path == "/api/native-push/device-registration":
                    value = {}
                else:
                    self.respond(
                        dict(error=dict(message="Unexpected local fixture route " + path)), 404
                    )
                    return
                self.respond(value)

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            if self.path != "/api/notifications/preferences/tickets/awaiting_reply":
                self.respond({}, 404)
                return
            with lock:
                state["preference_writes"].append(body)
                if state["fail_preference"]:
                    state["fail_preference"] = False
                    record()
                    self.respond(dict(error=dict(message="Local preference save refused")), 503)
                    return
                state["enabled"] = body["enabled"]
                record()
                self.respond(settings())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            if self.path == "/api/native-push/device-registration":
                self.respond({})
                return
            if self.path != "/api/chief/conversation/send":
                self.respond({}, 404)
                return
            with lock:
                entry = dict(path=self.path, body=body)
                state["attempts"].append(entry)
                if not any(
                    old["body"]["sender_message_id"] == body["sender_message_id"]
                    for old in state["accepted"]
                ):
                    state["accepted"].append(entry)
                record()
                if state["drop_reply_response"]:
                    state["drop_reply_response"] = False
                    record()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.respond(dict(conversation_id="c_original", fate="started"))

    # Binding fails before Xcode starts if another task owns the surveyed port.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = dict(os.environ, TEST_RUNNER_PANELS_PUSH_STUB_URL=base)
    started = time.monotonic()
    result = 1
    try:
        result = subprocess.call(
            [
                "xcodebuild",
                "-project",
                str(ROOT / "ios/Panels.xcodeproj"),
                "-scheme",
                "PanelsReview",
                "-configuration",
                "Debug",
                "-destination",
                "id=" + device,
                "-derivedDataPath",
                str(output / "build"),
                "-resultBundlePath",
                str(output / "Notifications.xcresult"),
                "-jobs",
                "4",
                "-parallel-testing-enabled",
                "NO",
                "-maximum-concurrent-test-simulator-destinations",
                "1",
                "PANELS_APP_BUNDLE_IDENTIFIER=" + BUNDLE,
                "CODE_SIGNING_ALLOWED=NO",
                "-only-testing:PanelsReviewUITests/NotificationDeliveryTests/testDeliveryRepliesAndPreferences",
                "test",
            ],
            env=environment,
        )
        return result
    finally:
        (output / "review-summary.json").write_text(
            json.dumps(dict(exit_code=result, elapsed_seconds=round(time.monotonic() - started, 2)))
        )
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
