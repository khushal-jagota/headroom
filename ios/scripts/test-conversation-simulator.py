#!/usr/bin/env python3
"""Local-only conversation operations proof on an already-booted assigned lane.

Uses one HTTP stub and one existing UI-test host. No live server or simulator
lifecycle changes. Capture files and build output belong in the supplied directory.
"""

import hashlib
import http.server
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import live_socket_stub

ROOT = Path(__file__).resolve().parents[2]
DEVICE = os.environ.get("PANELS_CONVERSATION_DEVICE", "5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6")
LANE = os.environ.get("PANELS_CONVERSATION_LANE", "PanelsLaneA")
BUNDLE = os.environ.get("PANELS_CONVERSATION_BUNDLE", "app.panels.ios.pios-convo-build")


def review_png() -> bytes:
    """A 48x48 full-colour picture with a transparent margin, like a real driver PNG."""
    size, margin = 48, 5

    def pixel(x: int, y: int) -> bytes:
        inside = margin <= x < size - margin and margin <= y < size - margin
        return b"\xf0\x8a\x24\xff" if inside else b"\0\0\0\0"

    raw = b"".join(b"\0" + b"".join(pixel(x, y) for x in range(size)) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + checksum

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# Review-only driver list (--driver-review). The two bundled built-ins, one driver with a
# PNG icon and one with only an SVG icon. Hermes is left out on purpose: its stored key is
# still served as a backend, which the list does not name.
REVIEW_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    b'<circle cx="8" cy="8" r="6" fill="currentColor"/></svg>'
)
REVIEW_ICONS: dict[str, tuple[bytes, str]] = {
    "codex": ((ROOT / "server/drivers/codex/icon.svg").read_bytes(), "image/svg+xml"),
    "claude": ((ROOT / "server/drivers/claude/icon.svg").read_bytes(), "image/svg+xml"),
    "review-png": (review_png(), "image/png"),
    "review-svg": (REVIEW_SVG, "image/svg+xml"),
}
REVIEW_DRIVERS = {
    "drivers": [
        {
            "driver_kind": kind,
            "label": label,
            "icon": {
                "url": f"/api/drivers/{kind}/icon?v="
                + hashlib.sha256(REVIEW_ICONS[kind][0]).hexdigest()[:12],
                "media_type": REVIEW_ICONS[kind][1],
                "monochrome": kind != "review-png",
            },
            "accent_color": "#d97757",
            "checks_for_updates": kind in {"codex", "claude"},
        }
        for kind, label in [
            ("codex", "Codex"),
            ("claude", "Claude"),
            ("review-png", "Pixel Agent"),
            ("review-svg", "Vector Agent"),
        ]
    ],
    "default_backend_instance_id": "codex",
}


