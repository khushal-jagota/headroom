"""Prove the actual tool hooks reject a foreign planner source path."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path


def test_real_pytest_and_mypy_refuse_foreign_source_before_work(tmp_path: Path) -> None:
    started = time.monotonic()
    source_tree = Path(__file__).resolve().parents[2]
    tree = tmp_path / "fixture tree with spaces"
    outside = tmp_path / "external working directory"
    outside.mkdir()
    (tree / "scripts").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "src" / "planner").mkdir(parents=True)
    shutil.copyfile(
        source_tree / "scripts/tree_environment.py", tree / "scripts/tree_environment.py"
    )
    shutil.copyfile(source_tree / "tests/conftest.py", tree / "tests/conftest.py")
    shutil.copytree(source_tree / "scripts/test_containment", tree / "scripts/test_containment")

    shutil.copytree(
        source_tree / "tests/support/skill_tree_protection",
        tree / "tests/support/skill_tree_protection",
    )
    shutil.copyfile(
        source_tree / "tests/support/synthetic_homes.py", tree / "tests/support/synthetic_homes.py"
    )
    (tree / "tests/support/__init__.py").touch()

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # This copied-tree proof exercises ordinary default admission. The caller
    # already supplies the actual OS boundary around all fixture subprocesses.
    env.pop("PANELS_REQUIRE_TEST_CONTAINMENT", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--without-pip",
            str(tree / ".venv"),
        ],
        cwd=outside,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    python = tree / ".venv/bin/python"
    site_packages = next((tree / ".venv/lib").glob("python*/site-packages"))
    # Reuse installed tool modules; the fixture has its own interpreter and metadata.
    # A nested venv does not inherit its creator venv's site-packages automatically.
    (site_packages / "tool_modules.pth").write_text(sysconfig.get_path("purelib") + "\n")
    (site_packages / "__editable__.planner-2.0.0.pth").write_text(str(tree / "src") + "\n")
    (tree / "pyproject.toml").write_text(
        '[tool.mypy]\nplugins = ["scripts/tree_environment.py"]\n'
        '[tool.pytest.ini_options]\naddopts = "-q"\npythonpath = ["."]\n'
    )
    imported = tree / "planner-imported"
    collected = tree / "pytest-collected"
    (tree / "src/planner/__init__.py").write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).touch()\n"
    )
    (tree / "tests/test_sentinel.py").write_text(
        f"import planner\nfrom pathlib import Path\nPath({str(collected)!r}).touch()\n"
        "def test_sentinel():\n    assert True\n"
    )
    sentinel = tree / "analysis_sentinel.py"
    sentinel.write_text('value: int = "ordinary-analysis-sentinel"\n')

    def run_tool(tool: str) -> subprocess.CompletedProcess[str]:
        arguments = (
            ["pytest", str(tree / "tests")]
            if tool == "pytest"
            else [
                "mypy",
                "--config-file",
                str(tree / "pyproject.toml"),
                "--no-incremental",
                str(sentinel),
            ]
        )
        return subprocess.run(
            [str(python), "-m", *arguments],
            cwd=outside,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    healthy_pytest = run_tool("pytest")
    assert healthy_pytest.returncode == 0, healthy_pytest.stdout + healthy_pytest.stderr
    assert imported.exists()
    assert collected.exists()
    imported.unlink()
    collected.unlink()
    healthy_mypy = run_tool("mypy")
    assert healthy_mypy.returncode == 1, healthy_mypy.stdout + healthy_mypy.stderr
    assert "[assignment]" in healthy_mypy.stdout
    assert not imported.exists()

    foreign = tmp_path / "live app" / "src"
    (foreign / "planner").mkdir(parents=True)
    (foreign / "planner/__init__.py").write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).touch()\n"
    )
    (site_packages / "__editable__.planner-2.0.0.pth").write_text(str(foreign) + "\n")
    for tool in ("pytest", "mypy"):
        refused = run_tool(tool)
        output = refused.stdout + refused.stderr
        assert refused.returncode == (4 if tool == "pytest" else 2), output
        assert "planner resolves to" in output
        assert str(foreign) in output
        assert "outside" in output
        assert "[assignment]" not in output
        assert not imported.exists(), output
        assert not collected.exists(), output
    print(f"Real pytest/mypy hook smoke wall time: {time.monotonic() - started:.3f}s")
