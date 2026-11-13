"""One owned-process sequence proves control admission, replacement and lease lifetime.

No HTTP/browser host or service manager. Native pidfds signal only this fixture's
processes. The hosted unit/release proof owns real ExecStop and installer behavior.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit21_metrics import _owned_pidfd
from tests.support.synthetic_homes import create_synthetic_homes

from planner.server_lifecycle.contracts import (
    ServerRestartConnectionError,
    ServerRestartProtocolError,
)
from planner.server_lifecycle.control import request_server_restart

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "server/lifecycle"

pytestmark = pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux",
    reason="Actual supervisor/kernel sequence is hosted Linux only",
)


@pytest.fixture
def _owned_orphans() -> Iterator[None]:
    if os.uname().sysname != "Linux":
        yield
        return
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    try:
        yield
    finally:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


def _wait(predicate: Any, label: str) -> Any:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(label)


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _control(path: Path, value: dict[str, Any] | bytes) -> dict[str, Any] | None:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(path))
        try:
            client.sendall(
                value if isinstance(value, bytes) else json.dumps(value).encode() + b"\n"
            )
        except (BrokenPipeError, ConnectionResetError):
            return None
        chunks = bytearray()
        try:
            while not chunks.endswith(b"\n"):
                part = client.recv(4096)
                if not part:
                    return None
                chunks.extend(part)
        except ConnectionResetError:
            return None
        return cast(dict[str, Any], json.loads(chunks))


def _call(
    root: Path, env: dict[str, str], node: str, function: str, arguments: dict[str, Any]
) -> dict[str, Any] | str | None:
    spec = root / "call.json"
    spec.write_text(json.dumps(dict(function=function, arguments=arguments)))
    script = root / "call.mjs"
    script.write_text(
        "import{readFileSync}from'node:fs';const base=process.argv[2];"
        "const{MANAGED_STOP_CLIENT_SECONDS}=await import(new URL('configuration.ts',base));"
        "const deadline=performance.now()/1000+MANAGED_STOP_CLIENT_SECONDS;"
        "const s=JSON.parse(readFileSync(process.argv[3],'utf8'));let result=null;"
        "try{if(s.function==='capture')result=await(await import("
        "new URL('../environments/backup/capture.ts',base)))"
        ".create_database_backup(s.arguments.source,s.arguments.destination,'fixture');"
        "else if(s.function==='restart')result=await(await import(new URL('control.ts',base)))"
        ".request_control(s.arguments.control_socket_path,{version:1,operation:'restart'});"
        "else await(await import(new URL('managed_stop.ts',base))).stop_managed_supervisor("
        "s.arguments,{deadline});}"
        "catch(error){if(!(error instanceof Error))throw error;"
        "result={error_type:error.name,message:error.message};}"
        "console.log(JSON.stringify(result));"
    )
    completed = subprocess.run(
        [node, str(script), MODULE.as_uri() + "/", str(spec)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=85,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return cast(dict[str, Any] | str | None, json.loads(completed.stdout))


def _lease_is_held(path: Path) -> bool:
    with path.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(file, fcntl.LOCK_UN)
        return False


@contextmanager
def _supervisor(
    root: Path,
    env: dict[str, str],
    node: str,
    *,
    managed: bool,
    lease: Path | None = None,
    app_exit_code: int = 0,
) -> Iterator[tuple[subprocess.Popen[bytes], Any]]:
    for name in ("app", "data", "backups", "run", "runtime"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "runtime").chmod(0o700)
    app = root / "app/application.mjs"
    app.write_text(
        "import fs from'node:fs';import{spawn}from'node:child_process';"
        "import{createHash}from'node:crypto';"
        f"const root={json.dumps(str(root))};"
        "const write=e=>fs.appendFileSync(root+'/events.jsonl',JSON.stringify(e)+'\\n');"
        "const child=spawn(process.execPath,['-e',"
        "`const{createHash}=require('node:crypto');process.stdout.write(JSON.stringify("
        "Object.values(process.env).map(v=>createHash('sha256').update(v).digest('hex'))));`],"
        "{stdio:['ignore','pipe','inherit']});let data='';"
        "child.stdout.on('data',chunk=>data+=chunk);"
        "child.once('close',()=>write({kind:'ready',pid:process.pid,hashes:JSON.parse(data),"
        "app_hashes:Object.values(process.env).map(v=>createHash('sha256').update(v).digest('hex'))}));"
        "let stopping=false;process.on('SIGTERM',()=>{stopping=true;"
        "write({kind:'term',pid:process.pid});});"
        "setInterval(()=>{if(fs.existsSync(root+'/block')){"
        "write({kind:'blocked',pid:process.pid});while(true){}}"
        "if(process.ppid!==Number(process.env.FIXTURE_EXPECTED_SUPERVISOR)||"
        f"stopping&&!fs.existsSync(root+'/hold'))process.exit({app_exit_code});}},10);"
    )
    script = root / "supervisor.mjs"
    script.write_text(
        "import{readFileSync}from'node:fs';"
        "const{run_server_supervisor}=await import(process.argv[2]);"
        "const s=JSON.parse(readFileSync(process.argv[3],'utf8'));"
        "s.application.environment.FIXTURE_EXPECTED_SUPERVISOR=String(process.pid);"
        "try{process.exitCode=await run_server_supervisor(s);}"
        "catch(e){console.error(e.message);process.exitCode=1;}"
    )
    options = dict(
        mode=dict(
            kind="managed-linux",
            runtime_directory=str(root / "runtime"),
            application_root=str(root / "app"),
            database_path=str(root / "data/app.sqlite"),
            backup_root=str(root / "backups"),
        )
        if managed
        else dict(kind="foreground"),
        application=dict(
            node_executable=node,
            entrypoint=str(app),
            launch_root=str(root / "app"),
            environment={**env, "RUNTIME_DIRECTORY": str(root / "runtime")},
        ),
        control_socket_path=str(root / "control.sock"),
        lease_path=str(lease or root / "run/server.lock"),
        port=0,
    )
    spec = root / "supervisor.json"
    spec.write_text(json.dumps(options))
    owned: dict[int, int] = {}
    with (root / "output.log").open("wb") as output:
        process = subprocess.Popen(
            [node, str(script), (MODULE / "supervisor.ts").as_uri(), str(spec)],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
        )

        def ready(count: int) -> dict[str, Any]:
            def found() -> dict[str, Any] | None:
                rows = [row for row in _events(root) if row["kind"] == "ready"]
                assert process.poll() is None, (root / "output.log").read_text()
                return rows[count - 1] if len(rows) >= count else None

            row = cast(dict[str, Any], _wait(found, "owned application did not become ready"))
            if row["pid"] not in owned:
                descriptor = _owned_pidfd(row["pid"], process.pid)
                if descriptor is not None:
                    owned[row["pid"]] = descriptor
            return row

        try:
            yield process, ready
        finally:
            # Popen owns this unreaped direct child; no numeric-PID lookup/signalling.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for pid, descriptor in owned.items():
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    assert select.select([descriptor], [], [], 10)[0], (
                        "owned application did not exit"
                    )
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass  # The supervisor already reaped its own ordinary child.
                    os.close(descriptor)


def test_unit21_owned_supervisor_control_and_stop_protocol(
    tmp_path: Path, request: pytest.FixtureRequest, _owned_orphans: None
) -> None:
    started = time.perf_counter()
    node = shutil.which("node")
    assert node is not None
    env = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        **create_synthetic_homes(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_DISPATCH_ENABLED": "false",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    with tempfile.TemporaryDirectory(
        prefix="p21s-", dir=Path(tempfile.gettempdir()).resolve()
    ) as temporary:
        base = Path(temporary)
        env.update(TMPDIR=str(base), TMP=str(base), TEMP=str(base))
        for key, name in {
            "PLAN_DB_PATH": "data/private.sqlite",
            "PLAN_CONFIG_PATH": "config.yaml",
            "PLAN_LOGS_DIR": "logs",
            "PLAN_BACKUP_DIR": "backups",
            "PLAN_DISPATCHER_LOCK_PATH": "dispatcher.lock",
            "PLAN_SERVER_LIFECYCLE_LEASE_PATH": "lifecycle.lock",
            "PLAN_SERVER_CONTROL_SOCKET": "control.sock",
            "PLAN_CREDENTIAL_KEY_PATH": "key",
            "PLAN_APP_ROOT": "app",
            "PLAN_WORKSPACE_ROOT": "workspace",
        }.items():
            env[key] = str(base / name)
        (base / "config.yaml").write_text("{}\n")
        root = base / "blocked-app"
        fallback_host_started = time.monotonic()
        with _supervisor(root, env, node, managed=False) as (process, ready):
            row = ready(1)  # Pins the actual APP before it blocks its own event loop.
            descriptor = _owned_pidfd(row["pid"], process.pid)
            assert descriptor is not None
            try:
                (root / "block").write_text("block\n")
                _wait(
                    lambda: any(
                        event["kind"] == "blocked" and event["pid"] == row["pid"]
                        for event in _events(root)
                    ),
                    "APP did not enter its synchronous block",
                )
                fallback_stop_started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                remaining = min(75.0, fallback_host_started + 90.0 - time.monotonic())
                assert remaining > 0, "fallback host exhausted its original budget"
                assert process.wait(timeout=remaining) == 1
                fallback_elapsed = time.monotonic() - fallback_stop_started
                assert fallback_elapsed >= 60.0
                fallback_log = (root / "output.log").read_text()
                assert (
                    "Panels application exceeded the 60-second supervisor stop bound; "
                    "forcing its owned group."
                ) in fallback_log
                remaining = min(10.0, fallback_host_started + 90.0 - time.monotonic())
                assert remaining > 0, "APP exit observation exhausted the original host budget"
                # Observe exit before the context manager can force the fixture's APP.
                fallback_app_exited = bool(select.select([descriptor], [], [], remaining)[0])
                assert fallback_app_exited, "supervisor fallback did not stop its actual APP"
            finally:
                os.close(descriptor)
        fallback_host_seconds = time.monotonic() - fallback_host_started
        root = base / "foreground"
        with _supervisor(root, env, node, managed=False) as (process, ready):
            ready(1)
            socket_inode = (root / "control.sock").stat().st_ino
            with _supervisor(
                base / "contender", env, node, managed=False, lease=root / "run/server.lock"
            ) as (contender, _):
                assert contender.wait(timeout=10) == 1
            assert (root / "control.sock").stat().st_ino == socket_inode
            (root / "hold").write_text("hold\n")
            assert _control(root / "control.sock", dict(version=1, operation="restart")) == dict(
                version=1, status="accepted"
            )
            _wait(
                lambda: any(event["kind"] == "term" for event in _events(root)),
                "restart did not signal its child",
            )
            held = socket.socket(socket.AF_UNIX)
            held.settimeout(10)
            held.connect(str(root / "control.sock"))
            held.sendall(b"{")
            process.send_signal(signal.SIGTERM)
            assert held.recv(1) == b""
            assert _lease_is_held(root / "run/server.lock")
            (root / "hold").unlink()
            assert process.wait(timeout=10) == 0
            held.close()
            assert len([row for row in _events(root) if row["kind"] == "ready"]) == 1
        assert not (root / "control.sock").exists() and not _lease_is_held(root / "run/server.lock")
        # An actual successor owns the same lease only after complete prior cleanup.
        with _supervisor(root, env, node, managed=False) as (process, ready):
            ready(2)
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=10) == 0
        # A requested stop during restart must preserve failed APP shutdown.
        root = base / "failed-stop"
        with _supervisor(root, env, node, managed=False, app_exit_code=7) as (process, ready):
            ready(1)
            (root / "hold").write_text("hold\n")
            assert _control(root / "control.sock", dict(version=1, operation="restart")) == dict(
                version=1, status="accepted"
            )
            _wait(
                lambda: any(row["kind"] == "term" for row in _events(root)),
                "failed-stop restart did not signal APP",
            )
            process.send_signal(signal.SIGTERM)
            (root / "hold").unlink()
            assert process.wait(timeout=10) == 1
            assert len([row for row in _events(root) if row["kind"] == "ready"]) == 1
        assert not _lease_is_held(root / "run/server.lock")
        missing = base / "absent.sock"
        with pytest.raises(ServerRestartConnectionError) as expected:
            request_server_restart(missing)
        assert _call(base, env, node, "restart", dict(control_socket_path=str(missing))) == dict(
            error_type="ServerRestartConnectionError", message=str(expected.value)
        )
        bad = base / "invalid.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(bad))
            listener.listen()
            listener.settimeout(10)

            def answer() -> None:
                for _ in range(2):
                    client, _ = listener.accept()
                    with client:
                        client.recv(4096)
                        client.sendall(b'{"version":1,"status":"wrong"}\n')

            peer = threading.Thread(target=answer)
            peer.start()
            try:
                with pytest.raises(ServerRestartProtocolError) as expected_protocol:
                    request_server_restart(bad)
                assert _call(
                    base, env, node, "restart", dict(control_socket_path=str(bad))
                ) == dict(
                    error_type="ServerRestartProtocolError",
                    message=str(expected_protocol.value),
                )
            finally:
                peer.join(timeout=10)
                assert not peer.is_alive()
        if os.uname().sysname == "Linux":
            alias = base / "alias-refusal"
            (alias / "data").mkdir(parents=True)
            (alias / "runtime").mkdir()
            (alias / "data/skills").symlink_to(alias / "runtime", target_is_directory=True)
            with _supervisor(alias, env, node, managed=True) as (process, _):
                assert process.wait(timeout=10) == 1
                assert not (alias / "runtime/stop-capability.json").exists()
                assert not _events(alias)
            root = base / "managed"
            (root / "data").mkdir(parents=True)
            external_skills = base / "external-skills"
            external_skills.mkdir()
            (external_skills / "SKILL.md").write_text("synthetic managed skill\n")
            (root / "data/skills").symlink_to(external_skills, target_is_directory=True)
            (root / "data/worker-settings").mkdir()
            (root / "data/worker-settings/settings.json").write_text("{}\n")
            external_backups = base / "external-backups"
            external_backups.mkdir()
            (root / "backups").symlink_to(external_backups, target_is_directory=True)
            with sqlite3.connect(root / "data/app.sqlite") as database:
                database.execute("CREATE TABLE records(value TEXT)")
                database.execute("INSERT INTO records VALUES('private fixture')")
            with _supervisor(root, env, node, managed=True) as (process, ready):
                row = ready(1)
                cap_path = root / "runtime/stop-capability.json"
                record = json.loads(cap_path.read_text())
                capability_digest = hashlib.sha256(cap_path.read_bytes()).digest()
                assert cap_path.stat().st_mode & 0o777 == 0o600
                assert cap_path.parent.stat().st_mode & 0o777 == 0o700
                assert all(
                    hashlib.sha256(value.encode()).hexdigest() not in row[scope]
                    for scope in ("hashes", "app_hashes")
                    for value in (record["capability"], str(cap_path), str(cap_path.parent))
                )
                captured = _call(
                    base,
                    env,
                    node,
                    "capture",
                    dict(source=str(root / "data/app.sqlite"), destination=str(root / "backups")),
                )
                assert isinstance(captured, str)
                snapshot = Path(captured)
                assert snapshot.is_relative_to(external_backups)
                assert all(
                    record["capability"].encode() not in path.read_bytes()
                    for path in snapshot.rglob("*")
                    if path.is_file()
                )
                assert not list(snapshot.rglob("stop-capability.json"))
                process.send_signal(signal.SIGTERM)
                _wait(
                    lambda: "refused SIGTERM" in (root / "output.log").read_text(),
                    "bare TERM was not refused",
                )
                process.send_signal(signal.SIGINT)
                _wait(
                    lambda: "refused SIGINT" in (root / "output.log").read_text(),
                    "bare INT was not refused",
                )
                intent = dict(
                    version=2,
                    operation="stop_intent",
                    incarnation=record["incarnation"],
                    capability=record["capability"],
                )
                assert _control(root / "control.sock", {**intent, "capability": "0" * 64}) is None
                assert (
                    _control(root / "control.sock", b'{"version":1.0,"operation":"restart"}\n')
                    is None
                )
                assert _control(root / "control.sock", b"x" * 4097 + b"\n") is None
                assert _control(root / "control.sock", intent) == dict(
                    version=2,
                    status="accepted",
                    incarnation=record["incarnation"],
                    supervisor_pid=process.pid,
                )
                time.sleep(5.05)
                previous = (root / "output.log").read_text().count("refused SIGTERM")
                process.send_signal(signal.SIGTERM)
                _wait(
                    lambda: (root / "output.log").read_text().count("refused SIGTERM") > previous,
                    "expired intent was not refused",
                )
                assert _call(
                    base, env, node, "restart", dict(control_socket_path=str(root / "control.sock"))
                ) == dict(version=1, status="accepted")
                ready(2)
                assert hashlib.sha256(cap_path.read_bytes()).digest() == capability_digest
                held = socket.socket(socket.AF_UNIX)
                held.connect(str(root / "control.sock"))
                held.sendall(b"{")
                assert (
                    _call(
                        base,
                        env,
                        node,
                        "managed_stop",
                        dict(
                            runtime_directory=str(cap_path.parent),
                            control_socket_path=str(root / "control.sock"),
                        ),
                    )
                    is None
                )
                assert process.wait(timeout=10) == 0
                held.close()
                assert not cap_path.exists()
            with _supervisor(root, env, node, managed=True) as (process, ready):
                ready(3)
                fresh = json.loads(cap_path.read_text())
                rotated = (
                    fresh["incarnation"] != record["incarnation"]
                    and fresh["capability"] != record["capability"]
                )
                assert rotated
                assert _control(root / "control.sock", intent) is None
                (root / "hold").write_text("hold\n")
                count = len([event for event in _events(root) if event["kind"] == "term"])
                assert _control(
                    root / "control.sock", dict(version=1, operation="restart")
                ) == dict(version=1, status="accepted")
                _wait(
                    lambda: (
                        len([event for event in _events(root) if event["kind"] == "term"]) > count
                    ),
                    "managed restart did not reach held child",
                )
                pending_client = socket.socket(socket.AF_UNIX)
                pending_client.settimeout(10)
                pending_client.connect(str(root / "control.sock"))
                pending_client.sendall(b"{")
                current_intent = dict(
                    version=2,
                    operation="stop_intent",
                    incarnation=fresh["incarnation"],
                    capability=fresh["capability"],
                )
                assert _control(root / "control.sock", current_intent) is not None
                # Simulate a later capability file before old cleanup. The live intent
                # still authenticates the current supervisor's retained incarnation.
                successor_record = {
                    **fresh,
                    "incarnation": "next-incarnation",
                    "capability": "f" * 64,
                }
                replacement = cap_path.with_suffix(".replacement")
                replacement.write_text(json.dumps(successor_record) + "\n")
                replacement.chmod(0o600)
                replacement.replace(cap_path)
                process.send_signal(signal.SIGTERM)
                assert pending_client.recv(1) == b""
                pending_client.close()
                assert _lease_is_held(root / "run/server.lock")
                (root / "hold").unlink()
                assert process.wait(timeout=10) == 0
                assert len([event for event in _events(root) if event["kind"] == "ready"]) == 3
                preserved = json.loads(cap_path.read_text()) == successor_record
                assert preserved
    report = request.config.getoption("compare_report_dir", default=None)
    if report:
        Path(report).mkdir(parents=True, exist_ok=True)
        (Path(report) / "unit-21-supervisor.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - started,
                    "real_supervisor_fallback": {
                        "stop_seconds": fallback_elapsed,
                        "host_seconds": fallback_host_seconds,
                        "exit_code": 1,
                        "app_blocked": True,
                        "app_exit_observed": fallback_app_exited,
                        "log": fallback_log,
                        "additional_invocations": 1,
                        "additional_node_processes": 3,
                    },
                    "environment_scope": (
                        "Fixture APP and one inherited generic Node descendant; no vendor session"
                    ),
                    "boundary": (
                        "Owned foreground/managed process lifetime, v1 restart bytes/errors, "
                        "private capability and native pidfd stop. "
                        "Private result/error data, no CLI rendering. "
                        "Actual systemd/installer/"
                        "real-core-provider boundaries remain separate hosted proofs."
                    ),
                },
                indent=2,
            )
            + "\n"
        )
