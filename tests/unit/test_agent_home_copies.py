"""The home-copy tripwire accuses relationships, and records its bounded dismissals."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from check_agent_home_copies import inspect_source

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("missing", [("src", "tests", "scripts", "ops"), ("ops",)])
def test_cli_rejects_incomplete_scan_root(tmp_path: Path, missing: tuple[str, ...]) -> None:
    for name in ("src", "tests", "scripts", "ops"):
        if name not in missing:
            (tmp_path / name).mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_agent_home_copies.py"),
            "--root",
            str(tmp_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert str(tmp_path.resolve()) in result.stderr
    assert "missing searched directories: " + ", ".join(missing) in result.stderr
    assert result.stdout == ""


def test_copied_script_reports_its_invalid_default_root(tmp_path: Path) -> None:
    location = tmp_path / "copied"
    location.mkdir()
    script = location / "check_agent_home_copies.py"
    shutil.copy2(ROOT / "scripts/check_agent_home_copies.py", script)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2
    assert str(tmp_path.resolve()) in result.stderr
    assert "missing searched directories" in result.stderr
    assert result.stdout == ""


def test_real_provider_sites_accept_current_and_credential_only_repair() -> None:
    permissions = "tests/integration/test_conversation_real_provider_permissions.py"
    steering = "tests/integration/test_conversation_real_provider_steering.py"
    first = inspect_source((ROOT / permissions).read_text(), permissions)
    source = (ROOT / steering).read_text()
    second = inspect_source(source, steering)
    dismissals = [item for item in first + second if item.classification == "dismissal"]
    accusations = [item for item in first + second if item.classification == "accusation"]
    assert len(dismissals) == 2
    assert len(accusations) <= 1
    for finding in first + second:
        actual_line = (ROOT / finding.path).read_text().splitlines()[finding.line - 1]
        assert "shutil.copy2(" in actual_line
    if accusations:
        assert accusations[0].files == (".credentials.json",)
        # Simulate the approved repair: only credentials become a link.
        source = source.replace(
            "            shutil.copy2(candidate, destination / name)",
            '            if name == ".credentials.json":\n'
            "                (destination / name).symlink_to(candidate)\n"
            "            else:\n"
            "                shutil.copy2(candidate, destination / name)",
        )
    assert [item.classification for item in first + inspect_source(source)] == [
        "dismissal",
        "dismissal",
    ]


def test_prefixed_credential_copy_proof_and_credential_only_repair() -> None:
    before = """
import shutil

def prepare(source, destination, monkeypatch):
    for name in (".credentials.json", "settings.json"):
        candidate = source / name
        if candidate.exists():
            shutil.copy2(candidate, destination / name)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(destination))
"""
    findings = inspect_source(before)
    assert len(findings) == 1
    assert findings[0].classification == "accusation"
    assert findings[0].files == (".credentials.json",)
    after = before.replace(
        "            shutil.copy2(candidate, destination / name)",
        '            if name == ".credentials.json":\n'
        "                (destination / name).symlink_to(candidate)\n"
        "            else:\n"
        "                shutil.copy2(candidate, destination / name)",
    )
    assert inspect_source(after) == []


def test_whole_home_reproducer_and_import_aliases() -> None:
    findings = inspect_source("""
from shutil import copytree as duplicate
import os

def example(real_home, scratch_home):
    duplicate(real_home, scratch_home)
    os.environ["CODEX_HOME"] = str(scratch_home)
""")
    assert len(findings) == 1
    assert findings[0].classification == "accusation"


def test_synthetic_writes_and_skill_links_are_not_copies() -> None:
    assert (
        inspect_source("""
import os

def example(home, skills):
    (home / "auth.json").write_text('{"refresh_token":"invented"}')
    (home / "skills").symlink_to(skills)
    os.environ["CODEX_HOME"] = str(home)
""")
        == []
    )


def test_unrelated_paths_functions_and_mutually_exclusive_branches() -> None:
    assert (
        inspect_source("""
import shutil
import os

def first(source, backup, home):
    shutil.copytree(source, backup)
    os.environ["CODEX_HOME"] = str(home)

def second(source, home):
    shutil.copytree(source, home)

def third(home):
    os.environ["CODEX_HOME"] = str(home)

def fourth(source, home, condition):
    if condition:
        shutil.copytree(source, home)
    else:
        os.environ["CODEX_HOME"] = str(home)
""")
        == []
    )


def test_literal_filename_aliases_and_safe_config_structure() -> None:
    findings = inspect_source("""
import shutil as files

def example(source, root, monkeypatch):
    destination = root / "home"
    alias = destination
    for filename in ("config.yaml", ".env"):
        candidate = source / filename
        files.copy2(candidate, alias / filename)
    (alias / "auth.json").symlink_to(source / "auth.json")
    monkeypatch.setenv("PLAN_HERMES_HOME", str(destination))
""")
    assert len(findings) == 1
    assert findings[0].classification == "dismissal"
    assert findings[0].files == (".env", "config.yaml")


def test_auth_symlink_does_not_excuse_credentials_or_wrong_canonical_source() -> None:
    findings = inspect_source("""
import shutil

def example(source, other, home, monkeypatch):
    shutil.copy2(source / ".credentials.json", home / ".credentials.json")
    shutil.copy2(source / "config.yaml", home / "config.yaml")
    (home / "auth.json").symlink_to(other / "auth.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
""")
    assert [item.classification for item in findings] == ["accusation", "accusation"]


def test_renaming_credentials_to_config_does_not_dismiss_the_copy() -> None:
    findings = inspect_source("""
import shutil

def example(source, home, monkeypatch):
    shutil.copy2(source / ".credentials.json", home / "config.yaml")
    (home / "auth.json").symlink_to(source / "auth.json")
    monkeypatch.setenv("PLAN_HERMES_HOME", str(home))
""")
    assert len(findings) == 1
    assert findings[0].classification == "accusation"


def test_unreachable_canonical_link_cannot_dismiss_a_copy() -> None:
    findings = inspect_source("""
import shutil

def example(source, home, monkeypatch, condition):
    if condition:
        shutil.copy2(source / "config.yaml", home / "config.yaml")
        monkeypatch.setenv("PLAN_HERMES_HOME", str(home))
        return
    (home / "auth.json").symlink_to(source / "auth.json")
""")
    assert len(findings) == 1
    assert findings[0].classification == "accusation"


def test_unknown_filename_does_not_prune_a_possible_credential_copy() -> None:
    findings = inspect_source("""
import shutil

def example(source, home, monkeypatch):
    filename = choose_filename()
    if filename == ".credentials.json":
        shutil.copy2(source / ".credentials.json", home / ".credentials.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
""")
    assert len(findings) == 1
    assert findings[0].classification == "accusation"


def test_literal_alias_prunes_only_until_an_unknown_reassignment() -> None:
    findings = inspect_source("""
import shutil

def example(source, home, monkeypatch):
    filename = "settings.json"
    alias = filename
    if alias != ".credentials.json":
        shutil.copy2(source / "settings.json", home / "settings.json")
    alias = choose_filename()
    if alias == ".credentials.json":
        shutil.copy2(source / ".credentials.json", home / ".credentials.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
""")
    assert len(findings) == 1
    assert findings[0].classification == "accusation"
