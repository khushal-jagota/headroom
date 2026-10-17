"""Synthetic proof that the steering harness shares Claude credential rotation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.integration.test_conversation_real_provider_steering import (
    CLAUDE_CONFIG_TEMPLATE_ENVIRONMENT_NAME,
    _prepare_isolated_provider_home,
)

from planner.conversation.contracts import ConversationBackendKey


def test_claude_home_follows_atomic_credential_rotation_and_isolates_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_home = tmp_path / "canonical-claude"
    canonical_home.mkdir()
    credential = canonical_home / ".credentials.json"
    credential.write_text('{"synthetic_token": "old"}')
    settings = canonical_home / "settings.json"
    settings.write_text('{"theme": "dark"}')
    copied_credential = tmp_path / "copied-credential.json"
    shutil.copy2(credential, copied_credential)
    hardlinked_credential = tmp_path / "hardlinked-credential.json"
    hardlinked_credential.hardlink_to(credential)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv(CLAUDE_CONFIG_TEMPLATE_ENVIRONMENT_NAME, str(canonical_home))

    _prepare_isolated_provider_home(ConversationBackendKey.claude, runtime_root, monkeypatch)

    isolated_home = Path(os.environ["CLAUDE_CONFIG_DIR"])
    assert isolated_home == runtime_root / "claude-config"
    isolated_credential = isolated_home / ".credentials.json"
    assert isolated_credential.is_symlink()
    assert isolated_credential.resolve() == credential
    replacement = canonical_home / "replacement.json"
    replacement.write_text('{"synthetic_token": "new"}')
    os.replace(replacement, credential)
    assert isolated_credential.read_text() == '{"synthetic_token": "new"}'
    assert copied_credential.read_text() == '{"synthetic_token": "old"}'
    assert hardlinked_credential.read_text() == '{"synthetic_token": "old"}'
    isolated_settings = isolated_home / "settings.json"
    assert isolated_settings.read_text() == '{"theme": "dark"}'
    assert not isolated_settings.is_symlink()
    assert isolated_settings.stat().st_ino != settings.stat().st_ino
    isolated_settings.write_text('{"theme": "light"}')
    assert settings.read_text() == '{"theme": "dark"}'


def test_claude_home_accepts_absent_optional_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_home = tmp_path / "canonical-claude"
    canonical_home.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv(CLAUDE_CONFIG_TEMPLATE_ENVIRONMENT_NAME, str(canonical_home))

    _prepare_isolated_provider_home(ConversationBackendKey.claude, runtime_root, monkeypatch)

    isolated_home = Path(os.environ["CLAUDE_CONFIG_DIR"])
    assert isolated_home == runtime_root / "claude-config"
    assert list(isolated_home.iterdir()) == []
