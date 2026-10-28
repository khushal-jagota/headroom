"""Prove the real tool entry points stop drift before importing or analyzing code."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path


def test_real_pytest_and_mypy_refuse_pin_drift_before_work(tmp_path: Path) -> None:
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
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--system-site-packages",
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
    planner_metadata = site_packages / "planner-2.0.0.dist-info"
    planner_metadata.mkdir()
    (planner_metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: planner\nVersion: 2.0.0\n"
    )
    (planner_metadata / "direct_url.json").write_text(
        json.dumps({"url": tree.as_uri(), "dir_info": {"editable": True}})
    )
    sdk_metadata = site_packages / "claude_agent_sdk-0.2.128.dist-info"
    sdk_metadata.mkdir()
    (sdk_metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: claude-agent-sdk\nVersion: 0.2.128\n"
    )
    (tree / "requirements.txt").write_text(
        f"claude-agent-sdk==0.2.128\npackaging=={importlib.metadata.version('packaging')}\n"
    )
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
        f"from pathlib import Path\nPath({str(collected)!r}).touch()\n"
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

    # A repository pin bump deliberately makes the unchanged environment stale.
    requirements = tree / "requirements.txt"
    requirements.write_text(requirements.read_text().replace("==0.2.128", "==0.2.152"))
    for tool in ("pytest", "mypy"):
        refused = run_tool(tool)
        output = refused.stdout + refused.stderr
        assert refused.returncode == (4 if tool == "pytest" else 2), output
        assert "claude-agent-sdk" in output
        assert "0.2.128" in output
        assert "0.2.152" in output
        assert "requirements.txt" in output
        assert "No bypass is supported." in output
        assert "After a pin change, refusal is expected" in output
        assert "until installed dependencies match the pins." in output
        assert shlex.quote(str(tree)) in output
        assert "[assignment]" not in output
        assert not imported.exists(), output
        assert not collected.exists(), output
    print(f"Real pytest/mypy hook smoke wall time: {time.monotonic() - started:.3f}s")
