from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.environments import cli
from planner.environments.deployment import SubprocessServiceController

TARGET = "/user.slice/user-1000.slice/user@1000.service/app.slice/panels-live.service"


def systemd_context(monkeypatch: pytest.MonkeyPatch, group: str) -> None:
    read_text = Path.read_text

    def read(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/cgroup"):
            return f"0::{group}\n"
        return read_text(path)  # only the proc read has no encoding arguments

    def inspect(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == [
            "systemctl", "--user", "show", "panels-alias.service",
            "--property=ControlGroup", "--value",
        ]
        return subprocess.CompletedProcess(command, 0, TARGET + "\n")

    monkeypatch.setattr("planner.environments.service_execution.platform", "linux")
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(subprocess, "run", inspect)


@pytest.mark.parametrize("group", [TARGET, TARGET + "/child"])
def test_self_stop_refusal_precedes_lifecycle_and_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str,
) -> None:
    """A detached shell and descendants still share the unit killed by a stop."""
    systemd_context(monkeypatch, group)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence = tmp_path / "proof.json"
    evidence.write_text("{}")
    lifecycle = tmp_path / "lifecycle.json"
    lifecycle.write_text('{"phase":"preparing"}')

    def unexpected(**kwargs: object) -> None:
        pytest.fail("refusal must precede deployment")

    monkeypatch.setattr(cli, "deploy_app", unexpected)
    result = CliRunner().invoke(cli.environment, [
        "app-deploy", "--candidate-app", str(candidate),
        "--current-root", str(tmp_path / "current"),
        "--source-db", str(tmp_path / "planner.db"),
        "--backup-dir", str(tmp_path / "backups"),
        "--health-url", "http://unused/api/health",
        "--service-manager", "systemctl", "--service-name", "panels-alias.service",
        "--gate-evidence", str(evidence), "--lifecycle-path", str(lifecycle),
        "--deployment-id", "attempt",
    ])
    assert result.exit_code == 1
    assert "Installer is inside" in result.output
    assert lifecycle.read_text() == '{"phase":"preparing"}'
    assert not (tmp_path / "current").exists()


@pytest.mark.parametrize("group", [TARGET + "-other", "/app.slice/panels-adoption.service"])
def test_external_service_can_control_target(
    monkeypatch: pytest.MonkeyPatch, group: str,
) -> None:
    systemd_context(monkeypatch, group)
    SubprocessServiceController("systemctl", "panels-alias.service")
