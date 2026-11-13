"""The oldest CLI features Panels uses, independent of binding generation versions."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from packaging.version import InvalidVersion, Version

type Backend = Literal["codex", "claude"]


@dataclass(frozen=True)
class _Requirement:
    name: str
    version: str
    reason: str
    output_pattern: str


_REQUIREMENTS: Final[dict[Backend, _Requirement]] = {
    "codex": _Requirement(
        "Codex", "0.148.0", "thread-aware app/read", r"codex-cli (\S+)"
    ),
    "claude": _Requirement(
        "Claude", "2.1.219", "queued message cancellation on Stop", r"(\S+) \(Claude Code\)"
    ),
}
VERSION_TIMEOUT_SECONDS: Final = 10.0


class BackendCliIncompatible(Exception):
    """The selected executable cannot supply the CLI features Panels uses."""


async def ensure_cli_compatible(
    backend: Backend,
    executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> None:
    """Check the chosen executable before it starts a backend or catalogue probe."""
    requirement = _REQUIREMENTS[backend]
    repair = (
        f"Update {requirement.name} to at least {requirement.version}. "
        f"Panels requires {requirement.reason}."
    )
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            env=None if environment is None else {**os.environ, **environment},
            cwd=working_directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await asyncio.wait_for(process.communicate(), VERSION_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as failed:
        raise BackendCliIncompatible(
            f"Cannot check the {requirement.name} version at {executable}. {repair}"
        ) from failed
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

    reported = re.fullmatch(requirement.output_pattern, output.decode(errors="replace").strip())
    if process.returncode != 0 or reported is None:
        raise BackendCliIncompatible(
            f"Cannot read the {requirement.name} version at {executable}. {repair}"
        )
    try:
        version = Version(reported.group(1))
    except InvalidVersion as invalid:
        raise BackendCliIncompatible(
            f"Cannot read the {requirement.name} version at {executable}. {repair}"
        ) from invalid
    if version < Version(requirement.version):
        raise BackendCliIncompatible(
            f"{requirement.name} {version} at {executable} is too old. {repair}"
        )
