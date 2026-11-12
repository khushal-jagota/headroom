"""One plain-function comparison proves operator streams and exception boundaries.

The seven Part 3 storage/registry/scratch proofs own physical behavior. This adds
only the adapter boundary, with the same synthetic status and Git fixtures.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pytest
from tests.compare.direct.test_unit21_status import _seed
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_database_backups import _legacy_snapshot
from tests.unit.test_ticket_scratch import _layout

from planner.core.config import load_config
from planner.environments import cli, vps_status

ROOT = Path(__file__).resolve().parents[3]


def _python(root: Path, calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    config = load_config(
        path=str(root / "config.yaml"),
        env={
            **os.environ,
            "PLAN_DB_PATH": str(root / "runtime/database.sqlite"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_APP_SHA": "a" * 40,
            "PLAN_TICKET_SCRATCH_ROOT": str(root / "git/panels"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "runtime/database.sqlite"),
        },
    )
    deps = cli.EnvironmentCliDependencies(
        load_config=lambda: config,
        vps_status_dependencies=vps_status.VpsStatusDependencies(
            now=lambda: datetime.fromisoformat("2026-11-05T12:00:00+00:00"),
            platform_name=lambda: "Darwin",
            statvfs=lambda _: type(
                "Disk", (), dict(f_frsize=4096, f_blocks=100_000_000, f_bavail=60_000_000)
            )(),
            process_lines=lambda: (),
            git_worktree_output=lambda _: "",
            live_reference_paths=lambda: (),
        ),
    )
    results: list[Any] = []
    for call in calls:
        if "manifest" in call:
            (root / "app/manifest.json").write_text(call["manifest"])
        stdout, stderr = io.StringIO(), io.StringIO()
        args = dict(call["args"])
        if "json" in args:
            args["as_json" if call["command"] in ("status", "cleanup") else "json_output"] = (
                args.pop("json")
            )
        for key in (
            "source_db",
            "backup_dir",
            "current_app",
            "database_parent",
            "hermes_home",
            "codex_home",
            "claude_home",
            "snapshot",
            "destination_db",
            "environment_root",
            "credentials_env_file",
        ):
            if args.get(key) is not None:
                args[key] = Path(args[key])
        if "repository_roots" in args:
            args["repository_roots"] = tuple(map(Path, args["repository_roots"]))
        code = 0
        with (
            monkeypatch.context() as scoped,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            click.Context(cli.environment, obj=deps),
        ):
            scoped.setattr(time, "time", lambda: 1_800_000_000)
            if call.get("validator_failure"):

                def fail_validator(
                    *_args: Any,
                    category: str = call["validator_failure"],
                    **_kwargs: Any,
                ) -> None:
                    error = {
                        "os": OSError,
                        "value": ValueError,
                        "runtime": RuntimeError,
                        "unexpected": LookupError,
                    }[category]
                    raise error(
                        "synthetic unexpected validator failure"
                        if category == "unexpected"
                        else "app artifact digest does not match its manifest"
                    )

                scoped.setattr(cli, "validate_app_manifest", fail_validator)
            try:
                command = cli.environment.commands[call["command"]]
                assert command.callback is not None
                command.callback(**args)
            except click.ClickException as error:
                error.show(file=stderr)
                code = error.exit_code
            except Exception as error:
                results.append({"thrown": type(error).__name__})
                continue
        results.append(dict(exit_code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()))
    return results


def test_unit21_environment_command_function_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    node = shutil.which("node")
    assert node is not None
    environment = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        **create_synthetic_homes(tmp_path),
        "PLAN_TEST_MODE": "1",
    }
    with tempfile.TemporaryDirectory(
        prefix="p21-cli-", dir=Path(tempfile.gettempdir()).resolve()
    ) as temporary:
        root = Path(temporary)
        source = _seed(root)
        (root / "workspace").mkdir()
        (root / "config.yaml").write_text("{}\n")
        snapshot = _legacy_snapshot(source, root / "restore-snapshot")
        (root / "git").mkdir()
        with monkeypatch.context() as private_git:
            for key in list(os.environ):
                private_git.delenv(key)
            for key, value in environment.items():
                private_git.setenv(key, value)
            layout = _layout(root / "git")
        scratch = layout.folders[0] / "t_12345678"
        scratch.mkdir()
        (scratch / ".git").write_text("gitdir: missing\n")
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE tickets(id TEXT,stage TEXT,updated_at INTEGER)")
            connection.execute("INSERT INTO tickets VALUES('t_12345678','done',0)")
        for key, path in {
            "PLAN_CONFIG_PATH": root / "config.yaml",
            "PLAN_DB_PATH": source,
            "PLAN_BACKUP_DIR": root / "backups",
            "PLAN_LOGS_DIR": root / "logs",
            "PLAN_APP_ROOT": root / "app",
            "PLAN_TICKET_SCRATCH_ROOT": root / "git/panels",
            "PLAN_TICKET_SCRATCH_DATABASE": source,
            "PLAN_DISPATCHER_LOCK_PATH": root / "dispatcher.lock",
            "PLAN_SERVER_CONTROL_SOCKET": root / "control.sock",
            "PLAN_WORKSPACE_ROOT": root / "workspace",
            "PLAN_CREDENTIAL_KEY_PATH": root / "key",
        }.items():
            environment[key] = str(path)
        registry: dict[str, Any] = dict(
            kind="staging",
            environment_root=str(root / "estate"),
            instance_id=None,
            port=None,
            credentials_env_file=None,
            repository_roots=[],
            json=True,
        )
        current = dict(
            source_db=str(source),
            backup_dir=str(root / "command-backups"),
            current_app=str(root / "app"),
        )
        app = dict(
            format="panels-app-v1",
            app_sha="a" * 40,
            source_digest="b" * 64,
            artifact_digest="c" * 64,
        )
        (root / "corrupt.sqlite").write_bytes(b"not a SQLite database\n")
        calls = [
            dict(command="status", args=dict(json=True)),
            dict(command="status", args=dict(json=False)),
            dict(command="cleanup", args=dict(apply=False, json=True)),
            dict(command="cleanup", args=dict(apply=True, json=False)),
            dict(
                command="backup",
                args=dict(
                    source_db=str(source),
                    backup_dir=str(root / "command-backups"),
                    deployed_revision="synthetic",
                ),
            ),
            dict(command="backup-current", args=current, manifest=json.dumps(app)),
            dict(
                command="provision-skills",
                args=dict(
                    database_parent=str(source.parent),
                    **{
                        f"{name}_home": str(root / f"native-{name}")
                        for name in ("hermes", "codex", "claude")
                    },
                ),
            ),
            dict(
                command="restore",
                args=dict(
                    snapshot=str(snapshot),
                    destination_db=str(root / "restored.sqlite"),
                    live_stopped=True,
                ),
            ),
            dict(command="prepare", args=registry),
            dict(command="inspect", args={**registry, "json": False}),
            dict(command="reset", args=registry),
            dict(command="remove", args=registry),
            dict(command="reset", args=registry),
            *[
                dict(
                    command="backup-current",
                    args=current,
                    manifest=json.dumps(app),
                    validator_failure=category,
                )
                for category in ("os", "runtime", "unexpected")
            ],
            *[
                dict(command="backup-current", args=current, manifest=manifest)
                for manifest in (
                    "{",
                    "{}",
                    '{"app_sha": 1}',
                    json.dumps({"app_sha": "a" * 40 + "\n"}),
                    '{"app_sha": NaN}',
                    json.dumps({**app, "optional": float("nan")}),
                    "1",
                    # Python 3.13 changes trailing-comma diagnostics. This missing
                    # value pins the cursor after NaN on both 3.12 and 3.13.
                    '{"app_sha":NaN,"optional":}',
                )
            ],
            dict(
                command="backup",
                args=dict(
                    source_db=str(root / "corrupt.sqlite"),
                    backup_dir=str(root / "command-backups"),
                    deployed_revision="synthetic",
                ),
            ),
        ]
        with monkeypatch.context() as private:
            for key in list(os.environ):
                private.delenv(key)
            for key, value in environment.items():
                private.setenv(key, value)
            expected = _python(root, calls, monkeypatch)
            # Both functions see the same original app/status state and private paths.
            (root / "app/manifest.json").unlink()
            spec = root / "calls.json"
            spec.write_text(json.dumps(dict(root=str(root), calls=calls)))
            launcher = root / "probe.mjs"
            launcher.write_text(
                "import{readFileSync}from'node:fs';"
                "const{command_probe}=await import(process.argv[2]);"
                "const s=JSON.parse(readFileSync(process.argv[3],'utf8'));"
                "console.log(JSON.stringify(await command_probe(s.root,s.calls)));"
            )
            completed = subprocess.run(
                [
                    node,
                    str(launcher),
                    (ROOT / "server/environments/testing/commands.ts").as_uri(),
                    str(spec),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        assert completed.returncode == 0, completed.stderr
        actual = json.loads(completed.stdout)

        def stable(value: Any) -> str:
            return re.sub(
                r"snapshot-\d{8}T\d{6}Z-[0-9a-f]{8}",
                "snapshot-<generated>",
                json.dumps(value, sort_keys=True),
            )

        assert stable(actual["results"][:-1]) == stable(expected[:-1])
        assert expected[-1] == {"thrown": "DatabaseError"}
        assert actual["results"][-1] == {"thrown": "SqliteError"}
        assert len(actual["table"]) == 10
        assert all(row["path"] == ["environment", name] for name, row in actual["table"].items())
        defaults_context = click.Context(cli.environment)
        default_mismatches = []
        for name, row in actual["table"].items():
            parameters = {}
            for parameter in cli.environment.commands[name].params:
                assert isinstance(parameter, click.Option)
                for option_name in parameter.opts:
                    parameters[option_name] = parameter
            assert len(row["options"]) == len(cli.environment.commands[name].params)
            for option in row["options"]:
                parameter = parameters[option["name"]]
                assert option.get("required", False) == parameter.required
                # Click turns an unset multiple value into an empty tuple. Use
                # that public conversion instead of its internal UNSET marker.
                default = parameter.type_cast_value(
                    defaults_context, parameter.to_info_dict()["default"]
                )
                if isinstance(default, tuple):
                    default = list(default)
                if option.get("default") != default:
                    default_mismatches.append(
                        (name, option["name"], option.get("default"), default)
                    )
                assert option.get("multiple", False) == parameter.multiple
                expected_type = (
                    "choice"
                    if isinstance(parameter.type, click.Choice)
                    else "path"
                    if isinstance(parameter.type, click.Path)
                    else "boolean"
                    if parameter.is_flag
                    else "integer"
                    if parameter.type is click.INT
                    else "string"
                )
                assert option["type"] == expected_type
                if isinstance(parameter.type, click.Choice):
                    assert option["choices"] == list(parameter.type.choices)
                if isinstance(parameter.type, click.Path):
                    assert all(
                        option.get("path", {}).get(key, default) == getattr(parameter.type, key)
                        for key, default in (
                            ("exists", False),
                            ("file_okay", True),
                            ("dir_okay", True),
                        )
                    )
        assert not default_mismatches, default_mismatches
        assert actual["results"][5]["stderr"].startswith(
            "Warning: app artifact digest does not match its manifest."
        )
        assert [result.get("thrown") for result in actual["results"][-9:-1]] == [
            None,
            "KeyError",
            "TypeError",
            None,
            "TypeError",
            None,
            "TypeError",
            None,
        ]
    report = request.config.getoption("compare_report_dir", default=None)
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-environment-commands.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - started,
                    "boundary": (
                        "Plain Python callback/TS handler streams and error classification. "
                        "No parser/help, real app validator, dispatcher or host-service acceptance."
                    ),
                    "results": actual["results"],
                },
                indent=2,
            )
            + "\n"
        )
