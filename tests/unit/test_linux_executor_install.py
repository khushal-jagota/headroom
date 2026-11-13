"""Linux service serialization and admission-before-mutation boundaries."""

from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.linux_executor import (
    LinuxExecutorInstallation,
    install,
    render_service,
)
from planner.executor.credential_file import write_credential


def configuration(tmp_path: Path) -> LinuxExecutorInstallation:
    return LinuxExecutorInstallation(
        tmp_path / "runtime with % $ space",
        tmp_path / "data",
        "linux-test",
        "https://home.example",
        "a" * 40,
    )


def test_service_preserves_literal_paths_and_supervisor_shutdown(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    unit = render_service(config, config.installation / "releases/one")
    assert "runtime with %% $$ space" in unit
    assert '"PLAN_EXECUTOR_ROOT=' + str(config.data) + '"' in unit
    assert "KillMode=mixed\nTimeoutStopSec=30" in unit
    assert "Restart=on-failure" in unit
    assert 'Environment="PLAN_DB_PATH' not in unit
    with pytest.raises(ValueError, match="control characters"):
        render_service(replace(config, data=tmp_path / "bad\npath"), config.installation)
    with pytest.raises(ValueError, match="service name"):
        render_service(replace(config, service_name="../home.service"), config.installation)


def test_failed_archive_admission_never_writes_credentials_or_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configuration(tmp_path)
    credential = tmp_path / "credential.json"
    write_credential(credential, "a" * 43)

    def refuse(*args: object, **kwargs: object) -> Path:
        raise ValueError("wrong archive role")

    monkeypatch.setattr("planner.environments.linux_executor_runtime.extract_runtime", refuse)
    with pytest.raises(ValueError, match="wrong archive role"):
        install(
            config,
            archive=tmp_path / "archive.tar.gz",
            archive_sha256="a" * 64,
            archive_size=123,
            credential_file=credential,
            unit_directory=tmp_path / "units",
        )
    assert not config.data.exists()
    assert not (tmp_path / "units").exists()
