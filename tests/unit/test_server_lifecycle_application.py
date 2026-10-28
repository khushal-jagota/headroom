"""Bound inbound WebSocket allocation before ASGI receives an application message."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from planner.core.config import Config
from planner.server_lifecycle import application


@pytest.mark.parametrize("listener_fd", [None, "42"])
def test_both_server_launch_paths_bound_websocket_ingress(
    tmp_path: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    listener_fd: str | None,
) -> None:
    isolated = replace(cfg, db_path=str(tmp_path / "test.db"), logs_dir=str(tmp_path / "logs"))
    # Restore process cwd after the entry point resolves its own source root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(application, "load_config", lambda *_args: isolated)
    monkeypatch.setattr(application, "create_app", lambda *_args: FastAPI())
    captured: list[uvicorn.Config] = []
    monkeypatch.setattr(
        application.ChangeStreamClosingServer, "run", lambda server: captured.append(server.config)
    )
    if listener_fd is None:
        monkeypatch.delenv("PLAN_SERVER_LISTENER_FD", raising=False)
    else:
        monkeypatch.setenv("PLAN_SERVER_LISTENER_FD", listener_fd)
    application.run_application_process()
    assert len(captured) == 1
    assert captured[0].ws == "websockets"
    assert captured[0].ws_max_size == 1024 * 1024
    assert captured[0].ws_max_queue == 4
    assert captured[0].ws_ping_interval == 20
    assert captured[0].ws_ping_timeout == 10
