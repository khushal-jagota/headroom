"""Darwin home lifecycle, local preview evidence, and native measurements."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from planner.platform_runtime.legacy_posix import LegacyPosixPlatformAdapter

if TYPE_CHECKING:
    from planner.environments.vps_metrics.config import MetricsConfig
    from planner.environments.vps_metrics.contracts import Collector
    from planner.platform_runtime.contracts import PreviewProcessInspector


class DarwinPlatformAdapter(LegacyPosixPlatformAdapter):
    update_unavailable_reason = None
    update_requires_snapshot = True

    def preview_inspector(self) -> PreviewProcessInspector:
        from .preview import DarwinProcessInspector

        return DarwinProcessInspector()

    def metrics_collector(self, config: MetricsConfig) -> Collector:
        from .metrics import DarwinCollector

        return DarwinCollector(config)

    def own_service_unit(self, cgroup: str | None = None) -> str | None:
        from .updates import own_service_unit

        return own_service_unit()

    def launch_installer(self, attempt_id: str, command: list[str]) -> None:
        from .updates import launch_installer

        launch_installer(attempt_id, command)

    def installer_source(self) -> Path:
        from planner.platform_runtime.linux.updates import installer_source

        return installer_source()

    def installer_python(self, current_root: Path) -> Path:
        from .updates import current_service

        service = current_service()
        if service.root != current_root.resolve():
            raise ValueError("Installer root differs from this Mac home")
        return service.python

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
