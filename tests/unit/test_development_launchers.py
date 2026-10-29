"""Launch refusal must precede executable startup and never weaken command grammar."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from development_safety import launcher
from development_safety.contracts import Diagnostic, SafetyRefusal


def _tree(tmp_path: Path) -> Path:
    for package in launcher.PACKAGES:
        (tmp_path / package).mkdir(parents=True)
        (tmp_path / package / "package.json").write_text('{"scripts":{"test":"node sentinel.js"}}')
    return tmp_path


def test_refusal_precedes_npm_or_python_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    monkeypatch.setattr(launcher, "TREE", tree)
    started: list[object] = []
    monkeypatch.setattr(os, "execvpe", lambda *args: started.append(args))

    def refuse(*args: object, **kwargs: object) -> None:
        raise SafetyRefusal(
            Diagnostic(
                "shared deployment storage detected",
                tree,
                tree,
                tree,
                "filesystem identity",
                "sentinel refusal",
            )
        )

    monkeypatch.setattr(launcher, "check_paths", refuse)
    monkeypatch.setattr(launcher, "check_python", refuse)
    for command in (
        ["npm", "--prefix", "web", "ci"],
        ["pytest", "tests"],
        ["python", "-m", "mypy", "source.py"],
        [".venv/bin/pytest", "tests"],
    ):
        assert launcher.main(command) == 2
    assert not started


def test_safe_commands_preserve_arguments_and_use_local_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _tree(tmp_path)
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_python", lambda *args, **kwargs: None)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "/usr/bin/npm")
    assert launcher.npm_command(tree, ["--prefix", "web", "run", "test", "--", "a b"], {}) == [
        "/usr/bin/npm",
        "--prefix",
        str(tree / "web"),
        "run",
        "test",
        "--",
        "a b",
    ]
    assert launcher.python_command(tree, ["python", "-m", "pytest", "a b"], {}) == [
        str(tree / ".venv/bin/python"),
        "-I",
        "-m",
        "pytest",
        "a b",
    ]
    (tree / ".venv/bin").mkdir(parents=True)
    (tree / ".venv/bin/mypy").write_text(f"#!{tree}/.venv/bin/python\n")
    assert launcher.python_command(tree, [".venv/bin/mypy", "a b"], {}) == [
        str(tree / ".venv/bin/mypy"),
        "a b",
    ]


def test_redirects_and_unsupported_npm_grammar_refuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _tree(tmp_path)
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    for env in (
        {"NODE_PATH": "/other"},
        {"NODE_OPTIONS": "--require evil"},
        {"npm_config_prefix": "/other"},
        {"NPM_CONFIG_USERCONFIG": "/other"},
    ):
        with pytest.raises(SafetyRefusal, match="cannot establish dependency safety"):
            launcher.npm_command(tree, ["--prefix", "web", "ci"], env)
    for args in (
        ["--prefix", "../other", "ci"],
        ["--prefix", "web", "ci", "--prefix=/other"],
        ["--prefix", "web", "exec", "tool"],
        ["--prefix", "web", "run", "absent"],
    ):
        with pytest.raises(SafetyRefusal):
            launcher.npm_command(tree, args, {})


def test_setup_preserves_existing_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    dependencies = tree / "web/node_modules"
    dependencies.mkdir()
    marker = dependencies / "keep"
    marker.write_text("retained")
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    with pytest.raises(SafetyRefusal, match="fresh dependencies"):
        launcher.setup(tree, {})
    assert marker.read_text() == "retained"


def test_bootstrap_ignores_target_python_startup(tmp_path: Path) -> None:
    """The actual shell bootstrap cannot import sitecustomize or PYTHONPATH code."""
    scripts = tmp_path / "scripts"
    package = scripts / "development_safety"
    package.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "scripts"
    shutil.copy(source / "dev", scripts / "dev")
    shutil.copy(source / "development_safety/bootstrap.py", package / "bootstrap.py")
    (package / "__init__.py").touch()
    (package / "launcher.py").write_text(
        "def main():\n    print('isolated bootstrap')\n    return 0\n"
    )
    marker = tmp_path / "unsafe-startup"
    (tmp_path / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [str(scripts / "dev")], cwd=tmp_path, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "isolated bootstrap\n"
    assert not marker.exists()


def test_foreign_console_interpreter_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    (tree / ".venv/bin").mkdir(parents=True)
    console = tree / ".venv/bin/pytest"
    console.write_text("#!/foreign/.venv/bin/python\n")
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_python", lambda *args, **kwargs: None)
    with pytest.raises(SafetyRefusal, match="console interpreter identity"):
        launcher.python_command(tree, ["pytest", "tests"], {})


def test_ancestor_node_modules_are_checked_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _tree(tmp_path / "checkout")
    ancestor_modules = tree.parent / "node_modules"
    ancestor_modules.mkdir()
    (ancestor_modules / "dependency").symlink_to(tmp_path / "deployment")
    (tmp_path / "deployment").mkdir()
    monkeypatch.setattr(launcher, "TREE", tree)
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    started: list[object] = []
    monkeypatch.setattr(os, "execvpe", lambda *args: started.append(args))

    def inspect(paths: list[Path], **kwargs: object) -> None:
        if ancestor_modules in paths:
            raise SafetyRefusal(
                Diagnostic(
                    "shared deployment storage detected",
                    ancestor_modules,
                    tmp_path / "deployment",
                    tmp_path / "deployment",
                    "path ancestry",
                    "ancestor dependency sentinel",
                )
            )

    monkeypatch.setattr(launcher, "check_paths", inspect)
    assert launcher.main(["npm", "--prefix", "web", "run", "test"]) == 2
    assert not started


def test_standard_npm_lifecycle_paths_are_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _tree(tmp_path)
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    launcher.node_environment(
        tree,
        tree / "web",
        {
            "npm_config_prefix": str(tree / "web"),
            "npm_config_local_prefix": str(tree / "web"),
            "npm_config_global_prefix": str(tree / "web"),
            "npm_config_globalconfig": str(tree / "web/etc/npmrc"),
            "npm_config_userconfig": str(Path.home() / ".npmrc"),
            "npm_config_cache": str(Path.home() / ".npm"),
            "npm_config_user_agent": "npm/10.9.8 node/v22.23.1 linux x64 workspaces/false",
        },
    )


def test_real_scanner_blocks_shared_dependencies_before_npm_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from development_safety import scanner

    tree = _tree(tmp_path / "checkout")
    deployment = tmp_path / "deployment"
    (deployment / "node_modules").mkdir(parents=True)
    (tree / "web/node_modules").symlink_to(deployment / "node_modules")
    monkeypatch.setattr(scanner, "protected_roots", lambda environment: (deployment,))
    monkeypatch.setattr(launcher, "TREE", tree)
    started: list[object] = []
    monkeypatch.setattr(os, "execvpe", lambda *args: started.append(args))
    assert launcher.main(["npm", "--prefix", "web", "ci"]) == 2
    output = capsys.readouterr().err
    assert "shared deployment storage detected" in output
    assert str(tree / "web/node_modules") in output
    assert str(deployment / "node_modules") in output
    assert not started


def test_setup_pip_cannot_inherit_install_destination_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _tree(tmp_path)
    monkeypatch.setattr(launcher, "check_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_paths", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "check_python", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "node_safety", lambda *args: tree / "web")
    monkeypatch.setattr(launcher, "npm_command", lambda *args: ["npm", "ci"])
    children: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> None:
        children.append((command, env))

    monkeypatch.setattr(subprocess, "run", run)
    assert (
        launcher.setup(
            tree,
            {
                "PIP_TARGET": "/deployment/packages",
                "PIP_PREFIX": "/deployment",
                "PIP_ROOT": "/deployment",
                "PIP_CONFIG_FILE": "/deployment/pip.conf",
                "PIP_USER": "1",
                "PIP_INDEX_URL": "https://registry.example/simple",
            },
        )
        == 0
    )
    installs = [command for command, _ in children if "pip" in command]
    assert len(installs) == 2
    for command in installs:
        assert "--no-cache-dir" in command
        assert command[1] == "-I"
    for _, environment in children:
        assert environment["PIP_CONFIG_FILE"] == os.devnull
        assert environment["PIP_INDEX_URL"] == "https://registry.example/simple"
        assert not {"PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_USER"} & environment.keys()
