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
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pytest
from tests.compare.direct.test_unit21_status import _seed
from tests.compare.direct.unit20_cli import _python_library
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
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    with (
        tempfile.TemporaryDirectory(
            prefix="p21-cli-", dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary,
        ExitStack() as cleanup,
    ):
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
        denied = root / "denied'app-\té"
        denied.mkdir()
        (denied / "manifest.json").write_text(json.dumps(app))
        cleanup.callback(denied.chmod, 0o700)
        denied.chmod(0)
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
            dict(command="backup-current", args={**current, "current_app": denied.name}),
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
            private.chdir(root)
            for key in list(os.environ):
                private.delenv(key)
            for key, value in environment.items():
                private.setenv(key, value)
            expected = _python(root, calls, monkeypatch)
            # Both functions see the same original app/status state and private paths.
            (root / "app/manifest.json").unlink()
            bindings = json.loads(
                (Path(__file__).with_name("unit20_operators.json"))
                .read_text()
                .replace("@root@", str(root))
            )
            routing: list[dict[str, Any]] = []
            aliases = {"as_json": "json", "json_output": "json", "db_path": "db"}
            from planner.cli.main import main

            for case in bindings:
                command: Any = main
                for name in case["path"]:
                    command = command.commands[name]

                def record(_name: str = case["name"], **values: Any) -> None:
                    routing.append(
                        {"name": _name, "args": {aliases.get(k, k): v for k, v in values.items()}}
                    )

                with monkeypatch.context() as callback:
                    callback.setattr(command, "callback", record)
                    parsed = _python_library(case["argv"], b"", environment)
                assert parsed["exit_code"] == 0, parsed
            spec = root / "calls.json"
            spec.write_text(json.dumps(dict(root=str(root), calls=calls, bindings=bindings)))
            launcher = root / "probe.mjs"
            launcher.write_text(
                "import{readFileSync}from'node:fs';"
                "import{createRequire}from'node:module';"
                "const require=createRequire(import.meta.url);"
                "const before=new Set(Object.keys(require.cache));"
                "const signals=['SIGINT','SIGTERM'].map(s=>process.listenerCount(s));"
                "const began=performance.now();"
                "await import(new URL('../commands.ts',process.argv[2]));"
                "const table_seconds=(performance.now()-began)/1000,second=performance.now();"
                "await import(new URL('../operator_dependencies.ts',process.argv[2]));"
                "const cold={table_seconds,factory_seconds:(performance.now()-second)/1000,"
                "new_require_modules:Object.keys(require.cache).filter(p=>!before.has(p)),"
                "signals_unchanged:['SIGINT','SIGTERM'].every((s,i)=>process.listenerCount(s)===signals[i])};"
                "const s=JSON.parse(readFileSync(process.argv[3],'utf8'));"
                "const{default:koffi}=await import("
                "new URL('../../node_modules/koffi/index.js',process.argv[2]));"
                "const load=koffi.load;let injected=0,acquire_calls=0,actions=0,registry,result,injecting=false;"
                "koffi.load=(...args)=>new Proxy(load(...args),{get(lib,key){"
                "if(key!=='func')return Reflect.get(lib,key);"
                "return(...signature)=>{const fn=lib.func(...signature);"
                "if(signature[0]!=='int flock(int fd, int operation)')return fn;"
                "return(fd,operation)=>{if(injecting&&operation===2){acquire_calls++;"
                "if(!injected){injected++;koffi.errno(4);return -1;}}"
                "return fn(fd,operation);};};}});"
                "try{const{command_probe}=await import(process.argv[2]);"
                "result=await command_probe(s.root,s.calls,s.bindings);"
                "registry=await import(new URL('../registry/store.ts',process.argv[2]));}"
                "finally{koffi.load=load;}"
                "injecting=true;"
                "await registry.with_registry_lock(s.root+'/eintr-registry',async()=>{actions++;});"
                "console.log(JSON.stringify({...result,cold,"
                "interrupted_lock:{injected,acquire_calls,actions}}));"
            )
            completed = subprocess.run(
                [
                    node,
                    str(launcher),
                    (ROOT / "server/environments/testing/commands.ts").as_uri(),
                    str(spec),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        assert completed.returncode == 0, completed.stderr
        actual = json.loads(completed.stdout)
        assert actual["cold"]["new_require_modules"] == []
        assert actual["cold"]["signals_unchanged"]
        assert actual["interrupted_lock"] == dict(injected=1, acquire_calls=2, actions=1)

        def stable(value: Any) -> str:
            return re.sub(
                r"snapshot-\d{8}T\d{6}Z-[0-9a-f]{8}",
                "snapshot-<generated>",
                json.dumps(value, sort_keys=True),
            )

        assert stable(actual["results"][:-1]) == stable(expected[:-1])
        assert expected[-1] == {"thrown": "DatabaseError"}
        assert actual["results"][-1] == {"thrown": "SqliteError"}
        assert actual["routing"] == json.loads(json.dumps(routing, default=str))
        operators = actual["operators"]
        assert operators["literal_manifest_path"] == str(Path("link/../app") / "manifest.json")
        assert operators["invocation_home"] == str(root)
        assert operators["unsafe_paths_refused"] == 4
        snapshot = operators["snapshot_only_refusals"]
        assert snapshot["actual_test_mode"] is False and snapshot["snapshot_test_mode"] is True
        assert snapshot["home"] == str(root / "snapshot-home")
        assert snapshot["refused"] == [
            "environment-tree",
            "provision-home",
            "cleanup-legacy-home",
        ]
        assert snapshot["admissions"]
        assert any(
            row["path"] == str(root / "snapshot-home/hermes") for row in snapshot["admissions"]
        )
        assert all(
            row["canonical"] == str(Path(row["path"]).resolve()) for row in snapshot["admissions"]
        )
        assert operators["interrupted_status"]["ready"]
        assert operators["interrupted_status"]["actual_closed"]
        assert operators["interrupted_status"]["cleanup_failure_retained"]
        assert operators["interrupted_status"]["later_spawns"] == 0
        assert actual["scratch_owned"]["actual_children"] > 0
        assert actual["scratch_owned"]["all_closed"]
        for name in ("app-build", "run", "serve", "restart"):
            assert operators["deferred"][name] == dict(
                exit_code=1,
                stdout="",
                failure_message=f"The {name} command is not yet available in this build.",
                stderr_prefix="",
            )
        message = "The TypeScript runtime cannot enumerate Promise await chains."
        assert operators["diagnostic"] == [
            dict(
                exit_code=1,
                stdout="",
                stderr="error: "
                + message
                + "\nThis diagnostic is unavailable in the TypeScript server.\n",
            ),
            dict(
                exit_code=1,
                stdout="",
                stderr=json.dumps(
                    {"error": {"code": "unsupported", "message": message, "detail": {}}}
                )
                + "\n",
            ),
        ]
        assert operators["watchdog"] == dict(exit_code=0, stdout="recorded watchdog\n", stderr="")
        assert actual["results"][5]["stderr"].startswith(
            "Warning: app artifact digest does not match its manifest."
        )
        assert expected[16]["stderr"] == (
            f"Error: [Errno 13] Permission denied: {str(Path(denied.name) / 'manifest.json')!r}\n"
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
                        "The parser and binder use recorded handlers with real factory selection. "
                        "No real app validator or host-service acceptance."
                    ),
                    "routing": {
                        "python": json.loads(json.dumps(routing, default=str)),
                        "typescript": actual["routing"],
                    },
                    "results": actual["results"],
                    "raw_results": actual["raw_results"],
                    "operators": actual["operators"],
                    "scratch_owned": actual["scratch_owned"],
                    "cold_imports": actual["cold"],
                    "interrupted_lock": actual["interrupted_lock"],
                },
                indent=2,
            )
            + "\n"
        )
