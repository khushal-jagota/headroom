"""Mac launch and retained-child composition, not another vendor adapter suite."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation import production_backends
from planner.conversation.contracts import ConversationBackendKey
from planner.execution_identity import ChildReference
from planner.executor import mac_runner
from planner.executor.configuration import ExecutorPaths
from planner.executor.mac_catalog import MacCatalog
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandResult,
    ModelListRequest,
    StartRun,
    StopRun,
    as_object,
    type_adapter,
)
from planner.executor.skills import native_skill_home

ROOT = Path(__file__).resolve().parents[3]


def normalized(value: Any, root: Path, capability: str | None = None) -> Any:
    if isinstance(value, str):
        if capability and value == capability:
            return "<capability>"
        return value.replace(str(root), "<root>")
    if isinstance(value, (list, tuple)):
        return [normalized(item, root, capability) for item in value]
    if isinstance(value, dict):
        return {key: normalized(item, root, capability) for key, item in value.items()}
    return value


def start_inputs(root: Path) -> list[dict[str, Any]]:
    values = []
    for backend, policy, workspace in (
        ("codex", "executor_default", "/vps/workspace"),
        ("claude", "home_relative", "/vps/project"),
        ("hermes", "absolute", str(root / "absolute")),
    ):
        identity = [
            ["PLAN_ACTOR", "worker"],
            ["PLAN_TICKET_ID", "ticket-" + backend],
            ["KEPT_IDENTITY", "retained"],
            *[
                [key, "/vps/" + key]
                for key in (
                    "HOME",
                    "USERPROFILE",
                    "CODEX_HOME",
                    "HERMES_HOME",
                    "CLAUDE_CONFIG_DIR",
                    "PLAN_EXECUTOR_ROOT",
                    "PLAN_EXECUTOR_CHILD_ID",
                    "PLAN_EXECUTOR_CAPABILITY",
                    "PLAN_TICKET_FILES_DIR",
                    "PLAN_NATIVE_SKILLS_DIR",
                )
            ],
        ]
        values.append(
            dict(
                resolved_start=dict(
                    conversation_id="conversation-" + backend,
                    backend_key=backend,
                    model=backend + "-model",
                    reasoning_effort="high",
                    access="full",
                    workspace_folder=workspace,
                    workspace_path_policy=policy,
                    role_materials=dict(
                        role_text="Canonical Worker role", identity_environment_variables=identity
                    ),
                ),
                canonical_skill_snapshot=dict(sources=[], required_skill_names=[]),
                vendor_session_cursor="resume-" + backend,
                workspace_home_relative="project" if policy != "absolute" else None,
            )
        )
    return values


async def reference(root: Path, patch: pytest.MonkeyPatch) -> dict[str, Any]:
    root.mkdir()
    homes = {
        key: str(root / value)
        for key, value in {
            "HOME": "home",
            "CODEX_HOME": "native/codex",
            "CLAUDE_CONFIG_DIR": "native/claude",
            "PLAN_HERMES_HOME": "native/hermes",
            "PLAN_HERMES_PYTHON": "hermes/venv/bin/python",
            "PLAN_APP_ROOT": "application",
        }.items()
    }
    for key, value in homes.items():
        patch.setenv(key, value)
    launch_cases = []
    for override, installed in (
        (
            homes["PLAN_APP_ROOT"]
            + "/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
            True,
        ),
        (str(root / "owner/../alternate-claude"), True),
        (None, False),
    ):
        if override is None:
            patch.delenv("PLAN_CLAUDE_EXECUTABLE", raising=False)
        else:
            patch.setenv("PLAN_CLAUDE_EXECUTABLE", override)

        def executable_path(name: str, installed: bool = installed) -> str | None:
            return str(root / "bin" / name) if installed else None

        launches = production_backends.production_backend_launches(
            panels_server_url="http://127.0.0.1:9",
            executable_path=executable_path,
        )
        launch_cases.append(
            {
                "launches": json.loads(json.dumps(asdict(launches), default=str)),
                "homes": {
                    backend.value: str(native_skill_home(backend))
                    for backend in ConversationBackendKey
                },
            }
        )
    paths = ExecutorPaths(root / "state")
    (root / "home/project").mkdir(parents=True)
    (root / "absolute").mkdir()
    (root / "not-directory").write_text("regular file", encoding="utf-8")
    calls: list[Any] = []
    starts = start_inputs(root)

    def publish(snapshot: Any, *, paths: ExecutorPaths, native_home: Path) -> Path:
        calls.append(["skills", str(native_home), as_object(snapshot)])
        generation = paths.skills / "fixture-generation"
        (generation / "skills").mkdir(parents=True, exist_ok=True)
        return generation

    patch.setattr(mac_runner, "install_native_skill_snapshot", publish)
    patch.setattr(mac_runner, "native_skill_home", lambda backend: root / "native" / backend.value)

    class Native:
        def __init__(self, backend: str) -> None:
            self.backend = backend

        async def start(self, resolved: Any, *, vendor_session_cursor: str | None) -> None:
            capability = dict(resolved.role_materials.identity_environment_variables)[
                "PLAN_EXECUTOR_CAPABILITY"
            ]
            calls.append(
                [
                    "start",
                    self.backend,
                    normalized(as_object(resolved), root, capability),
                    vendor_session_cursor,
                ]
            )
            if resolved.model == "start-fail":
                raise RuntimeError("scripted native start failed")

        async def stop(self) -> None:
            calls.append(["stop", self.backend])

    def factory(resolved: Any) -> Any:
        def create(**options: Any) -> Native:
            assert options["resolved_start"] == resolved
            capability = dict(resolved.role_materials.identity_environment_variables)[
                "PLAN_EXECUTOR_CAPABILITY"
            ]
            calls.append(
                [
                    "factory",
                    resolved.backend_key.value,
                    normalized(as_object(resolved), root, capability),
                ]
            )
            return Native(resolved.backend_key.value)

        return create

    class Processes:
        def record_children(self) -> None:
            calls.append(["record_children"])

    runner = MacRunner(
        incarnation="inc",
        outbox=cast(Any, None),
        factory=factory,
        message_files=cast(Any, None),
        paths=paths,
        workspace_home=root / "home",
        agent_processes=cast(Any, Processes()),
    )
    patch.setattr(runner, "_mark_invoking", lambda command: None)
    errors = []
    for index, raw in enumerate(starts):
        start = type_adapter(StartRun).validate_python(raw)
        reference = ChildReference(
            "inc", "child-" + str(index), start.resolved_start.conversation_id
        )
        command = ChildCommand("start-" + str(index), reference, ChildOperation.start, None, start)
        await runner._start_run(command, start)
        child = runner._children[reference.child_id]
        capability = child.capability
        # The capability is random in both runtimes. Its placement and refusal are exact.
        calls[:] = normalized(calls, root, capability)
        assert runner.authenticate(reference.child_id, capability) is child
        for child_id, supplied in (
            (reference.child_id, "wrong"),
            (None, capability),
        ):
            with pytest.raises(PermissionError) as auth_error:
                runner.authenticate(child_id, supplied)
            errors.append(str(auth_error.value))
        duplicate = type_adapter(StartRun).validate_python(raw)
        with pytest.raises(Exception) as duplicate_error:
            await runner._start_run(command, duplicate)
        errors.append(str(duplicate_error.value))
        if index == 0:
            other = ChildCommand(
                "same-ticket",
                ChildReference("inc", "another-child", reference.conversation_id),
                ChildOperation.start,
                None,
                start,
            )
            with pytest.raises(Exception) as ticket_error:
                await runner._start_run(other, start)
            errors.append(str(ticket_error.value))
        stop = ChildCommand("stop-" + str(index), reference, ChildOperation.stop, None, StopRun())
        outcome = await runner._stop_exact_child(stop)
        assert outcome.disposition.value == "succeeded"
        with pytest.raises(PermissionError) as retired_error:
            runner.authenticate(reference.child_id, capability)
        errors.append(str(retired_error.value))

    failed = type_adapter(StartRun).validate_python(starts[-1])
    failed = replace(failed, resolved_start=replace(failed.resolved_start, model="start-fail"))
    reference = ChildReference("inc", "failed-child", failed.resolved_start.conversation_id)
    with pytest.raises(RuntimeError, match="scripted native start failed") as start_error:
        await runner._start_run(
            ChildCommand("failed-start", reference, ChildOperation.start, None, failed), failed
        )
    errors.append(str(start_error.value))
    assert reference.child_id not in runner._children

    missing = replace(
        failed,
        resolved_start=replace(
            failed.resolved_start,
            model="hermes-model",
            workspace_folder=root / "missing" / "project",
        ),
    )
    reference = ChildReference("inc", "missing-workspace", missing.resolved_start.conversation_id)
    with pytest.raises(Exception) as workspace_error:
        await runner._start_run(
            ChildCommand("missing-start", reference, ChildOperation.start, None, missing), missing
        )
    errors.append(str(workspace_error.value))
    assert reference.child_id not in runner._children
    not_directory = replace(
        failed,
        resolved_start=replace(
            failed.resolved_start, workspace_folder=root / "not-directory" / ".." / "absolute"
        ),
    )
    reference = ChildReference(
        "inc", "not-directory-workspace", not_directory.resolved_start.conversation_id
    )
    with pytest.raises(Exception) as directory_error:
        await runner._start_run(
            ChildCommand(
                "not-directory-start", reference, ChildOperation.start, None, not_directory
            ),
            not_directory,
        )
    errors.append(str(directory_error.value))
    assert reference.child_id not in runner._children

    # The Mac wrapper passes command flags without starting advisory discovery.
    catalog_calls: list[Any] = []

    class Snapshots:
        async def snapshot(self, backend: Any, **options: Any) -> Any:
            from planner.conversation.snapshot import BackendModel, BackendSnapshot

            catalog_calls.append([backend.value, options])
            if backend.value == "hermes":
                raise ConnectionError("synthetic optional catalog unavailable")
            return BackendSnapshot(
                backend,
                True,
                "/synthetic/catalog/" + backend.value,
                "1.2.3",
                None,
                (
                    BackendModel(
                        backend.value + "-model",
                        display_name="🦉" * 270_000 if backend.value == "codex" else None,
                        reasoning_effort_options=("high",),
                        version=(9_007_199_254_741_003,) if backend.value == "codex" else (),
                    ),
                ),
                ("high",),
                backend.value + "-model",
                "high",
                None,
                (),
                True,
            )

    catalog = MacCatalog(cast(Any, Snapshots()))
    catalogs: list[list[Any]] = []
    for backend, refresh in (("codex", False), ("claude", True), ("hermes", True)):
        snapshot = await catalog.read(ModelListRequest(backend, refresh))
        canonical_bytes = type_adapter(CommandResult).dump_json(
            CommandResult("model-" + backend, model_list=snapshot)
        )
        catalogs.append([len(canonical_bytes), hashlib.sha256(canonical_bytes).hexdigest()])
    assert catalogs[0][0] > 1024 * 1024
    await catalog.settle()
    return cast(
        dict[str, Any],
        normalized(
            dict(
                launches=launch_cases,
                calls=calls,
                errors=errors,
                catalog_calls=catalog_calls,
                catalogs=catalogs,
            ),
            root,
        ),
    )


def test_unit22_mac_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), monkeypatch.context() as patch:
        expected = asyncio.run(asyncio.wait_for(reference(tmp_path / "py", patch), 20))
    target = tmp_path / "ts"
    target.mkdir()
    source = target / "input.json"
    source.write_text(json.dumps(dict(starts=start_inputs(target))))
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in homes
    }
    environment.update(homes)
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    process = subprocess.Popen(
        ["node", "server/executor/mac/testing/adapters_parity.ts", str(target)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == 0, stdout + stderr
    observed = json.loads(stdout)
    (target / "expected.json").write_text(json.dumps(expected, indent=2))
    (target / "observed.json").write_text(json.dumps(observed, indent=2))
    assert observed["parity"] == expected
    assert observed["lifecycle"] and all(observed["lifecycle"].values())
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit-22-mac-adapters.json").write_text(
            json.dumps(
                dict(
                    kind="direct",
                    equal=True,
                    **{key: value for key, value in observed.items() if key != "elapsed_seconds"},
                    node_elapsed_seconds=observed["elapsed_seconds"],
                    elapsed_seconds=time.perf_counter() - started,
                    boundary=(
                        "Mac composition and retained-child custody; no network or provider process"
                    ),
                ),
                indent=2,
            )
        )
