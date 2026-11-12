"""Admission proofs use synthetic deployment paths and disposable subprocesses."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime import RuntimeRefusal, validate_environment

ROOT = Path(__file__).resolve().parents[2]


def test_pytest_entrypoints_replace_deployment_paths_before_collection(tmp_path: Path) -> None:
    deployment = tmp_path / "Deployments/Panels/current"
    deployment.mkdir(parents=True)
    database = deployment / "planner.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('untouched')")
    before = database.read_bytes()
    config = deployment / "config.yaml"
    config.write_text(f"db_path: {database}\nport: 8767\n")
    inherited = {
        "PLAN_DB_PATH": str(database),
        "PLAN_CONFIG_PATH": str(config),
        "PLAN_LOGS_DIR": str(deployment / "logs"),
        "PLAN_BACKUP_DIR": str(deployment / "backups"),
        "PLAN_CREDENTIAL_KEY_PATH": str(deployment / "credential.key"),
        "PLAN_HERMES_HOME": str(deployment / "hermes"),
        "PLAN_DISPATCHER_LOCK_PATH": str(deployment / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(deployment / "control.sock"),
        "PLAN_TICKET_SCRATCH_ROOT": str(deployment / "scratch"),
        "PLAN_TICKET_SCRATCH_DATABASE": str(database),
        "PLAN_WORKSPACE_ROOT": str(deployment),
        "PLAN_SERVER_URL": "http://127.0.0.1:8767",
    }
    probe = tmp_path / "test_collection_probe.py"
    probe.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from planner.core.config import load_config\n"
        "from planner.cli.http import _base_url\n"
        "config = load_config(os.environ['PLAN_CONFIG_PATH'])\n"
        f"keys = {tuple(inherited)!r}\n"
        "answer = {'environment': {key: os.environ[key] for key in keys if key in os.environ}, "
        "'database': config.db_path, "
        "'url': _base_url()}\n"
        "Path(os.environ['RUNTIME_PROBE_OUTPUT']).write_text(json.dumps(answer))\n"
        "def test_probe(): pass\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    environment.update(inherited)
    private_home = tmp_path / "home"
    private_home.mkdir()
    environment.update(HOME=str(private_home))
    roots = []
    for name, command in (
        ("direct", [sys.executable, "-m", "pytest"]),
        ("dev", [str(ROOT / "scripts/dev"), "pytest"]),
    ):
        output = tmp_path / f"{name}.json"
        result = subprocess.run(
            [*command, "-c", str(ROOT / "pyproject.toml"),
             "-p", "tests.conftest", "--collect-only", str(probe)],
            cwd=ROOT,
            env={**environment, "RUNTIME_PROBE_OUTPUT": str(output)},
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        answer = json.loads(output.read_text())
        actual = answer["environment"]
        for key, value in inherited.items():
            if key != "PLAN_SERVER_URL":
                assert actual.get(key) != value, key
                if key in actual:
                    assert "Deployments" not in Path(actual[key]).resolve().parts, key
        assert answer["database"] == actual["PLAN_DB_PATH"]
        assert answer["url"] == actual["PLAN_SERVER_URL"]
        assert answer["url"] != "http://127.0.0.1:8767"
        roots.append(actual["PLAN_DB_PATH"])
        # Both launch paths must leave the fake production database intact.
        assert database.read_bytes() == before
    assert roots[0] != roots[1]


def test_residual_guard_refuses_deployment_aliases_and_live_url(tmp_path: Path) -> None:
    deployment = tmp_path / "Deployments/Panels/current"
    deployment.mkdir(parents=True)
    database = deployment / "planner.db"
    database.write_text("protected sentinel")
    alias = tmp_path / "database-alias"
    alias.symlink_to(database)
    hardlink = tmp_path / "hardlink.db"
    hardlink.hardlink_to(database)
    for path in (database, alias, hardlink):
        with pytest.raises(RuntimeRefusal):
            validate_environment({"PLAN_UNRECOGNIZED_PATH": str(path)}, (database,))
    for url in ("http://127.0.0.1:8767", "http://localhost:8767", "http://[::1]:8767", "", " "):
        with pytest.raises(RuntimeRefusal):
            validate_environment({"PLAN_SERVER_URL": url})
    validate_environment({"PLAN_DB_PATH": str(tmp_path / "scratch.db"),
                          "PLAN_SERVER_URL": "http://127.0.0.1:0"})
    assert database.read_text() == "protected sentinel"


def test_xdist_workers_have_independent_runtime_storage(tmp_path: Path) -> None:
    outputs = tmp_path / "workers"
    outputs.mkdir()
    probe = tmp_path / "test_worker_probe.py"
    probe.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "def test_runtime():\n"
        "    answer = {key: os.environ[key] for key in "
        "('PLAN_DB_PATH', 'PLAN_SERVER_CONTROL_SOCKET', 'PLAN_CONFIG_PATH')}\n"
        "    output = Path(os.environ['RUNTIME_PROBE_OUTPUT']) / "
        "(os.environ['PYTEST_XDIST_WORKER'] + '.json')\n"
        "    output.write_text(json.dumps(answer))\n"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest",
         "-n", "2", "--dist=each", str(probe)],
        cwd=ROOT, env={**environment, "RUNTIME_PROBE_OUTPUT": str(outputs)},
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    first = json.loads((outputs / "gw0.json").read_text())
    second = json.loads((outputs / "gw1.json").read_text())
    assert first.keys() == second.keys()
    assert all(first[key] != second[key] for key in first)


def test_conftest_refuses_residual_path_before_test_import(tmp_path: Path) -> None:
    deployment = tmp_path / "Deployments/Panels/current"
    deployment.mkdir(parents=True)
    alias = tmp_path / "unexpected-runtime-alias"
    alias.symlink_to(deployment, target_is_directory=True)
    marker = tmp_path / "imported"
    probe = tmp_path / "test_forbidden_probe.py"
    probe.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    # Simulate a later default that accidentally reintroduces a deployment alias.
    # The admission check must independently reject it before test imports.
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import test_runtime\n"
        "original = test_runtime.environment_for\n"
        "def broken_default(root, environment):\n"
        "    answer = original(root, environment)\n"
        f"    answer['PLAN_UNKNOWN_PATH'] = {str(alias)!r}\n"
        "    return answer\n"
        "test_runtime.environment_for = broken_default\n"
        "import pytest\n"
        "raise SystemExit(pytest.main(['-p', 'tests.conftest', '--collect-only', "
        f"{str(probe)!r}]))\n"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=environment,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert "refused deployment path" in result.stdout + result.stderr
    assert not marker.exists()


def test_existing_config_alias_is_refused_before_scratch_writes(tmp_path: Path) -> None:
    from test_runtime import environment_for

    deployment = tmp_path / "Deployments/Panels/current"
    deployment.mkdir(parents=True)
    sentinel = deployment / "config.yaml"
    sentinel.write_text("db_path: protected-sentinel\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "test-config.yaml").symlink_to(sentinel)
    with pytest.raises(RuntimeRefusal, match="deployment path"):
        environment_for(scratch, {})
    assert sentinel.read_text() == "db_path: protected-sentinel\n"
    assert list(scratch.iterdir()) == [scratch / "test-config.yaml"]


def test_runtime_hops_retain_original_and_current_protected_roots(tmp_path: Path) -> None:
    from test_runtime import environment_for, evidence_environments
    from tests.support.skill_tree_protection.inventory import capture_launch_inventory

    home = tmp_path / "home"
    home.mkdir()
    original_root = home / "original"
    first_root = tmp_path / "launcher"
    second_root = tmp_path / "pytest"
    original = {
        "HOME": str(home),
        "PLAN_DB_PATH": "~/original/planning.db",
        "PLAN_HERMES_HOME": "~/original/hermes",
        "CODEX_HOME": "~/selected-codex",
    }
    first = environment_for(first_root, original)
    # A nested launcher changes HOME. Earlier tilde paths retain their meaning.
    changed_home = tmp_path / "other-home"
    changed_home.mkdir()
    first["HOME"] = str(changed_home)
    second = environment_for(second_root, first)
    inventory, _ = capture_launch_inventory(
        second, tmp_path, account_home=home,
        extra_environments=evidence_environments(second),
    )
    for root in (original_root, first_root, second_root):
        assert str(root / "skills") in inventory.roots
        assert str(root / "hermes/skills") in inventory.roots
    assert str(home / "selected-codex/skills") in inventory.roots
    assert str(changed_home / "selected-codex/skills") in inventory.roots
