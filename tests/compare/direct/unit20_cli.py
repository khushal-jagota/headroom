"""Guarded CLI comparisons: fixture capability, argv and exact output bytes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import httpx
from tests.support.server import ServerHandle
from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]
PYTHON = [
    sys.executable,
    "-c",
    "from planner.cli.main import main; main(prog_name='panels')",
]
TYPESCRIPT = ["node", str(ROOT / "server/cli/main.ts")]
LIVE_URL = os.environ.get("PLAN_SERVER_URL", "http://127.0.0.1:8767")
ACTORS = {"owner", "chief", "worker", "manager", "unidentified"}


@dataclass(frozen=True)
class CliHost:
    handle: ServerHandle
    root: Path

    def check(self) -> None:
        """Refuse live/foreign addresses before files, network or subprocesses."""
        if not isinstance(self.handle, ServerHandle):
            raise ValueError("CLI comparison requires a fixture-owned host handle")
        base, live = urlsplit(self.handle.base), urlsplit(LIVE_URL)
        if (
            base.scheme != "http"
            or base.hostname != "127.0.0.1"
            or base.port != self.handle.port
            or base.port in (None, 8767)
            or (base.hostname, base.port) == (live.hostname, live.port)
            or base.username is not None
            or base.password is not None
            or base.path
            or base.query
            or base.fragment
        ):
            raise ValueError("CLI comparison refuses a live or foreign server address")
        root, temp = (
            self.root.resolve(strict=True),
            Path(tempfile.gettempdir()).resolve(),
        )
        if not root.is_relative_to(temp) or root == temp:
            raise ValueError("CLI comparison requires a private temporary root")
        if not self.handle.db_path.resolve(strict=True).is_relative_to(root):
            raise ValueError("CLI comparison database is outside its fixture root")
        if self.handle.proc.poll() is not None:
            raise ValueError("CLI comparison host is not running")
        # The URL and database paths alone do not establish listener ownership.
        # Both comparison launchers exec their server, so the handle PID owns its FD.
        pid = getattr(self.handle.proc, "pid", None)
        if sys.platform != "linux" or type(pid) is not int or pid <= 0:
            raise ValueError("CLI comparison requires Linux listener PID evidence")
        try:
            listeners = [
                line.split() for line in Path("/proc/net/tcp").read_text().splitlines()[1:]
            ]
            inodes = {
                row[9]
                for row in listeners
                if row[3] == "0A" and row[1] == f"0100007F:{self.handle.port:04X}"
            }
            owned = set()
            for descriptor in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    owned.add(os.readlink(descriptor))
                except FileNotFoundError:
                    continue  # A concurrent request can close its unrelated FD.
            if len(inodes) != 1 or not all(f"socket:[{inode}]" in owned for inode in inodes):
                raise ValueError("CLI comparison listener is not owned by its fixture PID")
        except OSError as error:
            raise ValueError("CLI comparison cannot establish listener PID ownership") from error
        if self.handle.proc.poll() is not None:
            raise ValueError("CLI comparison host exited during listener admission")


def environment(host: CliHost, identity: dict[str, str], root: Path) -> dict[str, str]:
    host.check()
    if set(identity) - {"actor", "ticket_id", "manager_id", "conversation_id"}:
        raise ValueError("CLI identity contains an unsupported field")
    actor = identity.get("actor")
    if actor not in ACTORS:
        raise ValueError("CLI comparison requires an explicit synthetic actor")
    if actor == "worker" and not identity.get("ticket_id", "").startswith("t_"):
        raise ValueError("synthetic worker identity requires a Ticket")
    if actor == "manager" and not identity.get("manager_id", "").startswith("si_"):
        raise ValueError("synthetic manager identity requires a Manager")
    # No inherited PLAN_*, provider homes, proxies, credentials or PYTHONPATH.
    result = {key: os.environ[key] for key in ("PATH", "LANG", "TZ") if key in os.environ}
    result.update(create_synthetic_homes(root))
    result.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "PLAN_SERVER_URL": host.handle.base,
            "PLAN_PORT": str(host.handle.port),
            "PLAN_TEST_MODE": "1",
            "PLAN_CONFIG_PATH": str(root / "config.yaml"),
            "PLAN_DB_PATH": str(root / "unused-client.db"),
            "PLAN_WORKSPACE_ROOT": str(root / "workspace"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
        }
    )
    (root / "config.yaml").write_text("{}\n")
    if actor != "unidentified":
        # Python names a direct fixture owner with a blank actor. "owner" is
        # not a wire principal. This explicit test identity is no CLI default.
        result["PLAN_ACTOR"] = "" if actor == "owner" else actor
    for key in ("ticket_id", "manager_id", "conversation_id"):
        if key in identity:
            result["PLAN_" + key.upper()] = identity[key]
    return result


def _admit(host: CliHost, root: Path) -> None:
    host.check()
    if not root.resolve().is_relative_to(host.root.resolve()):
        raise ValueError("CLI input root is outside its fixture root")
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        metadata = client.get(host.handle.base + "/api/meta")
        if metadata.status_code != 200 or metadata.json().get("test_mode") is not True:
            raise ValueError("CLI comparison requires a test-mode fixture host")


def _python_library(argv: list[str], stdin: bytes, env: dict[str, str]) -> dict[str, Any]:
    """Invoke fresh production parsing with exact streams and no inherited identity.

    Unexpected exceptions propagate. Click's SystemExit remains the real CLI result.
    """
    with patch.dict(os.environ, env, clear=True):
        from click.testing import CliRunner

        from planner.cli.main import main

        result = CliRunner().invoke(
            main,
            argv,
            input=stdin,
            prog_name="panels",
            catch_exceptions=False,
            # CliRunner forces 80 columns; physical non-TTY Click uses 80 - 2.
            terminal_width=78,
        )
        if result.exception is not None and not isinstance(result.exception, SystemExit):
            raise result.exception
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout_bytes.decode("utf-8"),
            "stderr": result.stderr_bytes.decode("utf-8"),
        }


def _check_output(
    answer: dict[str, Any],
    step: dict[str, Any],
    argv: list[str],
    mode: str,
    *,
    candidate: bool = False,
) -> None:
    assert not (candidate and mode == "text") or all(
        marker not in answer[stream]
        for stream in ("stdout", "stderr")
        for marker in ("undefined", "[object Object]")
    ), {
        "argv": argv,
        "reason": "TypeScript CLI leaked a JavaScript rendering placeholder",
        **answer,
    }
    if answer["exit_code"] != step["exit_code"]:
        raise AssertionError({"argv": argv, "expected_exit": step["exit_code"], **answer})
    if mode == "json" and answer["exit_code"] == 0 and "--help" not in argv:
        body = json.loads(answer["stdout"])
        for pointer, expected in step.get("assert_json", {}).items():
            value = body
            for part in pointer.split(".") if pointer else []:
                value = value[int(part)] if isinstance(value, list) else value[part]
            assert value == expected, (argv, pointer, value, expected)
    for text in step.get("assert_text", []):
        assert text in answer["stdout"] + answer["stderr"], (argv, text, answer)


def help_batch(host: CliHost, cases: list[tuple[list[str], int]], root: Path) -> dict[str, Any]:
    """One Node process and parent-process Click execute the actual help/error code."""
    started = time.perf_counter()
    if any(
        not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv)
        for argv, _ in cases
    ):
        raise ValueError("help cases require argv string arrays")
    _admit(host, root)
    env = environment(host, {"actor": "owner"}, root)
    python = [_python_library(argv, b"", env) for argv, _ in cases]
    completed = subprocess.run(
        ["node", str(ROOT / "server/cli/testing/help_batch.ts")],
        cwd=ROOT,
        env=env,
        input=json.dumps([argv for argv, _ in cases]).encode("utf-8"),
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8"))
    typescript = json.loads(completed.stdout)
    assert isinstance(typescript, list) and len(typescript) == len(cases)
    for (argv, code), left, right in zip(cases, python, typescript, strict=True):
        _check_output(left, {"exit_code": code}, argv, "text")
        _check_output(right, {"exit_code": code}, argv, "text", candidate=True)
    return {
        "python": python,
        "typescript": typescript,
        "strategy": {"python": "CliRunner.invoke", "typescript": "one main.run batch"},
        "seconds": time.perf_counter() - started,
    }


def run_cli(
    host: CliHost,
    step: dict[str, Any],
    candidate: bool,
    root: Path,
    mode: str = "json",
    *,
    python_in_process: bool = False,
) -> dict[str, Any]:
    allowed = {
        "kind",
        "area",
        "argv",
        "stdin",
        "files",
        "identity",
        "exit_code",
        "capture",
        "capture_text",
        "proof",
        "assert_json",
        "assert_text",
        "assert_http_status",
    }
    if set(step) - allowed:
        raise ValueError(f"unsupported CLI step fields: {sorted(set(step) - allowed)}")
    host.check()
    if not isinstance(step.get("argv"), list) or not all(isinstance(x, str) for x in step["argv"]):
        raise ValueError("CLI argv must be a string array")
    if not step.get("proof"):
        raise ValueError("each CLI step must state its observable proof")
    if candidate and python_in_process:
        raise ValueError("the in-process reference strategy is Python-only")
    _admit(host, root)
    env, argv = environment(host, step["identity"], root), list(step["argv"])
    for name, text in step.get("files", {}).items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
            raise ValueError("CLI fixture name must be one relative filename")
        if not isinstance(text, str):
            raise ValueError("CLI fixture must contain UTF-8 text")
        path = root / name
        path.write_bytes(text.encode("utf-8"))
        argv = [arg.replace("@file:" + name, str(path)) for arg in argv]
    if mode == "json" and "--help" not in argv:
        argv.append("--json")
    elif mode not in ("text", "json"):
        raise ValueError("unsupported CLI output mode")
    from tests.compare.direct.unit20_wire import python_capture, read_capture

    with tempfile.NamedTemporaryFile(dir=root, prefix="wire-", delete=False) as private:
        wire_file = Path(private.name)
    started = time.perf_counter()
    if python_in_process:
        with python_capture(wire_file, host.handle.base):
            answer = _python_library(argv, step.get("stdin", "").encode("utf-8"), env)
    else:
        command = (
            [
                "node",
                "--import",
                str(ROOT / "tests/compare/direct/unit20_wire.mjs"),
                str(ROOT / "server/cli/main.ts"),
                str(wire_file),
            ]
            if candidate
            else [sys.executable, str(ROOT / "tests/compare/direct/unit20_wire.py"), str(wire_file)]
        )
        completed = subprocess.run(
            [*command, *argv],
            cwd=ROOT,
            env=env,
            input=step.get("stdin", "").encode("utf-8"),
            capture_output=True,
            timeout=40,
            check=False,
        )
        answer = {
            "exit_code": completed.returncode,
            "stdout": completed.stdout.decode("utf-8"),
            "stderr": completed.stderr.decode("utf-8"),
        }
    elapsed = time.perf_counter() - started
    wire = read_capture(wire_file)
    # Fix the actual fixture file's input time before a later workspace read.
    for row in wire:
        url_path = urlsplit(row["path"]).path
        if row["method"] == "PUT" and 200 <= row["status"] < 300 and url_path.startswith("/files/"):
            host.check()
            files = host.handle.db_path.parent / "files"
            relative = Path(unquote(url_path)[len("/files/") :])
            assert not relative.is_absolute() and ".." not in relative.parts
            if relative.parts[0] == "managers":
                relative = Path("sprint-items", *relative.parts[1:])
            target = files / relative
            assert target.resolve(strict=True).is_relative_to(files.resolve(strict=True))
            for component in (target, *target.parents):
                if component == files.parent:
                    break
                assert not component.is_symlink()
            assert target.is_file()
            os.utime(target, (0, 0), follow_symlinks=False)
    if "assert_http_status" in step:
        assert [row["status"] for row in wire] == [step["assert_http_status"]], (argv, wire)
    offline = tuple(argv[:2]) in {
        ("goal", "schema"),
        ("page", "schema"),
        ("measure", "schema"),
        ("measure-source", "schema"),
        ("measure-source", "runtime"),
    }
    if offline or "--help" in argv:
        assert not wire, (argv, wire)
    elif answer["exit_code"] == 0:
        assert wire, ("successful HTTP command had no captured response", argv)
    _check_output(answer, step, argv, mode, candidate=candidate)
    return {
        **answer,
        "wire": wire,
        "seconds": elapsed,
        "strategy": "CliRunner.invoke" if python_in_process else "subprocess",
    }


def cli_capture(
    answer: dict[str, Any],
    step: dict[str, Any],
    captures: dict[str, Any],
    mode: str = "json",
) -> None:
    if mode == "json":
        if not step.get("capture"):
            return
        body = json.loads(answer["stdout"])
        for name, pointer in step["capture"].items():
            value = body
            for part in pointer.split(".") if pointer else []:
                value = value[int(part)] if isinstance(value, list) else value[part]
            captures[name] = value
    else:
        for name, expression in step.get("capture_text", {}).items():
            found = re.search(expression, answer["stdout"])
            assert found is not None, (name, expression, answer)
            captures[name] = found.group(1)


def comparable(answer: dict[str, Any]) -> dict[str, Any]:
    """Runtime is evidence, never output normalization."""
    return {key: answer[key] for key in ("exit_code", "stdout", "stderr")}
