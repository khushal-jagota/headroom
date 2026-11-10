"""Replaceable application-process entry point owned by the Panels supervisor."""

from __future__ import annotations

import os
import socket
import sqlite3
from pathlib import Path
from types import FrameType

import uvicorn

from planner.core.clock import build_clock
from planner.core.config import DB_BUSY_TIMEOUT_MS, HOST, load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.core.sse import close_open_change_streams
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.previews import config as preview_limits
from planner.server_lifecycle.control import resolve_event_loop_tasks_socket_path
from planner.server_lifecycle.event_loop_tasks import (
    EventLoopTasksListing,
    open_event_loop_tasks_listing,
)
from planner.server_lifecycle.supervisor import resolve_planner_launch_root


class ChangeStreamClosingServer(uvicorn.Server):
    """Close the browser's change streams first, then shut down normally.

    Uvicorn waits for open connections to finish before it stops, and never cuts an
    HTTP response short. A change stream is idle nearly all the time and only ends when
    its browser leaves, so left alone it would hold the whole shutdown open.

    Given an event-loop-tasks socket, it also answers `panels server-event-loop-tasks`
    from its own event loop for as long as it serves.
    """

    def __init__(
        self,
        config: uvicorn.Config,
        *,
        event_loop_tasks_socket_path: Path | None = None,
    ) -> None:
        super().__init__(config)
        self._event_loop_tasks_socket_path = event_loop_tasks_socket_path
        self._event_loop_tasks_listing: EventLoopTasksListing | None = None

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        if self._event_loop_tasks_socket_path is not None:
            self._event_loop_tasks_listing = await open_event_loop_tasks_listing(
                self._event_loop_tasks_socket_path
            )
        try:
            await super().serve(sockets)
        finally:
            self._close_event_loop_tasks_listing()

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # Close here as well: after a shutdown signal, uvicorn raises that signal again
        # once serve() unwinds, and the process ends before serve()'s own cleanup runs.
        try:
            await super().shutdown(sockets)
        finally:
            self._close_event_loop_tasks_listing()

    def _close_event_loop_tasks_listing(self) -> None:
        if self._event_loop_tasks_listing is not None:
            self._event_loop_tasks_listing.close()
            self._event_loop_tasks_listing = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        close_open_change_streams()
        super().handle_exit(sig, frame)


def run_application_process() -> None:
    """Compose and run the application from the root that owns its package."""
    launch_root = resolve_planner_launch_root()
    os.chdir(launch_root)
    config = load_config(os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")))
    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    clock = build_clock(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path, DB_BUSY_TIMEOUT_MS)

    app = create_app(config, clock, conn_factory)
    listener_fd = os.environ.get("PLAN_SERVER_LISTENER_FD")
    if listener_fd is not None:
        server_config = uvicorn.Config(
            app,
            fd=int(listener_fd),
            ws="websockets",
            ws_per_message_deflate=False,
            ws_max_size=max(MAX_ENVELOPE_BYTES, preview_limits.WEBSOCKET_MESSAGE_BYTES),
            ws_max_queue=preview_limits.WEBSOCKET_QUEUE_DEPTH,
            ws_ping_interval=preview_limits.WEBSOCKET_PING_SECONDS,
            ws_ping_timeout=preview_limits.WEBSOCKET_PONG_SECONDS,
        )
    else:
        server_config = uvicorn.Config(
            app,
            host=HOST,
            port=config.port,
            ws="websockets",
            ws_per_message_deflate=False,
            ws_max_size=max(MAX_ENVELOPE_BYTES, preview_limits.WEBSOCKET_MESSAGE_BYTES),
            ws_max_queue=preview_limits.WEBSOCKET_QUEUE_DEPTH,
            ws_ping_interval=preview_limits.WEBSOCKET_PING_SECONDS,
            ws_ping_timeout=preview_limits.WEBSOCKET_PONG_SECONDS,
        )
    # The supervisor names its control socket for the child it starts; the event-loop-tasks
    # socket lives beside it. Without a supervisor there is no socket to stand beside.
    control_socket = os.environ.get("PLAN_SERVER_CONTROL_SOCKET")
    ChangeStreamClosingServer(
        server_config,
        event_loop_tasks_socket_path=(
            resolve_event_loop_tasks_socket_path(Path(control_socket)) if control_socket else None
        ),
    ).run()


if __name__ == "__main__":
    run_application_process()
