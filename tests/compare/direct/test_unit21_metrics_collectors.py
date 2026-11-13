"""Linux evidence parity over existing fake proc/sys fixtures and exact scripted children.

Collector evidence uses fake proc/sys trees and injected statistics. Commands and
clock proofs use only their recorded synthetic children.
"""

from __future__ import annotations

import builtins
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_vps_metrics_collectors import fixture_collector, process, write

from planner.environments.vps_metrics import collectors

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/vps_metrics/testing/collector_parity.ts"


def _environment(root: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(create_synthetic_homes(root))
    environment.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_APP_ROOT": str(root),
            "PLAN_DB_PATH": str(root / "unused.sqlite"),
            "PLAN_CONFIG_PATH": str(root / "absent-config.yaml"),
            "PLAN_WORKSPACE_ROOT": str(root / "workspace"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "key"),
        }
    )
    return environment


def _node(root: Path, function: str, value: dict[str, Any]) -> Any:
    specification = root / "request.json"
    specification.write_text(json.dumps(value))
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; const m=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            f"process.stdout.write(await m.{function}(input));",
            COMPANION.as_uri(),
            str(specification),
        ],
        cwd=ROOT,
        env=_environment(root),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _report(request: pytest.FixtureRequest, name: str, value: dict[str, Any]) -> None:
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _compare_sequence(
    tmp_path: Path,
    actions: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any]:
    python_root, node_root = tmp_path / "python", tmp_path / "node"
    collector, clock, _ = fixture_collector(python_root)
    original_query = collector.service_query
    initial_service = {
        scope: {"text": original_query(scope, 1.0, 1024 * 1024)} for scope in ("system", "user")
    }
    shutil.copytree(python_root, node_root)
    service = initial_service
    filesystem_error: str | None = None
    stat_calls = 0
    calls: list[str] = []
    samples = []
    reuse: dict[str, str] | None = None
    reads = 0
    original_read = collectors._read

    def safe_read(path: Path) -> str:
        nonlocal reads
        assert path.is_relative_to(python_root)
        assert path.name not in {"cmdline", "environ"}
        if reuse is not None and path == python_root / reuse["path"]:
            reads += 1
            if reads == 2:
                path.write_text(reuse["replacement"])
        return original_read(path)

    def system_constant(name: str) -> int:
        assert name in {"SC_CLK_TCK", "SC_PAGE_SIZE"}
        return 100 if name == "SC_CLK_TCK" else 4096

    def statistics(path: str) -> Any:
        nonlocal stat_calls
        stat_calls += 1
        assert path == "/"
        if filesystem_error is not None:
            raise getattr(builtins, filesystem_error)("scripted filesystem unavailable")
        return SimpleNamespace(f_blocks=1000, f_bavail=250, f_frsize=4096)

    def query(scope: str, timeout: float, limit: int) -> str:
        assert timeout == 1.0 and limit == 1024 * 1024
        calls.append(scope)
        reply = service[scope]
        if "error" in reply:
            raise getattr(builtins, reply["error"])("scripted service unavailable")
        return reply["text"]

    monkeypatch.setattr(collectors, "_read", safe_read)
    monkeypatch.setattr(os, "sysconf", system_constant)
    collector.statvfs, collector.service_query = statistics, query
    for action in actions:
        clock[0] = action["now"]
        service = action.get("service", service)
        filesystem_error = action.get("filesystem_error", filesystem_error)
        reuse, reads = action.get("reuse"), 0
        for name in action.get("mkdir", []):
            (python_root / name).mkdir(parents=True, exist_ok=True)
        for name, text in action.get("write", {}).items():
            write(python_root, name, text)
        for name in action.get("remove", []):
            (python_root / name).unlink()
        if group := action.get("clone_group"):
            path = python_root / group
            path.rename(path.with_name(path.name + ".old"))
            shutil.copytree(path.with_name(path.name + ".old"), path)
        samples.append(collector.collect(include_slow=action["include_slow"]))
    expected = {"samples": samples, "calls": calls, "stat_calls": stat_calls}
    actual = _node(
        tmp_path,
        "collector_parity",
        {
            "root": str(node_root),
            "service": initial_service,
            "actions": actions,
        },
    )
    # Re-encoding also proves integer/float JSON kinds, not only numeric equality.
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
    return actual, initial_service


def _process_stat(tmp_path: Path, *, cpu: int = 100, start: int = 50) -> str:
    process(tmp_path, 11, cpu=cpu, start=start)
    return (tmp_path / "11/stat").read_text()


