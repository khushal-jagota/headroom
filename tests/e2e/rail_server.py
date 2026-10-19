"""Isolated rail host with the application runtime and scripted backend children."""

from pathlib import Path

import uvicorn
from tests.e2e.conversation_signals_server import build_app

from planner.core.config import load_config
from planner.server_lifecycle.application import ChangeStreamClosingServer


def main() -> None:
    config = load_config("config.yaml")
    assert config.test_mode
    assert Path(config.db_path).name == "rail-proof.db"
    app = build_app(config)
    ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()


if __name__ == "__main__":
    main()
