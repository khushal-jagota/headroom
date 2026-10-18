"""Select the requested source's exact Codex CLI before deployment checks."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def required_version(source_root: Path) -> str:
    from planner.conversation.backends.codex_app_server import generate_bindings

    source = source_root.resolve() / "src"
    if not Path(generate_bindings.__file__).resolve().is_relative_to(source):
        raise RuntimeError("Codex pin import does not belong to the requested source")
    version = generate_bindings.PINNED_CODEX_CLI_VERSION
    if not re.fullmatch(r"codex-cli \d+\.\d+\.\d+(?:-[\w.-]+)?", version):
        raise RuntimeError(f"Invalid Codex CLI pin: {version!r}")
    return version


def inspect_version(executable: Path | None, required: str, label: str) -> bool:
    actual = "absent"
    if executable is not None:
        try:
            result = subprocess.run(
                [str(executable), "--version"], capture_output=True, text=True,
                check=True, timeout=30,
            )
            actual = result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            actual = f"unusable ({exc})"
    resolved = executable.resolve() if executable is not None else "absent"
    print(
        f"Codex {label}: required={required!r} actual={actual!r} "
        f"executable={str(executable)!r} resolved={str(resolved)!r}",
        file=sys.stderr,
    )
    return actual == required


def path_codex() -> Path | None:
    executable = shutil.which("codex")
    return Path(executable).absolute() if executable is not None else None


def setup(source_root: Path, tools_dir: Path) -> Path:
    required = required_version(source_root)
    ambient = path_codex()
    if inspect_version(ambient, required, "ambient"):
        assert ambient is not None
        return ambient.parent
    local = Path.home() / ".local" / "bin" / "codex"
    if local != ambient and inspect_version(local, required, "local"):
        return local.parent

    tools_dir = tools_dir.absolute()
    tools_dir.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to provision the pinned Codex CLI")
    subprocess.run(
        [npm, "install", "--prefix", str(tools_dir), "--no-audit", "--no-fund",
         f"@openai/codex@{required.removeprefix('codex-cli ')}"],
        check=True, stdout=sys.stderr, stderr=sys.stderr,
    )
    executable = tools_dir / "node_modules" / ".bin" / "codex"
    if not inspect_version(executable, required, "provisioned"):
        raise RuntimeError("Provisioned Codex CLI does not match the requested source pin")
    return executable.parent


def verify(source_root: Path) -> None:
    if not inspect_version(path_codex(), required_version(source_root), "before pytest"):
        raise RuntimeError("PATH does not resolve the requested source's pinned Codex CLI")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("--source-root", type=Path, required=True)
    setup_parser.add_argument("--tools-dir", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "setup":
        # The only stdout is a directory to prepend to PATH and append to GITHUB_PATH.
        print(setup(args.source_root, args.tools_dir))
    else:
        verify(args.source_root)


if __name__ == "__main__":
    main()
