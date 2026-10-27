"""Real source app with one test-owned HTTP provider and no agent execution."""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from tests.e2e.conversation_signals_server import NoInstalledBackends, ScriptedChild

from planner.conversation.contracts import ConversationBackendKey
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.measure_sources.runtime import Runtime
from planner.server_lifecycle.application import ChangeStreamClosingServer

FIRST_TOKEN = "fixture-first-key"
SECOND_TOKEN = "fixture-replacement-key"


class Provider:
    def __init__(self) -> None:
        self.token = FIRST_TOKEN
        self.value = 42
        self.accepted = 0
        self.refused = 0
        self.post_queries = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65536))
                body = json.loads(raw)
                assert body["query"]["kind"] == "HogQLQuery"
                assert "count()" in body["query"]["query"]
                owner.post_queries += 1
                self.do_GET()

            def do_GET(self) -> None:
                authorized = self.headers.get("Authorization") == "Bearer " + owner.token
                if authorized:
                    owner.accepted += 1
                else:
                    owner.refused += 1
                body = {"results": [[25]]} if self.command == "POST" else {"value": owner.value}
                raw = json.dumps(body if authorized else {"error": "unauthorized"}).encode()
                self.send_response(200 if authorized else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def build_app(config: Config, provider: Provider) -> FastAPI:
    assert config.test_mode
    patch(
        "planner.conversation.snapshot.SubprocessBackendProbeEnvironment",
        NoInstalledBackends,
    ).start()

    def child(**kwargs: Any) -> ScriptedChild:
        return ScriptedChild(kwargs["event_sink"])

    app = create_app(
        config,
        build_clock(config),
        lambda: connect(config.db_path),
        backend_child_factories_for_test={key: child for key in ConversationBackendKey},
        source_runtime_for_test=Runtime(),
    )

    @app.get("/api/test/measure-provider")
    def state() -> dict[str, object]:
        return {
            "origin": provider.origin,
            "accepted": provider.accepted,
            "refused": provider.refused,
            "post_queries": provider.post_queries,
        }

    @app.post("/api/test/measure-provider/rotate")
    def rotate() -> dict[str, bool]:
        provider.token = SECOND_TOKEN
        provider.value = 43
        return {"ok": True}

    return app


def main() -> None:
    config = load_config("config.yaml")
    assert config.test_mode
    provider = Provider()
    try:
        app = build_app(config, provider)
        ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()
    finally:
        provider.stop()


if __name__ == "__main__":
    main()