def test_collector_full_samples_rates_identity_and_slow_cache_match_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    group = "user.slice/user@1000.service/app.slice/panels.service"
    stat_500 = _process_stat(tmp_path / "stat-fixture", cpu=500)
    stat_reused = _process_stat(tmp_path / "stat-fixture", cpu=1000, start=51)
    user_health = {
        "text": "Id=panels.service\nActiveState=active\nSubState=running\nNRestarts=5\n"
        f"ControlGroup=/{group}\n"
    }
    actions: list[dict[str, Any]] = [
        {
            "now": 100.0,
            "include_slow": True,
            "remove": ["proc/11/io"],
            "write": {
                "proc/pressure/cpu": (
                    "some avg10=1.5 avg60=2.0 avg300=3.0 total=100\n"
                    "full avg10=malformed avg60=4.0 missing-token\n"
                ),
            },
        },
        {
            "now": 102.0,
            "include_slow": False,
            "remove": ["proc/meminfo"],
            "write": {
                "proc/11/stat": stat_500,
                "proc/stat": "cpu 200 0 0 200 0 0 0 0\ncpu0 1\ncpu1 1\n",
                "proc/diskstats": (
                    "254 0 vda 1 0 30 0 1 0 60 0 0 0 0\n"
                    "254 1 vda1 1 0 30 0 1 0 60 0 0 0 0\n"
                    "253 0 dm-0 1 0 30 0 1 0 60 0 0 0 0\n"
                ),
            },
        },
        {"now": 104.0, "include_slow": False, "write": {"proc/11/stat": stat_reused}},
        {
            "now": 106.0,
            "include_slow": False,
            "write": {"proc/sys/kernel/random/boot_id": "boot-b"},
        },
        {
            "now": 108.0,
            "include_slow": False,
            "mkdir": ["sys/class/net/eth1/device", "sys/block/vdb/slaves"],
            "write": {
                "proc/net/dev": (
                    "header\nheader\neth0: 300 0 0 0 0 0 0 0 400 0 0 0 0 0 0 0\n"
                    "eth1: 500 0 0 0 0 0 0 0 600 0 0 0 0 0 0 0\n"
                ),
                "proc/diskstats": (
                    "254 0 vda 1 0 50 0 1 0 70 0 0 0 0\n254 1 vdb 1 0 60 0 1 0 80 0 0 0 0\n"
                ),
            },
        },
        {
            "now": 110.0,
            "include_slow": True,
            "service": {"system": {"error": "TimeoutError"}, "user": user_health},
            "filesystem_error": "PermissionError",
        },
        {
            "now": 112.0,
            "include_slow": True,
            "service": {
                "system": {"text": "Id=recovered.service\nActiveState=inactive\nSubState=dead\n"},
                "user": user_health,
            },
            "filesystem_error": None,
            "clone_group": "cgroup/" + group,
        },
    ]
    actual, _ = _compare_sequence(tmp_path, actions, monkeypatch)
    assert actual["calls"] == ["system", "user"] * 3
    _report(
        request,
        "unit-21-metrics-collector-sequence.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
            "boundary": (
                "Existing fake proc/sys/cgroup fixture and injected clocks/sysconf/statvfs/query. "
                "Full Samples and numeric kinds match. Native ABI branches are not executed here."
            ),
        },
    )


def test_collector_rejects_pid_reuse_during_the_same_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    replacement = _process_stat(tmp_path / "stat-fixture", start=99)
    actual, _ = _compare_sequence(
        tmp_path,
        [
            {
                "now": 100.0,
                "include_slow": False,
                "reuse": {"path": "proc/11/stat", "replacement": replacement},
            }
        ],
        monkeypatch,
    )
    sample = actual["samples"][0]
    assert sample["process_count"] == 2 and sample["processes"] == []
    assert sample["process_availability"] == "unavailable"
    _report(
        request,
        "unit-21-metrics-collector-pid-reuse.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
        },
    )


