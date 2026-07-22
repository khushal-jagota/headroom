from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from conftest import BOOT_BUDGET_S, PLAN_BIN, REPO_ROOT


@dataclass(frozen=True)
class RuntimeInstance:
    kind: str
    instance_id: str | None
    manifest: dict[str, Any]
    repository_root: Path
    log_path: Path

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.manifest['port']}"


@dataclass
class RuntimeProcess:
    instance: RuntimeInstance
    proc: subprocess.Popen[bytes]


def test_environment_run_isolates_concurrent_test_mode_instances(tmp_path: Path) -> None:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    environment_root = Path("/tmp") / f"pe-e2e-{os.getpid()}-{digest}"
    shutil.rmtree(environment_root, ignore_errors=True)
    repository_roots = {
        "staging": _repository_root(tmp_path, "staging-repo"),
        "alpha": _repository_root(tmp_path, "alpha-repo"),
        "bravo": _repository_root(tmp_path, "bravo-repo"),
    }
    instances: list[RuntimeInstance] = []
    processes: list[RuntimeProcess] = []

    try:
        instances = [
            _prepare_instance(
                tmp_path,
                kind="staging",
                instance_id=None,
                environment_root=environment_root,
                repository_root=repository_roots["staging"],
                port=_test_port(tmp_path, 0),
            ),
            _prepare_instance(
                tmp_path,
                kind="preview",
                instance_id="alpha",
                environment_root=environment_root,
                repository_root=repository_roots["alpha"],
                port=_test_port(tmp_path, 1),
            ),
            _prepare_instance(
                tmp_path,
                kind="preview",
                instance_id="bravo",
                environment_root=environment_root,
                repository_root=repository_roots["bravo"],
                port=_test_port(tmp_path, 2),
            ),
        ]
        processes = [
            _start_instance(instance, environment_root)
            for instance in instances
        ]
        for runtime in processes:
            _wait_for_ready(runtime)

        metas = [
            httpx.get(f"{runtime.instance.base}/api/meta", timeout=1.0).json()
            for runtime in processes
        ]
        assert all(meta["test_mode"] is True for meta in metas)
        assert all(meta["ui_debounce_ms"] == 50 for meta in metas)
        assert all(meta["ws_heartbeat_ms"] == 500 for meta in metas)

        _assert_manifest_paths_are_distinct(instances)
        assert {
            instance.manifest["repository_roots"][0] for instance in instances
        } == {str(root.resolve()) for root in repository_roots.values()}
        for runtime in processes:
            manifest = runtime.instance.manifest
            assert Path(manifest["db_path"]).is_file()
            assert Path(manifest["managed_files_root"]).is_dir()
            assert Path(manifest["logs_dir"]).is_dir()
            _assert_skill_only_hermes_home(Path(manifest["hermes_home"]))
            assert Path(manifest["dispatcher_lock_path"]).parent.is_dir()
            assert Path(manifest["server_control_socket_path"]).exists()
            assert _port_listens(int(manifest["port"]))

        created = []
        for runtime in processes:
            response = httpx.post(
                f"{runtime.instance.base}/api/projects",
                json={
                    "name": (
                        f"Only {runtime.instance.kind} "
                        f"{runtime.instance.instance_id or 'stable'}"
                    )
                },
                timeout=5.0,
            )
            assert response.status_code < 300, response.text
            created.append(response.json()["id"])
            marker = (
                Path(runtime.instance.manifest["managed_files_root"])
                / "runtime-isolation"
                / "marker.txt"
            )
            marker.parent.mkdir(parents=True)
            marker.write_text(runtime.instance.instance_id or "staging", encoding="utf-8")

        for index, runtime in enumerate(processes):
            projects = httpx.get(f"{runtime.instance.base}/api/projects", timeout=5.0).json()[
                "projects"
            ]
            project_ids = {project["id"] for project in projects}
            assert created[index] in project_ids
            assert all(other not in project_ids for other in created[:index] + created[index + 1 :])
            marker = (
                Path(runtime.instance.manifest["managed_files_root"])
                / "runtime-isolation"
                / "marker.txt"
            )
            assert marker.read_text(encoding="utf-8") == (
                runtime.instance.instance_id or "staging"
            )

    finally:
        for runtime in processes:
            _terminate_process_group(runtime.proc)
        for runtime in processes:
            assert _nothing_listens(int(runtime.instance.manifest["port"]))
            assert not Path(runtime.instance.manifest["server_control_socket_path"]).exists()
        for instance in instances:
            if instance.kind != "live":
                _remove_instance(instance, environment_root)
        assert not (environment_root / "staging").exists()
        assert not (environment_root / "previews" / "alpha").exists()
        assert not (environment_root / "previews" / "bravo").exists()
        shutil.rmtree(environment_root, ignore_errors=True)


