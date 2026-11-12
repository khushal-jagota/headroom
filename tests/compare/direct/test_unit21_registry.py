"""One connected lifecycle sequence proves TS registry and fictional writer parity.

The existing Python unit fixtures cover individual validators. This sequence adds the
cross-language boundary: TS-created state, atomic replacement and an actual private
flock must preserve the same public contract without a Python runtime in TS.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes

from planner.environments import materialize
from planner.environments.logic.credentials import (
    parse_environment_file,
    validate_environment_values,
)
from planner.environments.logic.launch_env import build_test_environment_run_env
from planner.environments.logic.validation import validate_repository_roots
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease
from planner.skill_sources import provision_native_backend_skills

ROOT = Path(__file__).resolve().parents[3]


def _error(action: Any) -> str:
    try:
        action()
    except Exception as error:
        return str(error)
    return "NO ERROR"


def _python(
    root: Path, repository: Path, credentials: Path, output: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    invalid_repository = root.parent / "invalid-repository"
    (invalid_repository / "gitdirX").mkdir(parents=True)
    (invalid_repository / ".git").write_text("gitdirX")
    (invalid_repository / "gitdirX/HEAD").write_text("ref: refs/heads/main\n")
    invalid_git_marker = _error(
        lambda: validate_repository_roots((invalid_repository,), (invalid_repository,))
    )
    (invalid_repository / ".git").write_text("\ufeffgitdir: gitdirX\n")
    bom_git_marker = _error(
        lambda: validate_repository_roots((invalid_repository,), (invalid_repository,))
    )
    bom_credentials_path = root.parent / "bom.env"
    bom_credentials_path.write_text("\ufeffOPENAI_API_KEY=fake\n")
    bom_credentials = _error(lambda: parse_environment_file(bom_credentials_path, kind="staging"))
    options: dict[str, Any] = dict(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
        credentials_env_file=credentials,
    )
    before = materialize.inspect_environment_instance(**options)
    prepared = materialize.prepare_environment_instance(**options, now=1_800_000_000)
    disk = (prepared.instance_root / "manifest.json").read_text()
    tampered = json.loads(disk)
    tampered["db_path"] = str(output / "outside.db")
    (prepared.instance_root / "manifest.json").write_text(json.dumps(tampered))
    try:
        invalid_manifest = _error(lambda: materialize.inspect_environment_instance(**options))
    finally:
        (prepared.instance_root / "manifest.json").write_text(disk)
    forbidden_credentials = _error(
        lambda: validate_environment_values({"PLAN_DB_PATH": "/forbidden"}, kind="staging")
    )
    idempotent = materialize.prepare_environment_instance(**options, now=1_800_000_001)
    shutil.copytree(prepared.db_path.parent, output / "initial")
    original = prepared.db_path.read_bytes()

    def fail_fixture(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected fixture failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(materialize, "build_fake_environment_database", fail_fixture)
        fail_build = _error(lambda: materialize.reset_environment_instance(**options))
    unchanged_build = original == prepared.db_path.read_bytes()
    real_replace = os.replace
    injected = False

    def fail_rename(source: Path, destination: Path) -> None:
        nonlocal injected
        if (
            not injected
            and destination == prepared.db_path.parent
            and "-backup-" not in str(source)
        ):
            injected = True
            raise RuntimeError("injected rename failure")
        real_replace(source, destination)

    def copy_fixture(path: Path, **_kwargs: Any) -> None:
        shutil.copytree(prepared.db_path.parent, path.parent)

    with monkeypatch.context() as scoped:
        scoped.setattr(materialize, "build_fake_environment_database", copy_fixture)
        scoped.setattr(os, "replace", fail_rename)
        failed_rename = _error(lambda: materialize.reset_environment_instance(**options))
    unchanged_rename = original == prepared.db_path.read_bytes()
    lease = PortScopedServerLifecycleLease(prepared.instance_root / "run/server-lifecycle.lock", 0)
    lease.acquire()
    try:
        running_reset = _error(lambda: materialize.reset_environment_instance(**options))
        running_remove = _error(lambda: materialize.remove_environment_instance(**options))
    finally:
        lease.release()
    prepared.db_path.unlink()
    missing_database_status = materialize._environment_lifecycle_lease_is_held(prepared)
    reset = materialize.reset_environment_instance(**options, now=1_800_000_002)
    shutil.copytree(reset.db_path.parent, output / "reset")
    run_env = build_test_environment_run_env(
        materialize._instance_from_manifest(reset),
        credentials=parse_environment_file(credentials, kind="staging"),
        ambient={
            "HOME": "/synthetic/home",
            "PATH": "/synthetic/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "PLAN_DB_PATH": "/forbidden",
            "SECRET": "forbidden",
        },
        hermes_python=Path("/synthetic/python"),
        runtime_port=32123,
    )
    provisioned = []
    for name in ("hermes", "codex", "claude"):
        home = output / name
        provision_native_backend_skills(home, reset.db_path.parent)
        provisioned.append(
            {
                path.name: hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest()
                for path in sorted((home / "skills").iterdir())
            }
        )
    reset.db_path.unlink()
    materialize.remove_environment_instance(**options)
    return dict(
        before=materialize.manifest_to_json_dict(before),
        prepared=materialize.manifest_to_json_dict(prepared),
        idempotent=materialize.manifest_to_json_dict(idempotent),
        reset=materialize.manifest_to_json_dict(reset),
        disk=disk,
        restored_registry=True,
        missing_database_status=missing_database_status,
        invalid_git_marker=invalid_git_marker,
        bom_git_marker=bom_git_marker,
        bom_credentials=bom_credentials,
        invalid_manifest=invalid_manifest,
        forbidden_credentials=forbidden_credentials,
        fail_fixture=fail_build,
        fixture_unchanged=unchanged_build,
        fail_rename=failed_rename,
        rename_unchanged=unchanged_rename,
        injected=injected,
        running_reset=running_reset,
        running_remove=running_remove,
        run_env=run_env,
        provisioned=provisioned,
        removed=not prepared.instance_root.exists(),
    )


def _state(root: Path) -> dict[str, Any]:
    """Compare complete stored domain records after mapping only opaque generated IDs."""
    with closing(sqlite3.connect(root / "planner.db")) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            name: [dict(row) for row in connection.execute(f"SELECT * FROM {name} ORDER BY title")]
            for name in ("managers", "tickets")
        }
        ids = {
            row["id"]: f"{table}:{row['title']}" for table, rows in tables.items() for row in rows
        }
        tables["projects"] = [
            dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY id")
        ]
        tables["navigation"] = [
            dict(row)
            for row in connection.execute(
                "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
            )
        ]
        assert connection.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 7
        assert connection.execute("SELECT count(*) FROM navigation_dirty").fetchone()[0] == 0

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                for identity, label in ids.items():
                    value = value.replace(identity, label)
                return value
            if isinstance(parsed, (dict, list)):
                return normalize(parsed)
            for identity, label in ids.items():
                value = value.replace(identity, label)
            return value
        return value

    files = {
        str(path.relative_to(root / "files")).replace(
            next((identity for identity in ids if identity in str(path)), "IMPOSSIBLE"),
            next((label for identity, label in ids.items() if identity in str(path)), "IMPOSSIBLE"),
        ): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "files").rglob("*")
        if path.is_file()
    }
    result = normalize(tables)
    result["navigation"] = sorted(result["navigation"], key=lambda row: (row["kind"], row["id"]))
    return {"tables": result, "files": files}


def test_unit21_registry_fixture_and_lifecycle_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    selected_node = shutil.which("node")
    assert selected_node is not None, "selected Node runtime is required"
    homes = create_synthetic_homes(tmp_path)
    environment = {"PATH": str(Path(selected_node).parent) + os.pathsep + os.defpath, **homes}
    for name, relative in {
        "PLAN_DB_PATH": "private.sqlite",
        "PLAN_CONFIG_PATH": "config.json",
        "PLAN_DATA_DIR": "data",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_DISPATCHER_LOCK_PATH": "dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": "control.sock",
        "PLAN_APP_ROOT": "app",
        "PLAN_KEY_PATH": "key",
    }.items():
        environment[name] = str(tmp_path / relative)
    Path(environment["PLAN_WORKSPACE_ROOT"]).mkdir()
    environment["PLAN_TEST_MODE"] = "1"
    with monkeypatch.context() as private:
        for name in list(os.environ):
            private.delenv(name)
        for name, value in environment.items():
            private.setenv(name, value)
        # Short owned roots fit Python's portable AF_UNIX contract, including on Mac.
        with tempfile.TemporaryDirectory(
            prefix="p21-", dir=Path(tempfile.gettempdir()).resolve()
        ) as short:
            root = Path(short)
            repository = root / "repository"
            (repository / ".git/objects").mkdir(parents=True)
            (repository / ".git/HEAD").write_text("ref: refs/heads/main\n")
            credentials = root / "credentials.env"
            credentials.write_text("# synthetic credentials\nOPENAI_API_KEY='fake=value'\n")
            estate = root / "estate"
            python_output = root / "python"
            python = _python(estate, repository, credentials, python_output, monkeypatch)
            shutil.rmtree(estate)
            typescript_output = root / "typescript"
            spec = root / "request.json"
            spec.write_text(
                json.dumps(
                    dict(
                        root=str(estate),
                        repository=str(repository),
                        credentials=str(credentials),
                        output=str(typescript_output),
                    )
                )
            )
            launcher = root / "probe.mjs"
            launcher.write_text(
                "import{readFileSync}from'node:fs';"
                "const{registry_probe}=await import(process.argv[2]);"
                "console.log(JSON.stringify(await registry_probe("
                "JSON.parse(readFileSync(process.argv[3],'utf8')))));"
            )
            result = subprocess.run(
                [
                    selected_node,
                    str(launcher),
                    (ROOT / "server/environments/registry/testing/compare.ts").as_uri(),
                    str(spec),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            actual = json.loads(result.stdout)
            server_owner = actual.pop("server_owner")
            assert server_owner == {
                "running": True,
                "reset": "environment instance is running",
                "remove": "environment instance is running",
            }
            assert actual == python
            assert (
                actual["fixture_unchanged"]
                and actual["rename_unchanged"]
                and actual["removed"]
                and actual["restored_registry"]
            )
            assert (
                actual["running_reset"]
                == actual["running_remove"]
                == "environment instance is running"
            )
            for phase in ("initial", "reset"):
                assert _state(typescript_output / phase) == _state(python_output / phase)
    report_dir = request.config.getoption("compare_report_dir", default=None)
    if report_dir:
        destination = Path(report_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-registry.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - started,
                    "boundary": (
                        "TS-only fresh fictional writers, manifest bytes, private flock, "
                        "failed swap rollback, reset/remove and scrubbed launch environment. "
                        "One private actual TS server proves DB-owner admission. No browser."
                    ),
                    "result": actual,
                    "server_owner": server_owner,
                },
                indent=2,
            )
            + "\n"
        )
