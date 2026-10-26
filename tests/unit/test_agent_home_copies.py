"""The home-copy tripwire accuses relationships, and records its bounded dismissals."""

from pathlib import Path

from check_agent_home_copies import inspect_source

ROOT = Path(__file__).resolve().parents[2]








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