def row(
    key: str,
    text: str,
    own: bool = False,
    sender: str | None = None,
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
        self.uploads: dict[str, dict[str, Any]] = {}
        self.video_sends: dict[str, Any] = {}
        self.running_until = 0

    def tick(self) -> None:
        now = time.monotonic()
        self.live = None
        for sent in list(self.pending):
            age = now - sent["at"]
            if age >= sent["delay"] and not sent["delivered"]:
                self.items.append(row(sent["id"], sent["text"], True, sent["id"]))
                if sent.get("content"):
                    self.items[-1]["body"]["value"]["content"] = sent["content"]
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
    server_only = "--server-only" in sys.argv
    review = "--review" in sys.argv
    # Review-only: serve the driver list, its icons and two more backends, and/or say the
    # conversation's backend cannot join a running turn. The permanent run serves neither,
    # so it keeps exercising the app's fallback.
    driver_review = "--driver-review" in sys.argv
    no_steer = "--no-steer" in sys.argv
    # Review-only: the conversation runs on hermes, whose card lists no efforts.
    hermes_conversation = "--hermes-conversation" in sys.argv
    if (driver_review or no_steer or hermes_conversation) and not review:
        raise SystemExit(
            "--driver-review, --no-steer and --hermes-conversation are review-only; add --review"
        )
    if hermes_conversation and not driver_review:
        raise SystemExit("--hermes-conversation needs --driver-review")
    if review and not server_only and "/" not in os.environ.get("PANELS_CONVERSATION_TEST", ""):
        raise SystemExit("--review requires one explicit class/method in PANELS_CONVERSATION_TEST")
    if not server_only and "--locked" not in sys.argv:
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
    # A task may bring its own simulator: name the device as the lane and pass its UDID.
    assigned_lane = LANE in {"PanelsLaneA", "PanelsLaneB", "PanelsLaneC"}
    assert assigned_lane or "PANELS_CONVERSATION_DEVICE" in os.environ, (
        "Use an assigned Panels lane or your own named simulator"
    )
    assert BUNDLE.startswith("app.panels.ios.pios-"), "Use a separate task bundle"
    output = Path(os.environ["PANELS_CONVERSATION_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not server_only:
        devices = json.loads(
            subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"])
        )
        assert any(
            d["udid"] == DEVICE and d["name"] == LANE and d["state"] == "Booted"
            for group in devices["devices"].values()
            for d in group
        ), f"{LANE} must already be booted"
        original_appearance = subprocess.check_output(
            ["xcrun", "simctl", "ui", DEVICE, "appearance"], text=True
        ).strip()

    def native_control_allowed() -> bool:
        if not server_only:
            return True
        try:
            return os.environ.get("PANELS_OWNED_UDID") == DEVICE and time.time() < float(
                os.environ["PANELS_WORK_END"]
            )
        except (KeyError, ValueError):
            return False

    stub = Stub(output)
    fixtures = ROOT / "ios/PanelsTests/Fixtures"
    ticket = json.loads((fixtures / "ticket-full.json").read_text())
    ticket["conversation_id"] = "stub"
    manager = json.loads((fixtures / "manager.json").read_text())
    if driver_review:
        # The Ticket chip and Ticket worker setup show only for an editable configuration.
        ticket["employee_configuration_editable"] = True
    manager["manager"]["conversation_id"] = "stub"
    manifest = {
        "worker_types": [
            {
                "worker_type": "product_design",
                "label": "Product design",
                "stages": [
                    {
                        "id": "needs_brief",
                        "label": "Brief",
                        "gating_field": "brief",
                        "is_terminal": False,
                        "ownership_mode": "worker",
                    },
                    {
                        "id": "needs_wireframe",
                        "label": "Wireframe",
                        "gating_field": "wireframe",
                        "is_terminal": False,
                        "ownership_mode": "worker",
                    },
                    {"id": "done", "label": "Done", "is_terminal": True},
                ],
                "fields": [
                    {"id": "brief", "label": "Brief"},
                    {"id": "wireframe", "label": "Wireframe"},
                ],
            }
        ]
    }

    def backends_body() -> dict[str, Any]:
        if not driver_review:
            return {
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
                    for key, name in [("codex", "sol"), ("claude", "Opus"), ("hermes", "Hermes")]
                ]
            }
        efforts = ["low", "medium", "high"]

        def card(key: str, model: dict[str, Any], card_efforts: list[str]) -> dict[str, Any]:
            return {
                "backend_key": key,
                "installed": True,
                "available_models": [{"enabled": True, "option_descriptors": [], **model}],
                "reasoning_effort_options": card_efforts,
                "default_model_id": model["model_id"],
                "default_reasoning_effort": "medium" if card_efforts else None,
                "can_sign_in": False,
                "reports_allowance_usage": key in {"codex", "claude"},
                "version": None,
                "cached_usage": None,
                "diagnoses": [],
            }

        def model(model_id: str, name: str, model_efforts: list[str] | None) -> dict[str, Any]:
            return {
                "model_id": model_id,
                "display_name": name,
                "reasoning_effort_options": model_efforts,
                "resolved_display_name": None,
            }

        return {
            "backends": [
                # "sol" resolves to a dated model the server names, so the Workspace
                # preview shows "Sol (November)", neither the id nor the dated id.
                card("codex", {
                    **model("sol", "Sol", efforts),
                    "resolved_model_id": "sol-2026-11-01",
                    "selected_variant": "sol",
                    "resolved_display_name": "Sol (November)",
                }, efforts),
                card("claude", model("opus", "Opus", efforts), efforts),
                # As a real hermes card: no efforts on the card or its model.
                card("hermes", model("hermes-default", "Hermes default", None), []),
                card("review-png", model("pixel", "Pixel", efforts), efforts),
                card("review-svg", model("vector", "Vector", efforts), efforts),
            ]
        }

    # Settings > Workers and the Workspace Chief card, review-only. One Worker names a
    # model, the other only a backend, so its subtitle shows the backend's label.
    chief_detail = {
        "employee_id": "chief",
        "label": "Chief of Staff",
        "recap": manager["recap"],
        "skill": {"name": "panels-chief-of-staff", "description": "", "markdown_body": ""},
        "launch_defaults": {
            "employee_backend": "hermes" if hermes_conversation else "codex",
            "employee_launch_model": None,
            "employee_launch_reasoning_effort": None,
        },
    }
    review_workers = [
        {
            "worker_type": "product_design",
            "label": "Product design",
            "specialist_skill_name": "panels-worker-product-design",
            "launch_defaults": {
                "employee_backend": "claude",
                "employee_launch_model": None,
                "employee_launch_reasoning_effort": None,
            },
        },
        {
            "worker_type": "coding",
            "label": "Coding",
            "specialist_skill_name": "panels-worker-coding",
            "launch_defaults": {
                "employee_backend": "codex",
                "employee_launch_model": "sol",
                "employee_launch_reasoning_effort": "high",
            },
        },
    ]

    def navigation_snapshot(request: dict[str, Any]) -> dict[str, Any]:
        # Same scoped snapshot envelope as the offline Review host. The detail host
        # needs Chief's current address, while its page fixtures own their content.
        content = {
            "records": [{"kind": "chief", "id": "chief", "value": {
                "label": "Chief of Staff", "conversation_id": "stub",
            }}],
            "windows": [{
                "window_id": json.dumps([window["section"], window["owner"]]),
                "request": window, "members": [], "total": 0,
                "previous": None, "next": None,
            } for window in request["windows"]],
            "counts": {
                "running_conversations": 0, "out_claims": 0,
                "review": 0, "unread": 0, "settled": 0,
            },
            "review_context": [],
        }
        return {
            "event": "snapshot", "wire_version": 3, "projection_version": 7,
            "view_id": "fixture", "scope_generation": request["scope_generation"],
            "ordering_generation": "fixture", "content_version": "detail-fixture",
            "coverage": {"scope": "fixture", "schema_version": 11,
                "semantics": "attention-knowledge-v11-recap-freshness",
                "epoch": "fixture", "revision": "1"},
            "mode": "current", "authority": {
                "delivery_session_id": "fixture",
                "availability": {"state": "current", "generation": "1", "reason": None},
                "permit": {"scope": "fixture", "runtime_epoch": "fixture",
                    "installation_id": "fixture", "generation": "1"},
            }, "content": content,
        }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            with (output / "stub-requests.log").open("a") as log:
                log.write(fmt % args + "\n")

        def conversation_view(self, full: bool) -> dict[str, Any]:
            latest = stub.items[-1]["body"]["value"]
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
            return {
                "page": {"items": stub.items, "older": None},
                "coverage": {"through_sequence": len(stub.items)},
                "status": {
                    "authority": "current",
                    "turn_running": bool(stub.pending),
                    "live_output": output,
                    "rest": {
                        "latest_happened": {
                            "kind": latest["kind"],
                            "sender_label": latest.get("sender_label"),
                            "excerpt": latest["content"][0]["text"],
                        },
                        "latest_tool": None,
                        "permission_title_excerpt": None,
                        "plan_progress": {"current_text_excerpt": None},
                    },
                },
            }

        def respond_bytes(self, body: bytes, media_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(body)

        def respond(self, value: object, status: int = 200) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/live":
                def frames(subscription):
                    if subscription["kind"] == "navigation":
                        return [("snapshot", json.dumps(
                            navigation_snapshot(json.loads(subscription["params"]["view"]))))]
                    # The app applies tail frames instead of reading /view again. A snapshot
                    # frame may arrive at any point, so the stub sends its current view.
                    full = subscription["params"].get("lens") == "full"
                    with stub.lock:
                        stub.tick()
                        return [("snapshot", json.dumps(self.conversation_view(full)))]
                live_socket_stub.serve(
                    self, frames, periods={"navigation": 1, "conversation_view": 0.5},
                    changes=False, alive=lambda: not stub.offline,
                )
                return
            if path == "/api/navigation/view/snapshot":
                snapshot = navigation_snapshot(json.loads(parse_qs(urlparse(self.path).query)["view"][0]))
                self.respond(snapshot)
                return
            if path == "/api/goals":
                self.respond({
                    "goals": [], "status_date": "2026-11-15",
                    "next_refresh_at": time.time() + 3600,
                })
                return
            if path == "/api/projects":
                self.respond({"projects": []})
                return
            holder_pages = {"/api/chief/page", "/api/managers/si_4chgag3w/page"}
            if path in holder_pages:
                self.respond({"page": {"blocks": []}, "page_revision": 1})
                return
            if path.removesuffix("/readings") in holder_pages and path.endswith("/readings"):
                self.respond({"goal_id": "fixture", "revision": 1, "status": "neutral",
                    "status_date": "2026-11-15", "next_refresh_at": time.time() + 3600,
                    "values": [], "sources": [], "charts": {}})
                return
            if path == "/api/managers/si_4chgag3w/workspace":
                self.respond(manager)
                return
            if path == "/api/managers/si_4chgag3w/workspace-content":
                self.respond({"goal": manager["goal"], "recap": manager["recap"]})
                return
            if path == f"/api/tickets/{ticket['id']}/workspace":
                # Native detail reads the scoped workspace; retain the same fixture controls.
                self.respond(ticket)
                return
            if path == "/api/tickets":
                query = parse_qs(urlparse(self.path).query)
                if query.get("detail") == ["full"]:
                    self.respond(ticket)
                else:
                    rows = manager["tickets"] if query.get("is_backlogged") == ["false"] else []
                    self.respond(
                        {
                            "tickets": rows,
                            "page": {
                                "match_count": len(rows),
                                "return_count": len(rows),
                                "limit": 30,
                                "offset": 0,
                                "complete": True,
                            },
                        }
                    )
                return
            if path == "/api/worker-types/product_design/manifest":
                self.respond(manifest)
                return
            if path == "/api/workers":
                chief = {
                    "label": "Chief of Staff",
                    "recap": manager["recap"],
                    "conversation_signal": manager["conversation_signal"],
                }
                if driver_review:
                    self.respond(
                        {"chief_of_staff": {**chief_detail, **chief}, "workers": review_workers}
                    )
                else:
                    self.respond({"chief_of_staff": chief})
                return
            if driver_review and path == "/api/workers/chief-of-staff/settings":
                self.respond(chief_detail)
                return
            if driver_review and path == "/api/skills":
                self.respond({"skills": []})
                return
            if path.startswith("/api/artifact-catalog/"):
                self.respond(
                    {"revision": "fixture", "directory": "", "offset": 0, "total": 0, "entries": []}
                )
                return
            if path.startswith("/proof/start/"):
                if (
                    os.environ.get("PANELS_CONVERSATION_RECORD_CLIPS", "0" if server_only else "1")
                    == "1"
                ):
                    if not native_control_allowed():
                        self.respond({"error": "An owned device and active turn are required"}, 409)
                        return
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
            if path in {"/proof/appearance/light", "/proof/appearance/dark"}:
                if not native_control_allowed():
                    self.respond({"error": "An owned device and active turn are required"}, 409)
                    return
                subprocess.run(
                    ["xcrun", "simctl", "ui", DEVICE, "appearance", path.rsplit("/", 1)[-1]],
                    check=True,
                )
                self.respond({})
                return
            if path == "/proof/header-fixture":
                with stub.lock:
                    stub.reset()
                    stub.items.append(
                        row("header-own", "Keep this message visible.", True, "header-own")
                    )
                    stub.items.append(
                        row("header-reply", "I’ll keep the conversation steady as messages arrive.")
                    )
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
            if path == "/proof/videos":
                self.respond({"uploads": list(stub.uploads.values()), "sends": list(stub.video_sends.values()), "own_rows": sum(1 for item in stub.items if item.get("sender_message_id") in stub.video_sends)})
                return
            if path == "/files/review-video.mp4":
                self.respond_bytes((stub.output / "uploaded-video.mp4").read_bytes(), "video/mp4")
                return
            if path.startswith("/api/conversation/video-uploads/"):
                identity = path.rsplit("/", 1)[-1]
                self.respond(stub.uploads.get(identity, {}), 200 if identity in stub.uploads else 404)
                return
            if stub.offline:
                self.respond(
                    {"error": {"message": "Connection unavailable. Your words are saved."}}, 503
                )
                return
            if driver_review and path == "/api/drivers":
                self.respond(REVIEW_DRIVERS)
                return
            if driver_review and path.startswith("/api/drivers/") and path.endswith("/icon"):
                icon = REVIEW_ICONS.get(path.split("/")[3])
                if icon is None:
                    self.respond({"error": {"message": "Unknown driver"}}, 404)
                else:
                    self.respond_bytes(*icon)
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
                    full = parse_qs(urlparse(self.path).query).get("lens") == ["full"]
                    self.respond(self.conversation_view(full))
                elif path.endswith("/backends"):
                    self.respond(backends_body())
                elif path.endswith("/conversations/stub") or path.endswith("/start-values"):
                    self.respond(
                        {
                            **(
                                # A stored effort the hermes card does not offer: the
                                # Workspace preview must not show it.
                                {"backend_key": "hermes", "model": "hermes-default"}
                                if hermes_conversation
                                else {"backend_key": "codex", "model": "sol"}
                            ),
                            **({"supports_steer": False} if no_steer else {}),
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

        def do_PUT(self) -> None:
            identity = urlparse(self.path).path.split("/")[-2]
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if stub.offline:
                self.respond({"error": {"message": "Connection unavailable"}}, 503)
                return
            (stub.output / "uploaded-video.mp4").write_bytes(body)
            upload = stub.uploads[identity]
            upload.update(state="ready", received_bytes=len(body), media_type="video/mp4")
            self.respond(upload)

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/conversation/video-uploads/"):
                identity = path.rsplit("/", 1)[-1]
                if identity in stub.uploads: stub.uploads[identity]["state"] = "cancelled"
                self.respond({})
                return
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
            if driver_review and path == "/api/conversation/backends":
                # A Ticket's own catalogue is read by POST with its ticket_id.
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.respond(backends_body())
                return
            if path.endswith("/interrupt"):
                with stub.lock:
                    stub.pending = []
                self.respond({})
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if path == "/api/conversation/video-uploads":
                if stub.offline:
                    self.respond({"error": {"message": "Connection unavailable"}}, 503)
                    return
                identity = "video-" + str(len(stub.uploads) + 1)
                upload = {"upload_id": identity, **data, "received_bytes": 0, "state": "reserved", "media_type": None, "expires_at": time.time() + 86400, "error": None}
                stub.uploads[identity] = upload
                self.respond(upload)
                return
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
            if stub.offline:
                self.respond({"error": {"message": "Connection unavailable"}}, 503)
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
                video = any(p["piece"] == "attachment_upload" for p in data["content"])
                if video:
                    if sender in stub.video_sends:
                        self.respond({"conversation_id": "stub", "fate": "started"})
                        return
                    stub.video_sends[sender] = data
                queued = data.get("mode") == "queue"
                stub.pending.append(
                    {
                        "id": sender,
                        "text": text,
                        "at": time.monotonic(),
                        "delay": (120 if "Remove" in text else 5) if queued else 0.4,
                        "delivered": False,
                        "content": [{"piece": "text", "text": text}, {"piece": "file", "file_name": "Video.mp4", "path": "/files/review-video.mp4", "media_type": "video/mp4"}] if video else None,
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
        if server_only:
            # The caller owns build, test, device and deadline. Startup starts
            # only the existing loopback fixture, without native preflight.
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            (output / "fixture-ready.json").write_text(
                json.dumps({"PANELS_CONVERSATION_STUB_URL": url})
            )
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
                (
                    "-only-testing:PanelsReviewUITests/"
                    if review else "-only-testing:PanelsNotificationUITests/"
                )
                + os.environ.get("PANELS_CONVERSATION_TEST", "ConversationOperationTests"),
                "test",
            ],
            env=environment,
        )
    finally:
        stub.stop_clip()
        if not server_only:
            subprocess.run(
                ["xcrun", "simctl", "ui", DEVICE, "appearance", original_appearance], check=True
            )
            subprocess.run(["xcrun", "simctl", "terminate", DEVICE, BUNDLE], check=False)
        stub.offline = True
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
