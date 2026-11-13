"""Linux service serialization and admission-before-mutation boundaries."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


def test_install_checks_host_in_existing_private_state_before_service_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configuration(tmp_path)
    credential = tmp_path / "credential.json"
    write_credential(credential, "a" * 43)
    config.data.mkdir()
    (config.installation / "releases").mkdir(parents=True)
    runtime = tmp_path / "admitted-runtime"
    runtime.mkdir()
    units = tmp_path / "units"
    monkeypatch.setattr(
        "planner.environments.linux_executor_runtime.extract_runtime",
        lambda *a, **kw: runtime,
    )
    monkeypatch.setattr(
        "planner.environments.app.validate_app_manifest",
        lambda *a, **kw: SimpleNamespace(runtime_contract=object()),
    )
    checked: list[Path] = []

    def verify_host(self: object, contract: object, *, state: Path) -> None:
        assert state.is_dir()
        assert list(config.data.iterdir()) == []
        assert not units.exists()
        checked.append(state)

    monkeypatch.setattr(
        "planner.platform_runtime.linux.runtime.LinuxRuntime.verify_host", verify_host
    )
    unit = install(
        config,
        archive=tmp_path / "archive.tar.gz",
        archive_sha256="a" * 64,
        archive_size=123,
        credential_file=credential,
        unit_directory=units,
    )
    assert len(checked) == 1 and not checked[0].exists()
    assert unit.read_text() == render_service(config, runtime)
    assert (config.data / "machine-credential.json").stat().st_mode & 0o777 == 0o600


def test_reinstall_refuses_before_selected_runtime_or_credentials_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configuration(tmp_path)
    selection = config.data / "updates/selection.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"runtime_path":"selected-newer-runtime"}')
    old_credential = config.data / "machine-credential.json"
    old_credential.write_text("private-existing-credential")
    units = tmp_path / "units"
    units.mkdir()
    unit = units / "panels-executor.service"
    unit.write_text("existing service")
    monkeypatch.setattr(
        "planner.environments.linux_executor_runtime.extract_runtime",
        lambda *a, **kw: pytest.fail("reinstall must refuse before archive extraction"),
    )
    with pytest.raises(ValueError, match="already installed.*update path"):
        install(
            config,
            archive=tmp_path / "archive.tar.gz",
            archive_sha256="a" * 64,
            archive_size=123,
            credential_file=tmp_path / "missing-credential",
            unit_directory=units,
        )
    assert selection.read_text() == '{"runtime_path":"selected-newer-runtime"}'
    assert old_credential.read_text() == "private-existing-credential"
    assert unit.read_text() == "existing service"
    assert not config.installation.exists()
