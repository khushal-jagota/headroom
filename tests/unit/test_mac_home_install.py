"""Protect the release home from stale SHA bindings and foreign launchd control."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.mac_home_install import install_home

from planner.environments.app import _write_app_entrypoints
from planner.platform_runtime.darwin.adapter import DarwinPlatformAdapter
from planner.platform_runtime.linux import update_installer as installer


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Reuse setup generation with a cheap archive/admission seam. Native bytes and
    # launchd custody need the separate real Mac proof, not a mocked browser host.
    def extract(archive: Path, app: Path, digest: str, sha: str) -> SimpleNamespace:
        (app / "bin").mkdir(parents=True)
        return SimpleNamespace(runtime_contract=SimpleNamespace(
            system="darwin", architecture="arm64", role="home"))

    monkeypatch.setenv("PATH", "/build/icu/bin:/production/executor/bin:/usr/bin:/bin")
    monkeypatch.setattr("scripts.mac_home_install.extract_app_archive", extract)
    monkeypatch.setattr("planner.environments.runtime_admission.admit_runtime", lambda *args: None)
    root = tmp_path / "release home"
    install_home(root, tmp_path / "archive", "a" * 64, "a" * 40,
                 "com.panels.proof", 9876, Path(sys.executable), isolated_providers=True)
    return root


def service(root: Path) -> installer.MacService:
    return installer.MacService(root, f"gui/{os.getuid()}/com.panels.proof")


def loaded(root: Path, label: str, pid: int) -> str:
    return (
        f"path = {root / 'agents' / (label + '.plist')}\n"
        f"program = {root / 'app/bin/panels'}\nworking directory = {root}\npid = {pid}\n"
        + "arguments = {\n" + "\n".join(plistlib.loads(
            (root / "agents" / f"{label}.plist").read_bytes())["ProgramArguments"])
        + "\n}\nenvironment = {\n"
        + f"PLAN_DB_PATH => {root / 'data/planner.db'}\n"
        + f"PLAN_CONFIG_PATH => {root / 'data/config.yaml'}\n}}\n"
    )


def test_release_jobs_read_the_current_app_and_keep_provider_guard(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Source setup tests pin a source SHA; they do not protect installed jobs from
    # retaining that identity after an app replacement.
    identity = service(home)
    main = identity.plist(identity.label)
    backup = identity.plist(identity.label + ".backup")
    assert main["ProgramArguments"] == [str(home / "app/bin/panels"), "serve"]
    assert backup["ProgramArguments"][-2:] == ["--current-app", str(home / "app")]
    assert "PLAN_APP_SHA" not in main["EnvironmentVariables"]
    search_path = main["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert search_path[:2] == [
        str(home / "app/agent_backends/node_modules/.bin"), str(home / "app/.venv/bin"),
    ]
    assert set(search_path) >= {
        str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    }
    assert "/build/icu/bin" not in search_path
    assert "/production/executor/bin" not in search_path
    assert backup["EnvironmentVariables"] == main["EnvironmentVariables"]
    assert "--deployed-revision" not in backup["ProgramArguments"]
    config = json.loads((home / "data/config.yaml").read_text())
    assert config["provision_account_skills"] is False
    assert main["EnvironmentVariables"]["CODEX_HOME"] == str(home / "providers/codex")

    guards: list[Path] = []

    def occupied(authority: Path, *args: object, **kwargs: object) -> str:
        guards.append(authority)
        return "occupied account skills"

    monkeypatch.setattr("scripts.mac_home_install.account_skill_provisioning_conflict", occupied)
    codex = tmp_path / "canonical codex"
    monkeypatch.setenv("CODEX_HOME", str(codex))
    provider_bin = tmp_path / "provider/bin"
    provider_bin.mkdir(parents=True)
    executable = provider_bin / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(provider_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PLAN_CLAUDE_EXECUTABLE", str(provider_bin / "custom-claude"))
    normal = tmp_path / "normal home"
    install_home(normal, tmp_path / "archive", "a" * 64, "a" * 40,
                 "com.panels.normal", 9877, Path(sys.executable))
    configured = json.loads((normal / "data/config.yaml").read_text())
    normal_job = plistlib.loads((normal / "agents/com.panels.normal.plist").read_bytes())
    assert configured["provision_account_skills"] is True
    assert normal_job["EnvironmentVariables"]["CODEX_HOME"] == str(codex)
    assert normal_job["EnvironmentVariables"]["PLAN_CLAUDE_EXECUTABLE"] == str(
        provider_bin / "custom-claude"
    )
    assert str(provider_bin) in normal_job["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert "/build/icu/bin" not in normal_job["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert guards == [normal / "data"]
    assert "Warning: occupied account skills" in capsys.readouterr().out


def test_release_cli_and_application_leave_no_bytecode_in_sealed_app(
    home: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    # A plist assertion misses -I ignoring PYTHON* and a supervisor child losing
    # its parent's -B flag. Exercise both real interpreter launches, not launchd.
    app = home / "app"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(app / ".venv")],
                   check=True, capture_output=True)
    _write_app_entrypoints(app, app_sha="a" * 40)
    site = next((app / ".venv/lib").glob("python*/site-packages"))
    package = site / "planner"
    (package / "server_lifecycle").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cache_probe.py").write_text("value = 23\n")
    probe = "import sys; from planner import cache_probe; print(sys.dont_write_bytecode)\n"
    (package / "__main__.py").write_text(probe)
    (package / "server_lifecycle/__init__.py").write_text("")
    (package / "server_lifecycle/application.py").write_text(probe)
    (package / "environments").mkdir()
    (package / "environments/__init__.py").write_text("")
    (package / "environments/app_launcher.py").write_text(probe)
    environment = service(home).plist("com.panels.proof")["EnvironmentVariables"]
    before = {path.relative_to(app) for path in app.rglob("*")}
    cli = subprocess.run([str(app / "bin/panels"), "serve"], cwd=home,
                         env=environment, capture_output=True, text=True, check=True)
    assert cli.stdout == "True\n"
    smoke = subprocess.run([str(app / "bin/panels-launcher"), "serve"], cwd=home,
                           env={key: value for key, value in environment.items()
                                if key != "PYTHONDONTWRITEBYTECODE"},
                           capture_output=True, text=True, check=True)
    assert smoke.stdout == "True\n"
    child = DarwinPlatformAdapter().spawn_application_child(
        str(app / ".venv/bin/python"), home, environment, [],
    )
    assert child.wait(timeout=10) == 0
    assert capfd.readouterr().out == "True\n"
    assert {path.relative_to(app) for path in app.rglob("*")} == before
    assert not list(package.rglob("*.pyc"))


def test_setup_refuses_an_interpreter_inside_the_installation_before_writes(
    tmp_path: Path,
) -> None:
    # A host interpreter alias into the future app defeats independent custody.
    root = tmp_path / "home"
    with pytest.raises(ValueError, match="external Python 3.11 or newer"):
        install_home(root, tmp_path / "archive", "a" * 64, "a" * 40,
                     "com.panels.proof", 9876, root / "app/.venv/bin/python")
    assert not root.exists()


def test_foreign_loaded_job_is_refused_before_any_bootout(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = service(home)
    calls = []
    monkeypatch.setattr(installer, "mac_processes", lambda *args: {})
    monkeypatch.setattr(identity, "job", lambda label, **kwargs: loaded(home, label, 123).replace(
        str(home / "app/bin/panels"), "/another/home/bin/panels"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="different program"):
        identity.stop()
    assert calls == []


def test_stop_waits_for_birth_identity_even_when_a_child_gets_a_new_parent(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Linux systemctl fixtures cannot prove this native boundary. A reparented
    # child with the same birth identity must block replacement, even if its UID changes.
    identity = service(home)
    label = identity.label
    jobs = {label: loaded(home, label, 123),
            label + ".backup": loaded(home, label + ".backup", 125)}
    monkeypatch.setattr(identity, "job", lambda name, **kwargs: jobs.get(name))
    monkeypatch.setattr(installer, "mac_processes", lambda *args: {
        123: (1, os.getuid(), 10, 20), 124: (123, os.getuid(), 11, 21),
        125: (1, os.getuid(), 12, 22),
    })
    calls = []

    def run(command: list[str], **kwargs: object) -> None:
        calls.append(command)
        jobs.pop(command[-1].split("/")[-1])

    monkeypatch.setattr(subprocess, "run", run)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(installer, "mac_process_identity", lambda pid: (
        (1, os.getuid() + 1, 11, 21) if pid == 124 and not sleeps else None))
    identity.stop()
    assert len(sleeps) == 1
    assert [command[-1] for command in calls] == [
        f"gui/{os.getuid()}/{label}.backup", f"gui/{os.getuid()}/{label}",
    ]
    assert identity.stopped is True


def test_unreadable_supervisor_identity_is_not_absence(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = service(home)
    monkeypatch.setattr(identity, "job", lambda name, **kwargs: loaded(home, name, 123))

    def unreadable(pid: int) -> None:
        raise OSError("libproc permission denied")

    monkeypatch.setattr(installer, "mac_process_identity", unreadable)
    with pytest.raises(OSError, match="permission denied"):
        identity.validate_running_home(parent=123)


def test_loaded_foreign_record_is_refused_even_when_the_plist_matches(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plist edit does not change an already-loaded job's environment.
    identity = service(home)
    text = loaded(home, identity.label, 123).replace(
        f"PLAN_DB_PATH => {home / 'data/planner.db'}", "PLAN_DB_PATH => /another/record.db")
    monkeypatch.setattr(identity, "job", lambda name, **kwargs: text)
    with pytest.raises(ValueError, match="different PLAN_DB_PATH"):
        identity.authenticate(identity.label, required=True)


def prepared_attempt(home: Path) -> argparse.Namespace:
    (home / "app/manifest.json").write_text(json.dumps({"app_sha": "a" * 40}))
    staged = home / (".app-next-" + "b" * 40)
    staged.mkdir()
    (staged / "manifest.json").write_text(json.dumps({"app_sha": "b" * 40}))
    (home / "data/update-attempt.json").write_text(json.dumps({
        "attempt_id": "proof", "state": "installing", "from_sha": "a" * 40,
        "to_sha": "b" * 40, "backup": str(home / "data/backups/snapshot"),
    }))
    return argparse.Namespace(current_root=home, attempt_id="proof",
                           service=f"gui/{os.getuid()}/com.panels.proof",
                           health_url="unused", health_timeout_seconds=1)


def test_backup_restart_failure_after_health_never_restores_the_record(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Existing systemctl fixtures have no separate backup job. A backup failure
    # must not turn an accepted candidate into destructive snapshot rollback.
    arguments = prepared_attempt(home)
    database = home / "data/planner.db"

    def start() -> None:
        database.write_text("candidate committed work")

    def backup() -> None:
        raise OSError("backup bootstrap refused")

    native = SimpleNamespace(validate_running_home=lambda: None, stop=lambda: None,
                             start=start, resume_backup=backup)
    monkeypatch.setattr(installer, "MacService", lambda *args: native)
    monkeypatch.setattr(installer, "sys", SimpleNamespace(platform="darwin", stderr=sys.stderr))
    monkeypatch.setattr(installer, "healthy", lambda *args: True)
    monkeypatch.setattr(installer.Installation, "capture_interruptions", lambda self: None)
    assert installer.install(arguments) == 0
    assert database.read_text() == "candidate committed work"
    record = json.loads((home / "data/update-attempt.json").read_text())
    assert record["state"] == "done"
    assert "backup job did not restart" in record["detail"]
    assert "rollback_database_restored" not in record["steps"]


def test_partial_home_stop_refusal_restores_the_backup_without_another_home(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The backup can stop first while launchd refuses the home stop. Preserve
    # that backup registration without a second home or app/data replacement.
    arguments = prepared_attempt(home)
    identity = service(home)
    label = identity.label
    jobs = {label: loaded(home, label, 123),
            label + ".backup": loaded(home, label + ".backup", 125)}
    monkeypatch.setattr(identity, "job", lambda name, **kwargs: jobs.get(name))
    inventory = {123: (1, os.getuid(), 10, 20), 125: (1, os.getuid(), 12, 22)}
    monkeypatch.setattr(installer, "mac_processes", lambda *args: inventory)
    monkeypatch.setattr(installer, "mac_process_identity", lambda pid: inventory.get(pid))
    monkeypatch.setattr(installer, "MacService", lambda *args: identity)
    monkeypatch.setattr(installer, "sys", SimpleNamespace(platform="darwin", stderr=sys.stderr))
    monkeypatch.setattr(installer, "healthy", lambda *args: True)
    calls = []

    def run(command: list[str], **kwargs: object) -> None:
        calls.append(command)
        if command[1] == "bootout":
            if command[-1].endswith(".backup"):
                jobs.pop(label + ".backup")
            else:
                raise subprocess.CalledProcessError(1, command)
        else:
            assert command[1] == "bootstrap"
            assert command[-1] == str(home / "agents" / (label + ".backup.plist"))
            jobs[label + ".backup"] = loaded(home, label + ".backup", 125)

    monkeypatch.setattr(subprocess, "run", run)
    assert installer.install(arguments) == 1
    assert label + ".backup" in jobs
    assert len(calls) == 3
    assert json.loads((home / "app/manifest.json").read_text())["app_sha"] == "a" * 40
    record = json.loads((home / "data/update-attempt.json").read_text())
    assert record["state"] == "failed"
    assert "backup job was restored" in record["detail"]
