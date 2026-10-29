"""Serve the actual app with isolated state, synthetic homes, and real usage inputs."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.support.synthetic_homes import create_synthetic_homes  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--usage-state", type=Path, required=True)
    args = parser.parse_args()
    storage = args.storage.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    originalHome = Path.home()
    homes = create_synthetic_homes(storage)
    for key in list(os.environ):
        if key.startswith(("PLAN_", "PANELS_REAL_")):
            os.environ.pop(key)
    os.environ.update(homes)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    os.environ.update(
        PLAN_TEST_MODE="1",
        PLAN_DISPATCH_ENABLED="false",
        PLAN_PORT=str(port),
        PLAN_DB_PATH=str(storage / "planning.db"),
        PLAN_LOGS_DIR=str(storage / "logs"),
        PLAN_DISPATCHER_LOCK_PATH=str(storage / "dispatcher.lock"),
        PLAN_SERVER_CONTROL_SOCKET=str(storage / "control.sock"),
        PLAN_BACKUP_DIR=str(storage / "backups"),
    )
    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.usage.localUsageLimits import LocalUsageLimits
    from planner.conversation.usage.UsageService import UsageService
    from planner.core.clock import RealClock
    from planner.core.config import load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    config = load_config()
    connection = connect(config.db_path)
    try:
        create_schema(connection)
    finally:
        connection.close()
    def denyProvider(**kwargs: object) -> object:
        (storage / "provider-attempt.txt").write_text("A provider start was refused.\n")
        raise RuntimeError("Provider execution is forbidden in this proof")

    app = create_app(
        config,
        RealClock(),
        lambda: connect(config.db_path),
        backend_child_factories_for_test={key: denyProvider for key in ConversationBackendKey},
    )
    usageState = args.usage_state.resolve()
    usageState.mkdir(parents=True, exist_ok=True)
    app.state.recorded_usage = UsageService(
        usageState,
        [
            ("codex", originalHome / ".codex" / "sessions"),
            ("claude", originalHome / ".claude" / "projects"),
        ],
        localUsageLimits=LocalUsageLimits(usageState),
    )
    (storage / "server-identity.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "port": port,
                "db": config.db_path,
                "usageState": str(usageState),
                "homes": homes,
                "testMode": config.test_mode,
                "providerExecution": "denied",
                "source": str(Path(__file__).resolve().parents[1]),
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"pid": os.getpid(), "port": port}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
