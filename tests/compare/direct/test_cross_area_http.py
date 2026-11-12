"""Fresh Python parity and the accepted credential redirect exception on loopback."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx

from planner.measure_sources.transport import request as source_request
from planner.stalls.alarms import message_chief
from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]


def test_cross_area_http(tmp_path: Path) -> None:
    started = time.perf_counter()
    seen: list[dict[str, Any]] = []
    second_origin = ""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def do_POST(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append({"port": cast(ThreadingHTTPServer, self.server).server_port, "path": self.path,
                         "secret": self.headers.get("X-Synthetic-Key"),
                         "custom": self.headers.get("X-Test-Secret"),
                         "authorization": self.headers.get("Authorization")})
            destination = None
            status = 302
            if self.path == "/same":
                destination = "/final"
            elif self.path in ("/cross", "/updates"):
                destination = second_origin + ("/update-final" if self.path == "/updates" else "/final")
            elif self.path == "/api/messages/send":
                destination = second_origin + "/chief-final"
                status = 307
            elif self.path == "/rewrite":
                destination = "/method"
            elif self.path.startswith("/chain/"):
                count = int(self.path.rsplit("/", 1)[1])
                if count:
                    destination = f"/chain/{count - 1}"
            if destination is not None:
                self.send_response(status)
                self.send_header("Location", destination)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = (b'{"workflow_runs": []}' if self.path in ("/update-final", "/slow", "/idle", "/slow-chief/api/messages/send", "/idle-chief/api/messages/send")
                    else self.command.encode() if self.path == "/method" else b"{}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if self.path in ("/slow", "/idle", "/slow-chief/api/messages/send", "/idle-chief/api/messages/send"):
                    for offset in range(0, len(body), 4):
                        time.sleep(0.08 if self.path.startswith("/slow") else 0.4)
                        self.wfile.write(body[offset:offset + 4])
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    first, second_origin = [f"http://127.0.0.1:{server.server_port}" for server in servers]
    for thread in threads:
        thread.start()
    try:
        assert source_request(first + "/cross").status_code == 200
        assert source_request(first + "/cross", headers={"X-Synthetic-Key": "synthetic-secret"}).status_code == 200
        assert seen[-1]["port"] == servers[1].server_port
        assert seen[-1]["secret"] == "synthetic-secret"
        assert source_request(first + "/rewrite", method="POST", json_body={"value": 1}).text == "GET"
        assert source_request(first + "/chain/20").status_code == 200
        try:
            source_request(first + "/chain/21")
        except httpx.TooManyRedirects:
            pass
        else:
            raise AssertionError("Python accepted 21 redirects")
        assert not message_chief("synthetic", server_url=first)
        with patch("planner.stalls.alarms.CHIEF_MESSAGE_TIMEOUT_SECONDS", 0.2):
            assert message_chief("synthetic", server_url=first + "/slow-chief")
            assert not message_chief("synthetic", server_url=first + "/idle-chief")
        with httpx.Client(timeout=0.2, follow_redirects=True, trust_env=False) as client:
            assert client.get(first + "/slow").json() == {"workflow_runs": []}
            try:
                client.get(first + "/idle")
            except httpx.ReadTimeout:
                pass
            else:
                raise AssertionError("Python did not time out an idle response")
        seen.clear()
        spec = tmp_path / "spec.json"
        token = tmp_path / "token"
        token.write_text("synthetic-secret")
        spec.write_text(json.dumps({"first": first, "second": second_origin, "token": str(token)}))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("PLAN_") and key not in
               {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}}
        env.update(create_synthetic_homes(tmp_path))
        env.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:1")
        completed = subprocess.run(
            ["node", str(ROOT / "server/testing/cross_area_http.ts"), str(spec)],
            cwd=ROOT, env=env, text=True, capture_output=True, check=True, timeout=40,
        )
        actual = json.loads(completed.stdout)
        assert actual == {"source_failure": None, "source_values": [1], "stall": False,
                          "update": None, "slow": True, "idle": True,
                          "stallSlow": True, "stallIdle": False}
        second_requests = [item for item in seen if item["port"] == servers[1].server_port]
        paths = [item["path"] for item in second_requests]
        assert paths[0] == "/final"
        assert paths[1:] and set(paths[1:]) == {"/update-final"}
        assert all(item["secret"] is None and item["custom"] is None and
                   item["authorization"] is None for item in second_requests)
        same_origin = [item for item in seen if item["path"] == "/final" and
                       item["port"] == servers[0].server_port]
        assert same_origin[0]["secret"] == "synthetic-secret"
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
    print(f"cross-area HTTP proof: {time.perf_counter() - started:.3f}s")
