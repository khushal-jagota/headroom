#!/usr/bin/env python3
"""Opt-in, lane-locked APNs injection + tap proof. Never changes simulator lifecycle."""

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEVICE = "566D051B-B6BB-4046-9038-66C05CCC2B96"  # PanelsLaneB
BUNDLE = "app.panels.ios.pios-notify"
PORT = 18763


def main():
    if "--locked" not in sys.argv:
        return subprocess.call(
            ["lockf", "/tmp/panels-PanelsLaneB.lock", sys.executable, __file__, "--locked"]
        )
    devices = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"]))
    if not any(
        d["udid"] == DEVICE and d["state"] == "Booted"
        for group in devices["devices"].values()
        for d in group
    ):
        raise SystemExit("PanelsLaneB must already be booted by its owner.")
    output = Path(os.environ["PANELS_PUSH_TEST_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.removeprefix("/push/")
            if name not in {"ticket", "review", "orchestrator", "chief"}:
                self.send_error(404)
                return
            result = subprocess.run(
                [
                    "xcrun",
                    "simctl",
                    "push",
                    DEVICE,
                    BUNDLE,
                    str(ROOT / "ios/NotificationPayloads" / (name + ".apns")),
                ],
                capture_output=True,
            )
            self.send_response(200 if result.returncode == 0 else 500)
            self.end_headers()
            self.wfile.write(result.stdout + result.stderr)

    # Binding fails before the test starts if another lane owns this port.
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
                str(output / "Notifications.xcresult"),
                "-jobs",
                "4",
                "-parallel-testing-enabled",
                "NO",
                "-maximum-concurrent-test-simulator-destinations",
                "1",
                "PRODUCT_BUNDLE_IDENTIFIER=" + BUNDLE,
                "CODE_SIGNING_ALLOWED=NO",
                "-only-testing:PanelsTests/NotificationTests",
                "-only-testing:PanelsNotificationUITests/NotificationDeliveryTests",
                "test",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
