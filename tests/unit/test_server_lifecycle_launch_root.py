"""Lifecycle identity resolves package ownership before any runtime effects."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.cli.main import main
from planner.core import config
from planner.server_lifecycle import application, control, supervisor
from planner.server_lifecycle.contracts import ServerLifecycleError


def _module(root: Path, *, source: bool = False) -> Path:
    package = root / ("src" if source else ".venv/lib/python3.12/site-packages")
    module = package / "planner/server_lifecycle/supervisor.py"
    module.parent.mkdir(parents=True)
    module.touch()
    return module


@pytest.mark.parametrize("identity", [None, "/foreign/application"])
def test_source_ownership_precedes_environment_and_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: str | None
) -> None:
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(supervisor, "__file__", str(_module(checkout, source=True)))
    monkeypatch.chdir(tmp_path)
    if identity is None:
        monkeypatch.delenv("PLAN_APP_ROOT", raising=False)
    else:
        monkeypatch.setenv("PLAN_APP_ROOT", identity)

    assert supervisor.resolve_planner_launch_root() == checkout


@pytest.mark.parametrize("use_symlink", [False, True])
def test_packaged_ownership_accepts_matching_root_and_application_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_symlink: bool
) -> None:
    root = tmp_path / "application"
    module = _module(root)
    alias = root
    if use_symlink:
        alias = tmp_path / "current"
        alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(supervisor, "__file__", str(alias / module.relative_to(root)))
    monkeypatch.setenv("PLAN_APP_ROOT", str(alias))
    monkeypatch.chdir(tmp_path)

    assert supervisor.resolve_planner_launch_root() == root


@pytest.mark.parametrize("identity", [None, "", "relative", "foreign", "sibling-prefix"])
def test_packaged_ownership_rejects_missing_or_foreign_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: str | None
) -> None:
    root = tmp_path / "application-other"
    module = _module(root)
    identities = {
        "foreign": str(tmp_path / "foreign"),
        "sibling-prefix": str(tmp_path / "application"),
    }
    monkeypatch.setattr(supervisor, "__file__", str(module))
    monkeypatch.chdir(tmp_path)
    if identity is None:
        monkeypatch.delenv("PLAN_APP_ROOT", raising=False)
    else:
        monkeypatch.setenv("PLAN_APP_ROOT", identities.get(identity, identity))

    with pytest.raises(ServerLifecycleError, match="bin/panels-launcher serve") as caught:
        supervisor.resolve_planner_launch_root()
    assert str(module) in str(caught.value)


@pytest.mark.parametrize(
    "escape", ["virtualenv", "package", "virtualenv-sibling-prefix", "virtualenv-root"]
)
def test_packaged_ownership_rejects_symlink_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
    root = tmp_path / "application"
    root.mkdir()
    if escape == "virtualenv":
        module = _module(tmp_path / "foreign")
        (root / ".venv").symlink_to(tmp_path / "foreign/.venv", target_is_directory=True)
    elif escape == "virtualenv-root":
        module = root / "lib/python3.12/site-packages/planner/server_lifecycle/supervisor.py"
        module.parent.mkdir(parents=True)
        module.touch()
        (root / ".venv").symlink_to(root, target_is_directory=True)
    else:
        target = root / ".venv-other" if escape == "virtualenv-sibling-prefix" else tmp_path
        module = target / "lib/python3.12/site-packages/planner/server_lifecycle/supervisor.py"
        module.parent.mkdir(parents=True)
        module.touch()
        local_package = root / ".venv/lib/python3.12/site-packages/planner"
        local_package.parent.mkdir(parents=True)
        local_package.symlink_to(module.parents[1], target_is_directory=True)
    imported_module = (
        root / ".venv/lib/python3.12/site-packages/planner/server_lifecycle/supervisor.py"
    )
    monkeypatch.setattr(supervisor, "__file__", str(imported_module))
    monkeypatch.setenv("PLAN_APP_ROOT", str(root))

    with pytest.raises(ServerLifecycleError, match="bin/panels-launcher serve"):
        supervisor.resolve_planner_launch_root()


@pytest.mark.parametrize("layout", ["src-other", ".venv/lib64/python3.12/site-packages"])
def test_unsupported_layout_cannot_claim_source_or_packaged_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    root = tmp_path / "application"
    module = root / layout / "planner/server_lifecycle/supervisor.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(supervisor, "__file__", str(module))
    monkeypatch.setenv("PLAN_APP_ROOT", str(root))

    with pytest.raises(ServerLifecycleError):
        supervisor.resolve_planner_launch_root()


@pytest.mark.parametrize("entrypoint", ["supervisor", "application", "serve", "restart"])
def test_rejected_identity_fails_before_configuration_or_runtime_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    module = _module(tmp_path / "foreign")
    monkeypatch.setattr(supervisor, "__file__", str(module))
    monkeypatch.delenv("PLAN_APP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Rejected identity reached a runtime operation")

    monkeypatch.setattr(config, "load_config", forbidden)
    monkeypatch.setattr(supervisor, "load_config", forbidden)
    monkeypatch.setattr(application, "load_config", forbidden)
    monkeypatch.setattr(control, "resolve_server_control_socket_path", forbidden)
    monkeypatch.setattr(supervisor, "resolve_server_control_socket_path", forbidden)
    monkeypatch.setattr(supervisor.PortScopedServerLifecycleLease, "acquire", forbidden)
    monkeypatch.setattr(supervisor.ServerSupervisor, "_spawn_application_child", forbidden)
    monkeypatch.setattr(application, "connect", forbidden)
    monkeypatch.setattr(application, "create_app", forbidden)

    if entrypoint in {"serve", "restart"}:
        result = CliRunner().invoke(main, [entrypoint])
        assert result.exit_code != 0
        diagnostic = result.output + str(result.exception)
        assert "bin/panels-launcher serve" in diagnostic
    else:
        invoke = (
            supervisor.run_server_supervisor
            if entrypoint == "supervisor"
            else application.run_application_process
        )
        with pytest.raises(ServerLifecycleError, match="bin/panels-launcher serve"):
            invoke()
    assert Path.cwd() == tmp_path
    assert sorted(path.name for path in tmp_path.iterdir()) == ["foreign"]
