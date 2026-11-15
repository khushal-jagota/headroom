"""Use the current CLI resolved into the requested source's isolated backend tree."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def inspect_version(executable: Path, label: str) -> str:
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True,
            check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Codex {label} is unusable: {executable}") from exc
    version = result.stdout.strip()
    if not version.startswith("codex-cli "):
        raise RuntimeError(f"Codex {label} returned an invalid version: {version!r}")
    print(
        f"Codex {label}: actual={version!r} executable={str(executable)!r} "
        f"resolved={str(executable.resolve())!r}", file=sys.stderr,
    )
    return version


def source_codex(source_root: Path) -> Path:
    source = source_root.resolve()
    executable = source / "agent_backends/node_modules/.bin/codex"
    if not executable.resolve().is_relative_to(source):
        raise RuntimeError("Codex installation does not belong to the requested source")
    return executable


def setup(source_root: Path) -> Path:
    executable = source_codex(source_root)
    inspect_version(executable, "resolved for this build")
    return executable.parent


def verify(source_root: Path) -> None:
    selected = shutil.which("codex")
    expected = source_codex(source_root)
    if selected is None or Path(selected).resolve() != expected.resolve():
        raise RuntimeError("PATH does not resolve the requested source's Codex CLI")
    inspect_version(expected, "before pytest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "setup":
        print(setup(args.source_root))
    else:
        verify(args.source_root)


if __name__ == "__main__":
    main()
