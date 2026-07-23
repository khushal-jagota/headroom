"""Stable launcher boundary for a validated production release."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from planner.environments.release import validate_release_manifest


def build_release_launch_env(release_root: Path, *, ambient: Mapping[str, str]) -> dict[str, str]:
    root = release_root.expanduser().resolve()
    manifest = validate_release_manifest(root / "manifest.json")
    allowed = {
        key: value
        for key, value in ambient.items()
        if key in {"PATH", "LANG", "TERM", "TMPDIR"} or key.startswith("LC_")
    }
    allowed.update({"PLAN_RELEASE_SHA": manifest.release_sha, "PLAN_RELEASE_ROOT": str(root)})
    return allowed


def launch_release(
    release_root: Path, argv: list[str], *, ambient: Mapping[str, str] | None = None
) -> None:
    env = build_release_launch_env(release_root, ambient=os.environ if ambient is None else ambient)
    os.execve(str(release_root / ".venv" / "bin" / "python"), argv, env)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: release_launcher RELEASE_ROOT [planner arguments]")
    root = Path(sys.argv[1]).expanduser().resolve()
    launch_release(
        root,
        [str(root / ".venv" / "bin" / "python"), "-m", "planner", *sys.argv[2:]],
    )


if __name__ == "__main__":
    main()
