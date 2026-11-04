"""Disposable observations of real application startup, without PYTHONPATH hooks."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml


def assert_lifecycle_startup(root: Path, scratch: Path, *, packaged: bool) -> None:
    """Reuse one interpreter for default, relative-socket, and explicit-config proofs."""
    scratch.mkdir()
    python = root / ".venv/bin/python"
    site = Path(
        subprocess.check_output(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
    )
    descendant = scratch / "descendant"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(descendant)], check=True)
    unrelated = scratch / "unrelated"
    unrelated.mkdir()
    (scratch / "home").mkdir()
    (unrelated / "config.yaml").write_text("tick_seconds: 123\n")
    hook = scratch / "observe_application.py"
    observation = scratch / "observation.json"
    hook.write_text(_hook_source(observation, descendant / "bin/python"))
    pth = site / "panels_lifecycle_observation.pth"
    assert not pth.exists()
    # Execute a file directly. This adds no path to sys.path and removes no caller path.
    pth.write_text(f"import runpy; runpy.run_path({str(hook)!r})\n")
    try:
        for case in ("default", "relative", "explicit"):
            observation.unlink(missing_ok=True)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = int(listener.getsockname()[1])
                env = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith(("PLAN_", "PANELS_")) and key != "PYTHONPATH"
                }
                env.update(
                    {
                        "HOME": str(scratch / "home"),
                        "PLAN_DB_PATH": str(scratch / f"{case}.db"),
                        "PLAN_LOGS_DIR": str(scratch / "logs"),
                        "PLAN_BACKUP_DIR": str(scratch / "backups"),
                        "PLAN_HERMES_HOME": str(scratch / "home/.hermes"),
                        "PLAN_DISPATCHER_LOCK_PATH": str(scratch / "dispatcher.lock"),
                        "PLAN_PORT": str(port),
                        "PLAN_DISPATCH_ENABLED": "0",
                    }
                )
                expected_config = root / "config.yaml"
                expected_tick = yaml.safe_load((root / "config.yaml").read_text())["tick_seconds"]
                socket_path = Path(f"/tmp/panels-{os.getuid()}/server-{port}.sock")
                if case == "relative":
                    # The relative path is short enough for AF_UNIX even in pytest roots.
                    relative = f".lifecycle-{port}.sock"
                    env["PLAN_SERVER_CONTROL_SOCKET"] = relative
                    socket_path = root / relative
                elif case == "explicit":
                    expected_config = scratch / "explicit.yaml"
                    expected_tick += 17
                    expected_config.write_text(f"tick_seconds: {expected_tick}\n")
                    env["PLAN_CONFIG_PATH"] = str(expected_config)
                    socket_path = Path(f"/tmp/panels-lifecycle-{port}.sock")
                    env["PLAN_SERVER_CONTROL_SOCKET"] = str(socket_path)
                supplied_listener = not packaged and case == "explicit"
                if supplied_listener:
                    env["PLAN_SERVER_LISTENER_FD"] = str(listener.fileno())
                if packaged:
                    # The unchanged canonical smoke already proved the launcher.
                    # The disposable .pth changes the artifact digest, so these
                    # additional observations use its installed interpreter.
                    env["PLAN_APP_ROOT"] = str(root)
                command = [str(python), "-m", "planner", "serve"]
                started = time.monotonic()
                with (scratch / f"{case}.log").open("wb") as log:
                    proc = subprocess.Popen(
                        command,
                        cwd=unrelated,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        pass_fds=(listener.fileno(),) if supplied_listener else (),
                    )
                if not supplied_listener:
                    listener.close()
                try:
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        assert proc.poll() is None, (scratch / f"{case}.log").read_text()
                        try:
                            response = httpx.get(f"http://127.0.0.1:{port}/api/meta", timeout=0.3)
                            if response.status_code == 200 and observation.exists():
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.05)
                    else:
                        raise AssertionError((scratch / f"{case}.log").read_text())
                    data: dict[str, Any] = json.loads(observation.read_text())
                    assert data["cwd"] == str(root.resolve())
                    if sys.platform == "darwin":
                        cwd_output = subprocess.check_output(
                            ["/usr/sbin/lsof", "-a", "-p", str(proc.pid), "-d", "cwd", "-Fn"],
                            text=True,
                        )
                        cwd_paths = [line[1:] for line in cwd_output.splitlines()
                                     if line.startswith("n")]
                        assert cwd_paths == [str(unrelated)]
                    else:
                        assert Path(f"/proc/{proc.pid}/cwd").resolve() == unrelated
                    assert data["parent_pid"] == proc.pid
                    assert data["config_path"] == str(expected_config)
                    assert data["tick_seconds"] == expected_tick
                    assert data["db_path"] == env["PLAN_DB_PATH"]
                    assert Path(data["db_path"]).is_file()
                    assert Path(data["control_socket"]).resolve() == socket_path.resolve()
                    assert socket_path.is_socket()
                    assert data["pythonpath"] is None
                    assert data["descendant"] == {"pythonpath": None, "planner": None}
                    module_base = root / (".venv" if packaged else "src")
                    assert Path(data["module"]).is_relative_to(module_base)
                    assert Path(data["application_module"]).is_relative_to(module_base)
                    assert data["listener_valid"] is supplied_listener
                    print(
                        f"{'packaged' if packaged else 'editable'} startup {case}="
                        f"{time.monotonic() - started:.2f}s app_pid={data['pid']}"
                    )
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    assert not socket_path.exists()
    finally:
        pth.unlink()


def _hook_source(observation: Path, descendant: Path) -> str:
    # The hook activates only in the final application interpreter, never its
    # supervisor, the mask-reset trampoline, or the independent descendant.
    return f"""
import sys
if sys.orig_argv[-2:] == ["-m", "planner.server_lifecycle.application"]:
    import json, os, socket, subprocess
    import planner.core.config as config
    import planner.server_lifecycle.supervisor as supervisor
    original = config.load_config
    def observed(path=None, environ=None):
        result = original(path, environ)
        fd = os.environ.get("PLAN_SERVER_LISTENER_FD")
        valid = False
        if fd is not None:
            with socket.fromfd(int(fd), socket.AF_INET, socket.SOCK_STREAM) as inherited:
                if sys.platform == "darwin":
                    with socket.create_connection(inherited.getsockname(), timeout=1):
                        accepted, _ = inherited.accept()
                        accepted.close()
                        valid = True
                else:
                    valid = inherited.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        descendant = subprocess.check_output([
            {str(descendant)!r}, "-c",
            "import os,json,importlib.util; "
            "print(json.dumps(dict(pythonpath=os.environ.get('PYTHONPATH'), "
            "planner=None if importlib.util.find_spec('planner') is None else 'found')))"
        ], text=True)
        from pathlib import Path
        Path({str(observation)!r}).write_text(json.dumps(dict(
            pid=os.getpid(), parent_pid=os.getppid(), cwd=os.getcwd(),
            module=supervisor.__file__, application_module=sys.modules["__main__"].__file__,
            config_path=path,
            tick_seconds=result.tick_seconds, db_path=result.db_path,
            pythonpath=os.environ.get("PYTHONPATH"),
            control_socket=os.environ.get("PLAN_SERVER_CONTROL_SOCKET"),
            listener_valid=valid, descendant=json.loads(descendant),
        )))
        return result
    config.load_config = observed
"""
