"""Read exact old launcher identity; repair only an explicitly selected conversation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from planner.conversation.child_custody import process_identity
from planner.conversation.deploy_interruptions.contracts import CaptureManifest
from planner.conversation.local_child_contracts import LegacySourceContext, LocalChildIdentity
from planner.conversation.worker_unit import WorkerUnit
from planner.conversation.worker_unit.config import (
    CONTROL_TIMEOUT_SECONDS,
    WORKER_SLICE,
    WORKER_TEMP_ROOT,
)
from planner.executor.configuration import ORPHAN_AGENT_TERMINATION_GRACE_SECONDS

_UNIT = re.compile(r"panels-worker-[0-9a-f]{32}\.service")


def _control(*arguments: str) -> str:
    result = subprocess.run(
        ["systemctl", "--user", *arguments],
        capture_output=True,
        text=True,
        timeout=CONTROL_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout


def _properties(unit: str) -> dict[str, str]:
    text = _control(
        "show", unit,
        "--property=MainPID,ControlGroup,EnvironmentFiles,Slice,KillMode,ActiveState,SubState,Result"
    )
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _matches(environment: dict[str, str], context: LegacySourceContext) -> bool:
    if environment.get("PLAN_CONVERSATION_ID") != context.conversation_id:
        return False
    expected = dict(context.identity_variables)
    if (
        not expected.get("PLAN_ACTOR")
        or expected.get("PLAN_CONVERSATION_ID") != context.conversation_id
    ):
        raise ValueError("the previous launcher has no persisted employee identity")
    if any(environment.get(key) != value for key, value in expected.items()):
        raise ValueError("the previous launcher identity is ambiguous")
    database = environment.get("PLAN_DB_PATH")
    if database:
        if Path(database).resolve() != Path(context.database_path).resolve():
            raise ValueError("the previous launcher's database context is ambiguous")
        return True
    if context.server_url is None:
        raise ValueError("the previous launcher has no verifiable home endpoint")
    if environment.get("PLAN_SERVER_URL") != context.server_url:
        raise ValueError("the previous launcher's home endpoint is ambiguous")
    return True


def _unit_environment(properties: dict[str, str]) -> tuple[Path, dict[str, str]]:
    value = properties.get("EnvironmentFiles", "")
    suffix = " (ignore_errors=no)"
    if not value.endswith(suffix):
        raise ValueError("the Worker unit has no exact environment file")
    path = Path(value[: -len(suffix)])
    directory = path.parent
    root = Path(WORKER_TEMP_ROOT).expanduser().resolve()
    if (
        path.name != "environment"
        or not directory.name.startswith(".pw-")
        or directory.parent.resolve() != root
        or directory.resolve() != directory
        or path.is_symlink()
        or path.stat().st_uid != os.getuid()
        or directory.stat().st_uid != os.getuid()
        or properties.get("Slice") != WORKER_SLICE
        or properties.get("KillMode") != "control-group"
    ):
        raise ValueError("the Worker unit ownership is not established")
    environment = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and value.startswith('"') and value.endswith('"'):
            environment[key] = (
                value[1:-1]
                .replace('\\"', '"')
                .replace("\\$", "$")
                .replace("\\`", "`")
                .replace("\\\\", "\\")
            )
    return directory, environment


def _process_directories() -> tuple[Path, ...]:
    return tuple(path for path in Path("/proc").iterdir() if path.name.isdigit())


def _process_unit(path: Path) -> str | None:
    groups = (path / "cgroup").read_text()
    return next((part for part in re.split(r"[/\n]", groups) if _UNIT.fullmatch(part)), None)


def inspect_source(context: LegacySourceContext) -> tuple[LocalChildIdentity, ...] | None:
    """None means incomplete inspection. Only a complete empty result proves absence."""
    if sys.platform != "linux" or not dict(context.identity_variables).get("PLAN_ACTOR"):
        return None
    found: dict[tuple[str, int], LocalChildIdentity] = {}
    try:
        for line in _control(
            "list-units", "--all", "--plain", "--no-legend", "--no-pager", "panels-worker-*.service"
        ).splitlines():
            unit = line.split()[0]
            if not _UNIT.fullmatch(unit):
                continue
            properties = _properties(unit)
            required = {"ActiveState", "ControlGroup", "MainPID", "Slice", "KillMode"}
            if not required.issubset(properties):
                return None
            active = properties["ActiveState"] not in {"inactive", "failed"}
            group = properties["ControlGroup"]
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            if group:
                if events.exists():
                    text = events.read_text()
                    if "populated 1" in text:
                        populated = True
                    elif "populated 0" in text:
                        populated = False
                    else:
                        return None
                elif events.parent.exists():
                    return None
                else:
                    populated = False
            else:
                terminal = (
                    properties["MainPID"] == "0"
                    and properties["ActiveState"] == "inactive"
                    and properties.get("SubState") == "dead"
                    and properties.get("Result") == "success"
                    and properties["KillMode"] == "control-group"
                    and properties["Slice"] == WORKER_SLICE
                )
                populated = not terminal
            if not active and not populated:
                continue
            _, environment = _unit_environment(properties)
            if not environment.get("PLAN_ACTOR") or not environment.get("PLAN_CONVERSATION_ID"):
                return None
            if _matches(environment, context):
                pid = int(properties.get("MainPID", "0"))
                identity = process_identity(pid, unit) if pid else None
                found[(unit, pid)] = identity or LocalChildIdentity(0, "", "", unit)
        for path in _process_directories():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                # A zombie still has a proc directory, but cannot execute work.
                state = (path / "stat").read_text().rsplit(")", 1)[1].split()[0]
                if state in {"Z", "X"}:
                    continue
                raw = (path / "environ").read_bytes()
                selected = set(dict(context.identity_variables)) | {
                    "PLAN_CONVERSATION_ID",
                    "PLAN_DB_PATH",
                    "PLAN_SERVER_URL",
                }
                environment = {}
                for item in raw.split(b"\0"):
                    key, separator, value = item.partition(b"=")
                    name = key.decode(errors="replace")
                    if separator and name in selected:
                        environment[name] = value.decode(errors="strict")
            except (FileNotFoundError, ProcessLookupError):
                continue
            if not _matches(environment, context):
                continue
            identity = process_identity(int(path.name))
            if identity is None:
                if path.exists():
                    return None
                continue
            process_unit = _process_unit(path)
            if process_unit is not None:
                if not any(value.unit == process_unit for value in found.values()):
                    return None
            else:
                found[("", identity.pid)] = identity
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, IndexError):
        return None
    return tuple(found.values())


def _signal_exact(source: LocalChildIdentity, signum: int) -> None:
    try:
        descriptor = os.pidfd_open(source.pid)
    except ProcessLookupError:
        return
    try:
        if process_identity(source.pid) == source:
            signal.pidfd_send_signal(descriptor, signum)
    finally:
        os.close(descriptor)


def repair_source(context: LegacySourceContext) -> bool:
    """The existing explicit Restart Worker action owns this exact-source cleanup."""
    sources = inspect_source(context)
    if sources is None:
        return False
    try:
        for unit in {source.unit for source in sources if source.unit is not None}:
            assert unit is not None
            properties = _properties(unit)
            directory, environment = _unit_environment(properties)
            if not _matches(environment, context):
                return False
            worker = WorkerUnit(
                name=unit,
                directory=directory,
                environment={},
                working_directory=directory,
                slice_name=WORKER_SLICE,
            )
            asyncio.run(worker.stop())
        direct = [source for source in sources if source.unit is None]
        for source in direct:
            _signal_exact(source, signal.SIGTERM)
        deadline = time.monotonic() + ORPHAN_AGENT_TERMINATION_GRACE_SECONDS
        while time.monotonic() < deadline:
            if inspect_source(context) == ():
                return True
            time.sleep(0.05)
        for source in direct:
            _signal_exact(source, signal.SIGKILL)
        return inspect_source(context) == ()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def checked_update_stop(db_path: str, manifest: CaptureManifest) -> bool:
    """Match the old installer's authorized stop and capture, never a bare snapshot."""
    path = Path(db_path).resolve()
    root = path.parent.parent
    if path != (root / "data/planner.db").resolve() or manifest.state != "captured":
        return False
    try:
        attempt = json.loads((path.parent / "update-attempt.json").read_text())
        app = json.loads((root / "app/manifest.json").read_text())
        if (
            attempt["attempt_id"] != manifest.deployment_id
            or app["app_sha"] != attempt["to_sha"]
            or attempt["from_sha"] == attempt["to_sha"]
            or not all(
                re.fullmatch(r"[0-9a-f]{40}", attempt[key]) for key in ("from_sha", "to_sha")
            )
            or attempt["state"] not in {"installing", "done"}
        ):
            return False
        steps = attempt["steps"]
        times = [
            datetime.fromisoformat(steps[key])
            for key in (
                "work_paused",
                "installing",
                "service_stopped",
                "interruptions_captured",
                "app_swapped",
            )
        ]
        return (
            times == sorted(times)
            and times[2].timestamp() <= manifest.captured_at + 1
            and manifest.captured_at <= times[3].timestamp()
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
