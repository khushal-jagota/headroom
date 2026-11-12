"""Darwin app custody; remaining native home capabilities follow separately."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

from planner.platform_runtime.legacy_posix import LegacyPosixPlatformAdapter


class DarwinPlatformAdapter(LegacyPosixPlatformAdapter):
    def control_socket_path(self, port: int, environ: Mapping[str, str], launch_root: Path) -> Path:
        from .paths import resolve_server_control_socket_path

        return resolve_server_control_socket_path(port, environ, launch_root)

    def event_loop_tasks_socket_path(self, path: Path) -> Path:
        from .paths import resolve_event_loop_tasks_socket_path

        return resolve_event_loop_tasks_socket_path(path)

    def lifecycle_lease_path(self, port: int) -> Path:
        from .paths import resolve_server_lifecycle_lease_path

        return resolve_server_lifecycle_lease_path(port)

    def spawn_application_child(
        self,
        interpreter: str,
        launch_root: Path,
        environment: dict[str, str],
        original_mask: list[int],
    ) -> subprocess.Popen[bytes]:
        # Keep the app in launchd's job process group. A separate session escapes
        # launchd's cleanup when the supervisor suffers an uncatchable crash.
        listener_fd = environment.get("PLAN_SERVER_LISTENER_FD")
        return subprocess.Popen(
            [
                interpreter,
                "-c",
                "import os,signal,sys; "
                f"signal.pthread_sigmask(signal.SIG_SETMASK, {original_mask!r}); "
                "os.execv(sys.executable, [sys.executable, '-m', "
                "'planner.server_lifecycle.application'])",
            ],
            cwd=launch_root,
            env=environment,
            start_new_session=False,
            pass_fds=(int(listener_fd),) if listener_fd is not None else (),
        )
