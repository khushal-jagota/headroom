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


if __name__ == "__main__":
    main()
