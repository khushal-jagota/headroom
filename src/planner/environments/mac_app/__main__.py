"""Explicit candidate build and signature inspection; no signing or installation."""

import argparse
from pathlib import Path

from planner.environments.mac_app.build import build_app
from planner.environments.mac_app.configuration import BUNDLE_ID
from planner.environments.mac_app.verify import verify_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--home", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--revision", required=True)
    build.add_argument("--python", type=Path)
    build.add_argument("--installed-app", type=Path)
    build.add_argument("--bundle-id", default=BUNDLE_ID)
    verify = commands.add_parser("verify")
    verify.add_argument("--app", type=Path, required=True)
    verify.add_argument("--previous", type=Path)
    verify.add_argument("--identity", required=True)
    verify.add_argument("--bundle-id", default=BUNDLE_ID)
    verify.add_argument("--python", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build_app(
            args.home,
            args.output,
            args.revision,
            python=args.python,
            installed_app=args.installed_app,
            bundle_id=args.bundle_id,
        )
    else:
        print(
            verify_app(
                args.app, args.identity, args.previous, bundle_id=args.bundle_id, python=args.python
            )
        )


if __name__ == "__main__":
    main()
