"""Offline resident preparation; no command installs or starts a service."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from planner.environments.resident.contracts import ResidentError


def _revision(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError("revision must be a full lowercase Git SHA")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser(
        "preflight", help="Check deployment admission without contacting either host"
    )
    preflight.add_argument("--requested-sha", type=_revision, required=True)
    build = commands.add_parser("source-build", help="Build an exact-revision source-only payload")
    build.add_argument("--source-root", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--requested-sha", type=_revision, required=True)
    build.add_argument("--candidate-app", type=Path, required=True)
    report = commands.add_parser(
        "verify-report", help="Validate recorded host evidence without host contact"
    )
    report.add_argument("--path", type=Path, required=True)
    report.add_argument("--deployment-id", required=True)
    report.add_argument("--requested-sha", type=_revision, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "preflight":
            payload: dict[str, object] = {
                "requested_revision": arguments.requested_sha,
                "disposition": "deferred",
                "detail": (
                    "No production resident deployment driver is wired. "
                    "Executor source closure, native runtime preparation, and "
                    "identity-bound control must be connected before either host changes."
                ),
            }
            print(json.dumps(payload, sort_keys=True))
            return 3
        if arguments.command == "source-build":
            from planner.environments.resident.packaging import (
                build_source_package,
                package_from_dict,
            )

            manifest = json.loads(arguments.manifest.read_bytes())
            if not isinstance(manifest, dict):
                raise ResidentError("package manifest must be an object")
            package = package_from_dict(manifest)
            if package.application_revision != arguments.requested_sha:
                raise ResidentError("package revision does not match requested SHA")
            app = build_source_package(arguments.source_root, package, arguments.candidate_app)
            print(
                json.dumps(
                    {
                        "requested_revision": package.application_revision,
                        "disposition": "prepared",
                        "candidate_app": str(app),
                        "detail": (
                            "Source payload only. Native runtime and installed service "
                            "remain unproved."
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if arguments.command == "verify-report":
            from planner.environments.resident.report import read_deployment_record

            record = read_deployment_record(arguments.path)
            if (
                record.deployment_id != arguments.deployment_id
                or record.result.requested_revision != arguments.requested_sha
            ):
                raise ResidentError(
                    "report does not match the requested deployment identity and SHA"
                )
            print(json.dumps(asdict(record), sort_keys=True))
            return 0 if record.result.verified else 3
        raise ResidentError("unsupported resident command")
    except (ResidentError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
