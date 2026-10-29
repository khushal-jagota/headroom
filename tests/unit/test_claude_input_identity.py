"""Private Claude inputs: semantic values, selected sources, and sampled consistency.

These synthetic fixtures protect the new collector boundary without a provider host.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest

from planner.conversation.model_resolution import claude_inputs
from planner.conversation.model_resolution.logic.claude_configuration import (
    ClaudeConfigurationRole as Role,
)
from planner.conversation.model_resolution.logic.claude_configuration import (
    claude_configuration_root,
    claude_global_candidates,
    claude_secure_storage_root,
    parse_claude_document,
)


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    home, workspace, managed = (tmp_path / name for name in ("home", "workspace", "managed"))
    home.mkdir()
    workspace.mkdir()
    config = home / ".claude"
    config.mkdir()
    return home, workspace, managed, {"HOME": str(home)}


def collect(sources: tuple[Path, Path, Path, dict[str, str]]) -> Any:
    home, workspace, managed, environment = sources
    return claude_inputs.collect_claude_inputs(
        workspace, environment, home=home, managed_directory=managed,
    )


def write(path: Path, content: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def replace_file(path: Path, content: str) -> None:
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_text(content, encoding="utf-8")
    replacement.replace(path)


@pytest.mark.parametrize("before,after", [
    ('{"value":true}', '{"value":1}'),
    ('{"value":1}', '{"value":1.0}'),
    ('{"value":null}', '{}'),
    ('{"value":{}}', '{"value":null}'),
    ('{"value":[1,2]}', '{"value":[2,1]}'),
    ('{"value":[{"nested":true}]}', '{"value":[{"nested":1}]}'),
])
def test_json_preserves_types_presence_and_array_order(before: str, after: str) -> None:
    assert parse_claude_document(before.encode(), Role.global_state) != parse_claude_document(
        after.encode(), Role.global_state,
    )


def test_json_ignores_only_formatting_and_object_order() -> None:
    assert parse_claude_document(b'{"a":1,"b":{"c":2,"d":3}}', Role.settings) == (
        parse_claude_document(b' { "b": {"d":3, "c":2}, "a":1 }\n', Role.settings)
    )


@pytest.mark.parametrize("content", [
    b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":1}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
    b'{"x":"\xff"}', b'{broken', b'[]', b'null', b'1', b'"text"',
])
def test_ambiguous_or_invalid_documents_are_unavailable(
    sources: tuple[Path, Path, Path, dict[str, str]], content: bytes,
) -> None:
    path = sources[0] / ".claude" / ".config.json"
    path.write_bytes(content)
    assert collect(sources) is None


@pytest.mark.parametrize("relative", ["", "relative", "~/config", "$HOME/config"])
def test_normal_configuration_root_requires_absolute_input(
    sources: tuple[Path, Path, Path, dict[str, str]], relative: str,
) -> None:
    sources[3]["CLAUDE_CONFIG_DIR"] = relative
    assert collect(sources) is None


def test_provider_roots_preserve_normalization_raw_legacy_and_secure_empty(
    sources: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    home, _, _, environment = sources
    raw = home / "e\u0301"
    normalized = home / "é"
    environment.update(CLAUDE_CONFIG_DIR=str(raw), CLAUDE_CODE_CUSTOM_OAUTH_URL="synthetic")
    assert claude_configuration_root(environment, home) == normalized
    assert claude_global_candidates(environment, home) == (
        normalized / ".config.json", raw / ".claude-custom-oauth.json",
    )
    assert claude_secure_storage_root(environment, home) == normalized
    environment["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = ""
    assert claude_secure_storage_root(environment, home) == home / ".claude"
    # With preferred absent, the decomposed raw legacy source must participate.
    write(raw / ".claude-custom-oauth.json", '{"account":"one"}')
    before = collect(sources)
    assert before is not None
    write(raw / ".claude-custom-oauth.json", '{"account":"two"}')
    assert collect(sources) != before
    environment["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = "relative"
    assert collect(sources) is None


def test_inactive_legacy_is_not_inspected_and_selection_remains_significant(
    sources: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    preferred, legacy = claude_global_candidates(sources[3], sources[0])
    write(preferred)
    before = collect(sources)
    assert before is not None
    # A symlink loop proves that inactive legacy is not resolved or read.
    legacy.symlink_to(legacy)
    assert collect(sources) == before
    legacy.unlink()
    write(legacy, "invalid legacy")
    assert collect(sources) == before
    preferred.unlink()
    assert collect(sources) is None
    write(preferred, "invalid preferred")
    write(legacy)
    assert collect(sources) is None


@pytest.mark.parametrize("role", [
    "preferred", "legacy", "user", "user_local", "project", "project_local",
    "ancestor", "ancestor_local", "managed", "dropin", "remote",
])
def test_each_candidate_role_tracks_presence_and_complete_content(
    sources: tuple[Path, Path, Path, dict[str, str]], role: str,
) -> None:
    home, workspace, managed, _ = sources
    paths = {
        "preferred": home / ".claude/.config.json", "legacy": home / ".claude.json",
        "user": home / ".claude/settings.json", "user_local": home / ".claude/settings.local.json",
        "project": workspace / ".claude/settings.json",
        "project_local": workspace / ".claude/settings.local.json",
        "ancestor": workspace.parent / ".claude/settings.json",
        "ancestor_local": workspace.parent / ".claude/settings.local.json",
        "managed": managed / "managed-settings.json",
        "dropin": managed / "managed-settings.d/a.json",
        "remote": home / ".claude/remote-settings.json",
    }
    path = paths[role]
    absent = collect(sources)
    assert absent is not None
    write(path)
    empty = collect(sources)
    assert empty is not None and empty != absent
    # All arbitrary nested fields participate, including cache and account data.
    write(path, '{"cachedGrowthBookFeatures":{"unknown":true},"oauthAccount":{"id":"x"}}')
    changed = collect(sources)
    assert changed is not None and changed != empty
    path.unlink()
    assert collect(sources) == absent


def test_empty_directories_unrelated_entries_and_locks_do_not_create_sources(
    sources: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    home, workspace, managed, _ = sources
    before = collect(sources)
    assert before is not None
    for directory in (workspace / ".claude", managed / "managed-settings.d"):
        directory.mkdir(parents=True)
    write(home / ".claude/unrelated", "not JSON")
    write(home / ".claude/.storage-write", "transient")
    write(managed / "managed-settings.d/ignored.txt", "not JSON")
    os.utime(home / ".claude", ns=(1_000_000_000, 2_000_000_000))
    assert collect(sources) == before
    write(managed / "managed-settings.d/b.json")
    with_dropin = collect(sources)
    (managed / "managed-settings.d/b.json").rename(managed / "managed-settings.d/a.json")
    assert collect(sources) != with_dropin


@pytest.mark.parametrize("role", [Role.settings, Role.managed_settings, Role.remote_policy_cache])
def test_unsupported_settings_selectors_refuse_but_global_fields_remain_data(role: Role) -> None:
    with pytest.raises(ValueError):
        parse_claude_document(b'{"settings_path":"synthetic-secret"}', role)
    assert parse_claude_document(b'{"settings_path":"data"}', Role.global_state) is not None


def test_resolved_targets_change_identity_and_unsafe_selected_sources_refuse(
    sources: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    home, workspace, _, _ = sources
    source = home / ".claude/settings.json"
    first, second = workspace / "first.json", workspace / "second.json"
    write(first)
    write(second)
    source.symlink_to(first)
    before = collect(sources)
    assert before is not None
    source.unlink()
    source.symlink_to(second)
    assert collect(sources) != before
    source.unlink()
    source.symlink_to(source)
    assert collect(sources) is None
    source.unlink()
    source.mkdir()
    assert collect(sources) is None
    source.rmdir()
    os.mkfifo(source)
    assert collect(sources) is None


@pytest.mark.parametrize("secure_override", [None, "", "custom"])
def test_credential_metadata_changes_identity_without_opening_bytes(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
    secure_override: str | None,
) -> None:
    home, _, _, environment = sources
    environment["CLAUDE_CONFIG_DIR"] = str(home / "config")
    if secure_override is not None:
        environment["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = (
            str(home / "secure") if secure_override == "custom" else secure_override
        )
    root = claude_secure_storage_root(environment, home)
    assert root is not None
    credential = root / ".credentials.json"
    absent = collect(sources)
    assert absent is not None
    write(credential, "synthetic-credential-not-json")
    original_open = os.open

    def guarded_open(path: Any, *args: Any, **kwargs: Any) -> int:
        assert Path(path).name != ".credentials.json", "credential bytes must never be opened"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    present = collect(sources)
    assert present is not None and present != absent
    os.utime(credential, ns=(1_000_000_000, 2_000_000_000))
    touched = collect(sources)
    assert touched is not None and touched != present
    replace_file(credential, "synthetic-credential-not-json")
    assert collect(sources) != touched
    credential.unlink()
    assert collect(sources) == absent
    credential.symlink_to(root / "missing")
    assert collect(sources) is None
    credential.unlink()
    credential.mkdir()
    assert collect(sources) is None


def test_private_values_have_no_representation(
    sources: tuple[Path, Path, Path, dict[str, str]], caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "synthetic-secret-never-public"
    sources[3]["UNKNOWN_ENVIRONMENT"] = secret
    path = sources[0] / ".claude/.config.json"
    write(path, '{"unknown":"' + secret + '"}')
    vector = collect(sources)
    assert vector is not None and secret not in repr(vector)
    write(path, '{"unknown":"' + secret + '","unknown":1}')
    assert collect(sources) is None
    assert secret not in caplog.text


def test_configuration_home_retarget_is_significant_with_equal_documents(
    sources: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    home, _, _, environment = sources
    first, second, link = home / "first", home / "second", home / "config-link"
    write(first / "settings.json")
    write(second / "settings.json")
    link.symlink_to(first, target_is_directory=True)
    environment["CLAUDE_CONFIG_DIR"] = str(link)
    before = collect(sources)
    assert before is not None
    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    assert collect(sources) != before


def test_equal_replacement_between_exactly_two_passes_is_accepted(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = sources[0] / ".claude/.config.json"
    write(path, '{"a":1,"b":2}')
    before = collect(sources)
    assert before is not None
    original = claude_inputs._capture_pass
    passes = 0

    def capture(*args: Any, **kwargs: Any) -> Any:
        nonlocal passes
        vector = original(*args, **kwargs)
        passes += 1
        if passes == 1:
            replace_file(path, ' {"b":2, "a":1}\n')
        return vector

    monkeypatch.setattr(claude_inputs, "_capture_pass", capture)
    assert collect(sources) == before
    assert passes == 2


def test_different_values_between_passes_refuse_without_retry(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = sources[0] / ".claude/.config.json"
    write(path)
    original = claude_inputs._capture_pass
    passes = 0

    def capture(*args: Any, **kwargs: Any) -> Any:
        nonlocal passes
        vector = original(*args, **kwargs)
        passes += 1
        if passes == 1:
            write(path, '{"unknown":"changed"}')
        return vector

    monkeypatch.setattr(claude_inputs, "_capture_pass", capture)
    assert collect(sources) is None
    assert passes == 2


def test_unreadable_traversal_is_not_confirmed_absence(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = sources[0] / ".claude"
    original = Path.stat

    def inaccessible(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == config:
            raise PermissionError("synthetic-private-path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", inaccessible)
    assert collect(sources) is None


def test_replacement_during_guarded_read_refuses_even_with_equal_bytes(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = sources[0] / ".claude/.config.json"
    write(path)
    original = os.fdopen
    reads = 0

    class ReplacingStream:
        def __init__(self, descriptor: int, *args: Any, **kwargs: Any) -> None:
            self.stream = cast(BinaryIO, original(descriptor, *args, **kwargs))

        def __enter__(self) -> Any:
            self.stream.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def fileno(self) -> int:
            return self.stream.fileno()

        def read(self) -> bytes:
            nonlocal reads
            content = self.stream.read()
            reads += 1
            replace_file(path, "{}")
            return content

    monkeypatch.setattr(os, "fdopen", ReplacingStream)
    assert collect(sources) is None
    assert reads == 1


def test_managed_inventory_change_during_pass_refuses(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = sources[2] / "managed-settings.d"
    directory.mkdir(parents=True)
    original = claude_inputs._managed_membership
    calls = 0

    def inventory(path: Path) -> Any:
        nonlocal calls
        result = original(path)
        calls += 1
        if calls == 1:
            write(directory / "inserted.json")
        return result

    monkeypatch.setattr(claude_inputs, "_managed_membership", inventory)
    assert collect(sources) is None
    assert calls == 2


def test_preferred_insertion_during_fallback_pass_refuses(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferred, legacy = claude_global_candidates(sources[3], sources[0])
    write(legacy)
    original = claude_inputs._read_document

    def read(path: Path, role: Role, **kwargs: Any) -> Any:
        result = original(path, role, **kwargs)
        if path == legacy:
            write(preferred)
        return result

    monkeypatch.setattr(claude_inputs, "_read_document", read)
    assert collect(sources) is None


def test_unstable_credential_marker_refuses_without_content_read(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = sources[0] / ".claude/.credentials.json"
    write(credential, "synthetic")
    original = Path.lstat
    samples = 0

    def changing(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal samples
        result = original(path, *args, **kwargs)
        if path == credential:
            samples += 1
            if samples == 2:
                replace_file(credential, "synthetic")
        return result

    monkeypatch.setattr(Path, "lstat", changing)
    assert collect(sources) is None


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_selected_document_cannot_read_known_credential_bytes(
    sources: tuple[Path, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
    alias: str,
) -> None:
    config = sources[0] / ".claude"
    credential = config / ".credentials.json"
    write(credential, '{"synthetic-secret":"never-read"}')
    selected = config / "settings.json"
    if alias == "symlink":
        selected.symlink_to(credential)
    else:
        selected.hardlink_to(credential)
    original = os.fdopen

    class GuardedStream:
        def __init__(self, descriptor: int, *args: Any, **kwargs: Any) -> None:
            self.stream = cast(BinaryIO, original(descriptor, *args, **kwargs))

        def __enter__(self) -> Any:
            self.stream.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def fileno(self) -> int:
            return self.stream.fileno()

        def read(self) -> bytes:
            opened = os.fstat(self.stream.fileno())
            marker = credential.stat()
            assert (opened.st_dev, opened.st_ino) != (marker.st_dev, marker.st_ino), (
                "a configuration alias must not read credential bytes"
            )
            return self.stream.read()

    monkeypatch.setattr(os, "fdopen", GuardedStream)
    assert collect(sources) is None