def test_service_command_limits_kill_and_reap_only_the_scripted_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    script = tmp_path / "service-command.cjs"
    script.write_text(
        "const mode=process.argv[2]; "
        "if(mode==='success')process.stdout.write(Buffer.concat(["
        "Buffer.from('Id=unit.service\\n'),Buffer.from([255])])); "
        "else if(mode==='large')process.stdout.write(Buffer.alloc(131072,65)); "
        "else if(mode==='timeout')setInterval(()=>{},1000); "
        "else if(mode==='retained'){require('node:child_process').spawn(process.execPath,"
        "['-e',\"setTimeout(()=>{require('node:fs').writeFileSync(process.argv[1],'done');"
        "process.exit(0)},700)\",process.argv[3]],{stdio:['ignore',1,'ignore']});"
        "process.exit(0);} else process.exit(7);"
    )
    cases: list[dict[str, Any]] = [
        {"mode": mode, "timeout": 0.08 if mode == "timeout" else 2.0, "limit": 64}
        for mode in ("success", "large", "timeout", "exit")
    ]
    cases.append(
        {
            "mode": "retained",
            "timeout": 0.25,
            "limit": 64,
            "completion": str(tmp_path / "node-retained.done"),
        }
    )
    expected = []
    original_popen = subprocess.Popen
    environment = _environment(tmp_path)
    python_retained_ms = None
    for case in cases:
        query_started = time.perf_counter()
        children: list[Any] = []
        argv: list[str] = []
        completion = tmp_path / "python-retained.done" if case["mode"] == "retained" else None

        def command(
            args: list[str],
            case: dict[str, Any] = case,
            argv: list[str] = argv,
            children: list[Any] = children,
            completion: Path | None = completion,
            **kwargs: Any,
        ) -> Any:
            argv.extend(args[1:])
            assert args[0] == "systemctl"
            child = original_popen(
                ["node", str(script), case["mode"], *([str(completion)] if completion else [])],
                **kwargs,
                env=environment,
            )
            children.append(child)
            return child

        with monkeypatch.context() as local:
            local.setattr(subprocess, "Popen", command)
            try:
                value = collectors._query_services("user", case["timeout"], case["limit"])
                expected.append(
                    {
                        "mode": case["mode"],
                        "value": value,
                        "error": None,
                        "closed": children[0].returncode is not None,
                        "argv": argv,
                    }
                )
            except Exception as error:
                expected.append(
                    {
                        "mode": case["mode"],
                        "value": None,
                        "error": {"name": type(error).__name__, "message": str(error)},
                        "closed": children[0].returncode is not None,
                        "argv": argv,
                    }
                )
        if completion is not None:
            python_retained_ms = (time.perf_counter() - query_started) * 1000
            expected[-1]["returned_before_descendant_expiry"] = not completion.exists()
            deadline = time.monotonic() + 2
            while not completion.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert completion.exists()
            time.sleep(0.025)
    actual = _node(tmp_path, "collector_commands", {"script": str(script), "cases": cases})
    assert actual["results"] == expected
    assert all(result["closed"] for result in actual["results"])
    assert actual["results"][-1]["returned_before_descendant_expiry"]
    clock_started = time.perf_counter()
    clock_script = tmp_path / "clock-origin.cjs"
    clock_script.write_text(
        "(async()=>{const m=await import(process.argv[2]);"
        "if(process.argv[3]==='once'){process.stdout.write(String(m.monotonic_seconds())+'\\n');return;}"
        "const r=require('node:readline').createInterface({input:process.stdin});"
        "r.on('line',line=>{if(line==='now')process.stdout.write(String(m.monotonic_seconds())+'\\n');"
        "else if(line==='stop'){r.close();process.exit(0);}});"
        "process.stdout.write('ready\\n');})();"
    )
    factory = (ROOT / "server/environments/vps_metrics/collectors/index.ts").as_uri()
    old = subprocess.Popen(
        ["node", str(clock_script), factory, "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
        env=environment,
    )

    def clock_line() -> str:
        assert old.stdout is not None
        deadline = time.monotonic() + 30
        line = bytearray()
        while not line.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            assert (
                remaining > 0
                and select.select([old.stdout], [], [], remaining)[0]
                and time.monotonic() < deadline
            ), "clock child did not answer"
            value = os.read(old.stdout.fileno(), 1)
            assert value, "clock child exited without its answer"
            line.extend(value)
        return line.decode("utf-8").strip()

    try:
        assert clock_line() == "ready"
        assert old.stdin is not None
        python_before = time.monotonic()
        old.stdin.write(b"now\n")
        old.stdin.flush()
        old_before = float(clock_line())
        new = subprocess.run(
            ["node", str(clock_script), factory, "once"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
            check=True,
        )
        new_value = float(new.stdout)
        old.stdin.write(b"now\n")
        old.stdin.flush()
        old_after = float(clock_line())
        python_after = time.monotonic()
        assert python_before <= old_before <= new_value <= old_after <= python_after
        old.stdin.write(b"stop\n")
        old.stdin.flush()
        old.communicate(timeout=5)
        assert old.returncode == 0
    finally:
        if old.poll() is None:
            old.kill()
            old.wait(timeout=5)
    clock_origin = {
        "old_before": old_before,
        "new": new_value,
        "old_after": old_after,
        "python_before": python_before,
        "python_after": python_after,
        "seconds": time.perf_counter() - clock_started,
    }
    _report(
        request,
        "unit-21-metrics-collector-commands.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
            "python_retained_query_ms": python_retained_ms,
            "clock_origin": clock_origin,
            "boundary": (
                "Actual bounded pipe/deadline/kill/reap helpers on one synthetic Node program. "
                "No systemctl executes. No unrelated PID is signaled."
            ),
        },
    )
