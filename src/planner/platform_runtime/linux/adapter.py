"""Linux factories retain domain contracts and their current behavior."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import Collector
from planner.platform_runtime.contracts import PreviewProcessInspector
from planner.server_lifecycle.contracts import ProcessIdentity, ShutdownPolicy


class LinuxPlatformAdapter:
    update_unavailable_reason: str | None = None

    def read_process_identity(self, pid: int) -> ProcessIdentity | None:
        from .lifecycle import read_process_identity

        return read_process_identity(pid)

    def read_authenticated_manager_identity(
        self, pid: int, bus: Literal["user", "system"] | None = None
    ) -> ProcessIdentity | None:
        from .lifecycle import read_authenticated_manager_identity

        return read_authenticated_manager_identity(pid, bus)

    def revalidate_manager_identity(self, manager: ProcessIdentity) -> ProcessIdentity | None:
        from .lifecycle import revalidate_manager_identity

        return revalidate_manager_identity(manager)

    def capture_shutdown_policy(self) -> ShutdownPolicy:
        from .lifecycle import capture_shutdown_policy

        return capture_shutdown_policy()

    def spawn_application_child(
        self,
        interpreter: str,
        launch_root: Path,
        environment: dict[str, str],
        original_mask: list[int],
    ) -> subprocess.Popen[bytes]:
        from .lifecycle import spawn_application_child

        return spawn_application_child(interpreter, launch_root, environment, original_mask)

    def process_is_zombie(self, pid: int) -> bool:
        from .lifecycle import process_is_zombie

        return process_is_zombie(pid)

    def preview_inspector(self) -> PreviewProcessInspector:
        from .preview import LinuxProcessInspector

        return LinuxProcessInspector()

    def control_socket_path(self, port: int, environ: Mapping[str, str], launch_root: Path) -> Path:
        from .paths import resolve_server_control_socket_path

        return resolve_server_control_socket_path(port, environ, launch_root)

    def event_loop_tasks_socket_path(self, path: Path) -> Path:
        from .paths import resolve_event_loop_tasks_socket_path

        return resolve_event_loop_tasks_socket_path(path)

    def lifecycle_lease_path(self, port: int) -> Path:
        from .paths import resolve_server_lifecycle_lease_path

        return resolve_server_lifecycle_lease_path(port)

    def lock_exclusive(self, descriptor: int, *, nonblocking: bool = False) -> None:
        from .paths import lock_exclusive

        lock_exclusive(descriptor, nonblocking=nonblocking)

    def unlock(self, descriptor: int) -> None:
        from .paths import unlock

        unlock(descriptor)

    def metrics_collector(self, config: MetricsConfig) -> Collector:
        from planner.environments.vps_metrics.collectors import LinuxCollector

        return LinuxCollector(config)

    def own_service_unit(self, cgroup: str | None = None) -> str | None:
        from .updates import own_service_unit

        return own_service_unit(cgroup)

    def launch_installer(self, attempt_id: str, command: list[str]) -> None:
        from .updates import launch_transient_unit

        launch_transient_unit(attempt_id, command)

    def installer_source(self) -> Path:
        from .updates import installer_source

        return installer_source()