def _prepare_instance(
    tmp_path: Path,
    *,
    kind: str,
    instance_id: str | None,
    environment_root: Path,
    repository_root: Path,
    port: int,
) -> RuntimeInstance:
    args = [
        str(PLAN_BIN),
        "environment",
        "prepare",
        "--kind",
        kind,
        "--environment-root",
        str(environment_root),
        "--port",
        str(port),
        "--repository-root",
        str(repository_root),
        "--json",
    ]
    if instance_id is not None:
        args.extend(["--instance-id", instance_id])
    result = subprocess.run(
        args,
        cwd=str(repository_root),
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    manifest = json.loads(result.stdout)
    return RuntimeInstance(
        kind=kind,
        instance_id=instance_id,
        manifest=manifest,
        repository_root=repository_root.resolve(),
        log_path=tmp_path / f"{kind}-{instance_id or 'stable'}.log",
    )


def _start_instance(
    instance: RuntimeInstance,
    environment_root: Path,
) -> RuntimeProcess:
    args = [
        str(PLAN_BIN),
        "environment",
        "run",
        "--kind",
        instance.kind,
        "--environment-root",
        str(environment_root),
        "--repository-root",
        str(instance.repository_root),
        "--test-mode",
    ]
    if instance.instance_id is not None:
        args.extend(["--instance-id", instance.instance_id])
    launch_env = _scrubbed_env()
    launch_env.update(
        {
            "HOME": str(instance.repository_root / ".ambient-home-must-not-be-used"),
            "PLAN_TEST_MODE": "0",
            "PLAN_FAKE_NOW": "2099-01-01T00:00:00",
            "PLAN_GATEWAY_ADAPTER": "real",
            "PLAN_PORT": "1",
            "PLAN_DB_PATH": str(
                instance.repository_root / "data" / "must-not-be-used.db"
            ),
            "PLAN_HERMES_HOME": str(
                instance.repository_root / "data" / "must-not-be-used-hermes"
            ),
        }
    )
    log = instance.log_path.open("wb")
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(instance.repository_root.parent),
            env=launch_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    return RuntimeProcess(instance=instance, proc=proc)


def _wait_for_ready(runtime: RuntimeProcess) -> None:
    deadline = time.monotonic() + BOOT_BUDGET_S
    while time.monotonic() < deadline:
        if runtime.proc.poll() is not None:
            raise AssertionError(
                f"{runtime.instance.kind} exited during boot rc={runtime.proc.returncode}\n"
                f"{_log_tail(runtime.instance.log_path)}"
            )
        try:
            response = httpx.get(f"{runtime.instance.base}/api/meta", timeout=0.5)
        except httpx.HTTPError:
            response = None
        if (
            response is not None
            and response.status_code == 200
            and Path(runtime.instance.manifest["server_control_socket_path"]).exists()
        ):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"{runtime.instance.kind} did not become ready\n{_log_tail(runtime.instance.log_path)}"
    )


def _assert_manifest_paths_are_distinct(instances: list[RuntimeInstance]) -> None:
    for key in (
        "db_path",
        "managed_files_root",
        "logs_dir",
        "dispatcher_lock_path",
        "server_control_socket_path",
        "hermes_home",
        "port",
        "repository_roots",
    ):
        values = [json.dumps(instance.manifest[key]) for instance in instances]
        assert len(set(values)) == len(values), key


def _assert_skill_only_hermes_home(hermes_home: Path) -> None:
    assert {path.name for path in hermes_home.iterdir()} == {"skills"}
    assert not (hermes_home / "auth.json").exists()
    assert not (hermes_home / "config.json").exists()
    assert not (hermes_home / "sessions").exists()
    for skill_path in (hermes_home / "skills").iterdir():
        if skill_path.is_symlink():
            continue
        assert skill_path.is_dir()
        assert (skill_path / "SKILL.md").is_file()


def _remove_instance(instance: RuntimeInstance, environment_root: Path) -> None:
    args = [
        str(PLAN_BIN),
        "environment",
        "remove",
        "--kind",
        instance.kind,
        "--environment-root",
        str(environment_root),
    ]
    if instance.instance_id is not None:
        args.extend(["--instance-id", instance.instance_id])
    result = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10.0)


def _scrubbed_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}


def _test_port(tmp_path: Path, offset: int) -> int:
    return 20_000 + (
        (os.getpid() * 3 + sum(tmp_path.name.encode("utf-8"))) % 19_997
    ) + offset


def _repository_root(tmp_path: Path, name: str) -> Path:
    repository_root = tmp_path / name
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _port_listens(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _nothing_listens(port: int) -> bool:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _port_listens(port):
            return True
        time.sleep(0.05)
    return False


def _log_tail(log_path: Path, n: int = 80) -> str:
    try:
        return "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"
