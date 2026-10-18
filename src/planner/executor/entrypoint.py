"""Installed package entrypoint, with a light executor service boot path."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "executor":
        from planner.executor.cli import executor_group

        executor_group.main(args=sys.argv[2:], prog_name="panels executor")
    else:
        from planner.cli.main import main as panels_main

        panels_main()


def resident_main(revision: str) -> None:
    """Apply resident restrictions around the generated package entrypoint."""
    # Click accepts a separator before a group command. Refuse it at both
    # routing boundaries so it cannot hide serve or executor run from this guard.
    if sys.argv[1:2] == ["--"] or sys.argv[1:3] == ["executor", "--"]:
        raise SystemExit("resident command separators are not supported before commands")
    if sys.argv[1:2] == ["serve"]:
        raise SystemExit("resident package cannot run the local Panels server")
    if sys.argv[1:3] == ["executor", "run"]:
        if any(
            arg == "--application-revision" or arg.startswith("--application-revision=")
            for arg in sys.argv[3:]
        ):
            raise SystemExit("application revision is owned by the installed package")
        sys.argv.extend(["--application-revision", revision])
    main()


if __name__ == "__main__":
    main()
