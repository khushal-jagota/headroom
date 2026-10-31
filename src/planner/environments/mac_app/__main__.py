"""Explicit candidate build and signature inspection; no signing or installation."""

import argparse
from pathlib import Path

from planner.environments.mac_app.build import build_app
from planner.environments.mac_app.verify import verify_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--home", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--revision", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--app", type=Path, required=True)
    verify.add_argument("--previous", type=Path)
    verify.add_argument("--identity", required=True)
    args = parser.parse_args()
    if args.command == "build":
        build_app(args.home, args.output, args.revision)
    else:
        print(verify_app(args.app, args.identity, args.previous))


if __name__ == "__main__":
    main()
