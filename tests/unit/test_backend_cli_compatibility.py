"""The actual feature floor must not follow whichever CLI generated the bindings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from planner.conversation.backends import cli_compatibility as compatibility


@pytest.mark.parametrize(
    ("backend", "reported", "accepted"),
    [
        ("codex", "codex-cli 0.147.0", False),
        ("codex", "codex-cli 0.148.0", True),
        ("codex", "codex-cli 0.999.0", True),
        ("claude", "2.1.218 (Claude Code)", False),
        ("claude", "2.1.219 (Claude Code)", True),
        ("claude", "9.0.0 (Claude Code)", True),
    ],
)
def test_selected_cli_feature_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: compatibility.Backend,
    reported: str,
    accepted: bool,
) -> None:
    process = AsyncMock(returncode=0)
    process.communicate.return_value = (reported.encode(), b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run() -> None:
        check = compatibility.ensure_cli_compatible(
            backend, "/selected/cli", environment={"SELECTED_HOME": "test"},
            working_directory=tmp_path,
        )
        if accepted:
            await check
        else:
            with pytest.raises(compatibility.BackendCliIncompatible, match="Update .* to at least"):
                await check

    asyncio.run(run())
    assert spawn.call_args.args == ("/selected/cli", "--version")
    assert spawn.call_args.kwargs["env"]["SELECTED_HOME"] == "test"
    assert spawn.call_args.kwargs["cwd"] == tmp_path


@pytest.mark.parametrize("reported", ["", "codex-cli unknown", "unrelated 99.0.0"])
def test_unreadable_version_has_actionable_failure(
    monkeypatch: pytest.MonkeyPatch, reported: str,
) -> None:
    process = AsyncMock(returncode=0)
    process.communicate.return_value = (reported.encode(), b"")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(
        compatibility.BackendCliIncompatible, match="Update Codex to at least 0.148.0",
    ):
        asyncio.run(compatibility.ensure_cli_compatible("codex", "/selected/codex"))


@pytest.mark.parametrize("interrupted", [TimeoutError, asyncio.CancelledError])
def test_interrupted_version_probe_reaps_only_its_child(
    monkeypatch: pytest.MonkeyPatch, interrupted: type[BaseException],
) -> None:
    process = AsyncMock(returncode=None)
    process.kill = Mock()
    process.communicate.side_effect = interrupted()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    expected = (
        compatibility.BackendCliIncompatible if interrupted is TimeoutError else interrupted
    )
    with pytest.raises(expected):
        asyncio.run(compatibility.ensure_cli_compatible("codex", "/selected/codex"))
    process.wait.assert_awaited_once()
    process.kill.assert_called_once()
