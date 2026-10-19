"""Synthetic proof of the steering harness's canonical Claude profile route."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.real_provider_application import require_provider_auth

from planner.conversation.contracts import ConversationBackendKey


@pytest.fixture
def canonical_claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    normal_home = tmp_path / "normal-home"
    canonical = normal_home / ".claude"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(
        "tests.support.real_provider_application.pwd.getpwuid",
        lambda _: SimpleNamespace(pw_dir=str(normal_home)),
    )
    monkeypatch.setenv("HOME", str(normal_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(canonical))
    return canonical


def test_claude_canonical_profile_follows_atomic_credential_rotation_without_artifacts(
    canonical_claude_home: Path, tmp_path: Path
) -> None:
    credential = canonical_claude_home / ".credentials.json"
    credential.write_text('{"synthetic_token": "old"}')
    settings = canonical_claude_home / "settings.json"
    settings.write_text('{"theme": "dark"}')
    before = set(tmp_path.rglob("*"))

    receipt = require_provider_auth(ConversationBackendKey.claude)

    profile = Path(receipt["profile"])
    assert profile == canonical_claude_home
    assert receipt["profile_variable"] == "CLAUDE_CONFIG_DIR"
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(canonical_claude_home)
    assert set(tmp_path.rglob("*")) == before
    assert credential.read_text() == '{"synthetic_token": "old"}'
    assert settings.read_text() == '{"theme": "dark"}'

    replacement = canonical_claude_home / "replacement.json"
    replacement.write_text('{"synthetic_token": "new"}')
    os.replace(replacement, credential)

    assert (profile / ".credentials.json").read_text() == '{"synthetic_token": "new"}'


def test_claude_rejects_noncanonical_profile(
    canonical_claude_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_profile = tmp_path / "other-claude"
    other_profile.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other_profile))

    with pytest.raises(AssertionError, match="^CLAUDE_CONFIG_DIR$"):
        require_provider_auth(ConversationBackendKey.claude)
