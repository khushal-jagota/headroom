#!/usr/bin/env python3
"""Local-only conversation operations proof on an already-booted assigned lane.

Uses one HTTP stub and one existing UI-test host. No live server or simulator
lifecycle changes. Capture files and build output belong in the supplied directory.
"""

import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DEVICE = os.environ.get("PANELS_CONVERSATION_DEVICE", "5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6")
LANE = os.environ.get("PANELS_CONVERSATION_LANE", "PanelsLaneA")
BUNDLE = os.environ.get("PANELS_CONVERSATION_BUNDLE", "app.panels.ios.pios-convo-build")


def row(
    key: str, text: str, own: bool = False, sender: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "row",
        "key": key,
        "sender_message_id": sender,
        "turn_key": "turn-" + (sender or key.removeprefix("reply-")),
        "body": {
            "kind": "inline",
            "value": {
                "kind": kind or ("prompt" if own else "agent_message"),
                "content": [{"piece": "text", "text": text}],
            },
        },
    }


class Stub:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.lock = threading.Lock()
        self.recording: subprocess.Popen[bytes] | None = None
        self.reset()

    def reset(self) -> None:
        self.items = [
            row(
                f"history-{i}",
                (
                    f"Review {i + 1}: the conversation keeps each message in place "
                    "while work continues."
                    "\n\nThe page can be read independently of the composer."
                ),
            )
            for i in range(18)
        ]
        self.items.append(row("welcome", "The plan is ready. What should I work on next?"))
        self.pending: list[dict[str, Any]] = []
        self.live: dict[str, Any] | None = None
        self.offline = False
        self.refused = False
        self.running_until = 0

    def tick(self) -> None:
        now = time.monotonic()
        self.live = None
        for sent in list(self.pending):
            age = now - sent["at"]
            if age >= sent["delay"] and not sent["delivered"]:
                self.items.append(row(sent["id"], sent["text"], True, sent["id"]))
                sent["delivered"] = True
            if sent["delivered"]:
                reply = (
                    "I’ll keep the conversation steady as messages arrive. "
                    "Your message is delivered, "
                    "and the reply grows in place. You can scroll up to read earlier work "
                    "and return with Latest whenever you’re ready."
                )
                if sent["id"] == "latest-proof":
                    reply = (
                        "\n\n".join(f"Streaming paragraph {i}: " + reply for i in range(1, 8))
                        + "\n\nEnd of streamed reply."
                    )
                length = max(0, min(len(reply), int((age - sent["delay"]) * 30)))
                key = "reply-" + sent["id"]
                if length == len(reply):
                    self.items.append(row(key, reply))
                    self.pending.remove(sent)
                elif length:
                    self.live = {
                        "turn_key": "turn-" + sent["id"],
                        "text": reply[:length],
                        "version": "stub:turn-" + sent["id"] + ":0",
                    }

    def start_clip(self, name: str) -> None:
        self.stop_clip()
        self.recording = subprocess.Popen(
            [
                "xcrun",
                "simctl",
                "io",
                DEVICE,
                "recordVideo",
                "--codec=h264",
                "--force",
                str(self.output / (name + ".mp4")),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert self.recording.stderr is not None
        while True:
            line = self.recording.stderr.readline()
            if b"Recording started" in line or not line:
                break

    def stop_clip(self) -> None:
        if self.recording:
            self.recording.send_signal(signal.SIGINT)
            self.recording.communicate(timeout=15)
            self.recording = None


def main() -> int:
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
    assert LANE in {"PanelsLaneA", "PanelsLaneB", "PanelsLaneC"}, "Use an assigned Panels lane"
    assert BUNDLE.startswith("app.panels.ios.pios-"), "Use a separate task bundle"
    output = Path(os.environ["PANELS_CONVERSATION_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    devices = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"]))
    assert any(
        d["udid"] == DEVICE and d["name"] == LANE and d["state"] == "Booted"
        for group in devices["devices"].values()
        for d in group
    ), f"{LANE} must already be booted"
    stub = Stub(output)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            with (output / "stub-requests.log").open("a") as log:
                log.write(fmt % args + "\n")

        def respond(self, value: object, status: int = 200) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/proof/start/"):
                stub.start_clip(path.rsplit("/", 1)[-1])
                self.respond({})
                return
            if path == "/proof/stop":
                stub.stop_clip()
                self.respond({})
                return
            if path == "/proof/recovery":
                with stub.lock:
                    recovery = row(
                        "recovery",
                        "Inspect the interrupted work and continue the current authorized step.",
                        kind="prompt",
                    )
                    recovery["body"]["value"]["sender_label"] = "Panels deployment recovery"
                    stub.items.append(recovery)
                self.respond({})
                return
            if path == "/proof/stream":
                with stub.lock:
                    stub.pending.append(
                        {
                            "id": "latest-proof",
                            "text": "",
                            "at": time.monotonic(),
                            "delay": 0,
                            "delivered": True,
                        }
                    )
                self.respond({})
                return
            if path == "/proof/finish":
                with stub.lock:
                    for pending in stub.pending:
                        pending["at"] -= 200
                    stub.tick()
                self.respond({})
                return
            if path == "/proof/reset":
                with stub.lock:
                    stub.reset()
                self.respond({})
                return
            if path in {"/proof/offline", "/proof/online"}:
                stub.offline = path.endswith("/offline")
                self.respond({})
                return
            if stub.offline:
                self.respond(
                    {"error": {"message": "Connection unavailable. Your words are saved."}}, 503
                )
                return
            if path.endswith("/view/tail"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(b"event: status\ndata: {}\n\n")
                        self.wfile.flush()
                        time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError):
                    return
            with stub.lock:
                stub.tick()
                if path.endswith("/view/body"):
                    reference = json.loads(parse_qs(urlparse(self.path).query)["reference"][0])
                    text = (stub.live or {}).get("text", "")[: reference["byte_count"]]
                    self.respond({"text": text, "next_offset": None})
                elif path.endswith("/view/receipts"):
                    ids = json.loads(parse_qs(urlparse(self.path).query)["message_ids"][0])
                    self.respond(
                        {
                            "receipts": [
                                {
                                    "sender_message_id": item["sender_message_id"],
                                    "fate": item["body"]["value"]["kind"],
                                }
                                for item in stub.items
                                if item.get("sender_message_id") in ids
                            ]
                        }
                    )
                elif path.endswith("/view"):
                    latest = stub.items[-1]["body"]["value"]
                    full = parse_qs(urlparse(self.path).query).get("lens") == ["full"]
                    live = stub.live if full else None
                    output = (
                        None
                        if not live
                        else {
                            "turn_key": live["turn_key"],
                            "reference": {
                                "body_id": "live",
                                "version": live["version"],
                                "byte_count": len(live["text"].encode()),
                                "media_type": "text/markdown",
                            },
                        }
                    )
                    self.respond(
                        {
                            "page": {"items": stub.items, "older": None},
                            "status": {
                                "authority": "current",
                                "turn_running": bool(stub.pending),
                                "live_output": output,
                                "rest": {
                                    "latest_happened": {
                                        "kind": latest["kind"],
                                        "excerpt": latest["content"][0]["text"],
                                    },
                                    "latest_tool": None,
                                    "permission_title_excerpt": None,
                                    "plan_progress": {"current_text_excerpt": None},
                                },
                            },
                        }
                    )
                elif path.endswith("/backends"):
                    self.respond(
                        {
                            "backends": [
                                {
                                    "backend_key": key,
                                    "installed": True,
                                    "available_models": [
                                        {
                                            "model_id": name,
                                            "display_name": name,
                                            "enabled": True,
                                            "reasoning_effort_options": ["low", "medium", "high"],
                                        }
                                    ],
                                    "reasoning_effort_options": ["low", "medium", "high"],
                                    "default_model_id": name,
                                    "default_reasoning_effort": "medium",
                                }
                                for key, name in [
                                    ("codex", "sol"),
                                    ("claude", "Opus"),
                                    ("hermes", "Hermes"),
                                ]
                            ]
                        }
                    )
                elif path.endswith("/conversations/stub") or path.endswith("/start-values"):
                    self.respond(
                        {
                            "backend_key": "codex",
                            "model": "sol",
                            "reasoning_effort": "medium",
                            "latest_sequence": len(stub.items),
                            "owner_read_through_sequence": len(stub.items),
                            "held_prompts": [
                                {
                                    "held_prompt_id": p["id"],
                                    "sender_message_id": p["id"],
                                    "content": [{"piece": "text", "text": p["text"]}],
                                }
                                for p in stub.pending
                                if not p["delivered"]
                            ],
                        }
                    )
                elif path == "/api/chief/conversation":
                    self.respond({"conversation_id": "stub"})
                else:
                    self.respond({"error": {"message": "Unexpected stub route: " + path}}, 404)

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if "/held-prompts/" not in path:
                self.respond({}, 404)
                return
            identity = path.rsplit("/", 1)[-1]
            with stub.lock:
                held = next((item for item in stub.pending if item["id"] == identity), None)
                if held:
                    stub.pending.remove(held)
                    stub.items.append(
                        row(identity, held["text"], True, identity, "prompt_discarded")
                    )
                self.respond({"discarded": held is not None})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path.endswith("/interrupt"):
                with stub.lock:
                    stub.pending = []
                self.respond({})
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if "/held-prompts/" in path and path.endswith("/promote"):
                identity = path.split("/")[-2]
                with stub.lock:
                    held = next((item for item in stub.pending if item["id"] == identity), None)
                    if held:
                        held["delay"] = 0
                        stub.tick()
                    self.respond({"promoted": held is not None, "fate": "injected"})
                return
            if not path.endswith("/conversation/send"):
                self.respond({}, 404)
                return
            time.sleep(3)  # expose the optimistic Sending state
            with stub.lock:
                text = "\n".join(p.get("text", p.get("file_name", "")) for p in data["content"])
                sender = data["sender_message_id"]
                if "Fail once" in text and not stub.refused:
                    stub.refused = True
                    self.respond(
                        {
                            "conversation_id": "stub",
                            "fate": "refused",
                            "refusal_reason": "backend_did_not_start",
                        }
                    )
                    return
                if "Suppress" in text:
                    self.respond(
                        {
                            "conversation_id": "stub",
                            "fate": "suppressed",
                            "reason": "no longer current",
                        }
                    )
                    return
                queued = data.get("mode") == "queue"
                stub.pending.append(
                    {
                        "id": sender,
                        "text": text,
                        "at": time.monotonic(),
                        "delay": (120 if "Remove" in text else 5) if queued else 0.4,
                        "delivered": False,
                    }
                )
                self.respond({"conversation_id": "stub", "fate": "queued" if queued else "started"})

    # Let the OS select an unused loopback port, so occupied ports never cause lane collisions.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    (output / "stub-url.txt").write_text(url)
    environment = os.environ.copy()
    environment["TEST_RUNNER_PANELS_CONVERSATION_STUB_URL"] = url
    environment["TEST_RUNNER_PANELS_LATEST_BASELINE"] = os.environ.get(
        "PANELS_LATEST_BASELINE", "0"
    )
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
                str(output / "DD"),
                "-resultBundlePath",
                str(output / f"Conversation-{int(time.time())}.xcresult"),
                "-jobs",
                "4",
                "-parallel-testing-enabled",
                "NO",
                "-collect-test-diagnostics",
                "never",
                "-maximum-concurrent-test-simulator-destinations",
                "1",
                "PANELS_APP_BUNDLE_IDENTIFIER=" + BUNDLE,
                "CODE_SIGNING_ALLOWED=NO",
                "-only-testing:PanelsNotificationUITests/"
                + os.environ.get("PANELS_CONVERSATION_TEST", "ConversationOperationTests"),
                "test",
            ],
            env=environment,
        )
    finally:
        stub.stop_clip()
        subprocess.run(["xcrun", "simctl", "terminate", DEVICE, BUNDLE], check=False)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
