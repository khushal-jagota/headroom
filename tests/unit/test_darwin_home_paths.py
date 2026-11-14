"""Mac addresses must bind even when the source and data paths are long."""

import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from planner.platform_runtime.darwin import paths
from planner.platform_runtime.darwin.adapter import DarwinPlatformAdapter
from planner.server_lifecycle.contracts import ServerLifecycleError


def test_socket_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Model the fixed short native runtime root, not pytest's nested worker path.
    with TemporaryDirectory(prefix="pm-", dir="/var/tmp") as short_root:
        tmp_path = Path(short_root)
        monkeypatch.setattr(paths, "_RUNTIME_PARENT", tmp_path)
        adapter = DarwinPlatformAdapter()
        launch = tmp_path / ("source with spaces " * 8)
        configured = launch / "data" / "control.sock"
        environment = {"PLAN_SERVER_CONTROL_SOCKET": str(configured)}
        control = adapter.control_socket_path(8767, environment, launch)
        tasks = adapter.event_loop_tasks_socket_path(control)
        # The app receives the supervisor's selected address; CLI clients resolve the
        # original configuration. Both must reach the same two physical sockets.
        assert adapter.control_socket_path(8767, environment, launch) == control
        assert (
            adapter.control_socket_path(8767, {"PLAN_SERVER_CONTROL_SOCKET": str(control)}, launch)
            == control
        )
        assert adapter.event_loop_tasks_socket_path(control) == tasks
        other = adapter.control_socket_path(
            8767, {"PLAN_SERVER_CONTROL_SOCKET": str(configured) + "x"}, launch
        )
        assert other not in (control, tasks)
        # A valid control address can still overflow when the task suffix is added.
        explicit = tmp_path / ("c" * (100 - len(os.fsencode(tmp_path)) - 1))
        preserved = adapter.control_socket_path(
            8767, {"PLAN_SERVER_CONTROL_SOCKET": str(explicit)}, launch
        )
        assert preserved == explicit
        derived = adapter.event_loop_tasks_socket_path(preserved)
        assert derived != preserved
        for address in (control, tasks, other, preserved, derived):
            assert len(os.fsencode(address)) < 104
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(address))
        assert control.parent.stat().st_mode & 0o777 == 0o700


def test_private_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_RUNTIME_PARENT", tmp_path)
    target = tmp_path / "other-folder"
    target.mkdir(mode=0o755)
    runtime = tmp_path / f"panels-{os.getuid()}"
    runtime.symlink_to(target, target_is_directory=True)
    with pytest.raises(ServerLifecycleError, match="private and owned"):
        DarwinPlatformAdapter().lifecycle_lease_path(8767)
    assert target.stat().st_mode & 0o777 == 0o755
    runtime.unlink()
    runtime.mkdir(mode=0o755)
    with pytest.raises(ServerLifecycleError, match="private and owned"):
        DarwinPlatformAdapter().control_socket_path(8767, {}, tmp_path)
    assert runtime.stat().st_mode & 0o777 == 0o755
