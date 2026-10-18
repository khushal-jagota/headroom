"""Explicit resident deployment commands; preparation alone never installs a service."""

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
    configure = commands.add_parser(
        "configure-vps-launch", help="Write explicit VPS launch settings"
    )
    configure.add_argument("--path", type=Path, required=True)
    configure.add_argument("--mode", choices=["local", "remote"], required=True)
    configure.add_argument("--socket", type=Path)
    spec = commands.add_parser(
        "package-spec", help="Resolve pinned executor closure at requested SHA"
    )
    spec.add_argument("--source-root", type=Path, required=True)
    spec.add_argument("--requested-sha", type=_revision, required=True)
    spec.add_argument("--build-contract", type=Path, required=True)
    spec.add_argument("--target-architecture", required=True)
    spec.add_argument("--python-version", required=True)
    spec.add_argument("--output", type=Path, required=True)
    for name in ("deploy-local", "deploy-prepared", "activate"):
        command = commands.add_parser(name, help="Explicit native Mac resident deployment")
        command.add_argument("--candidate-app", type=Path, required=True)
        command.add_argument("--home", type=Path, default=Path.home().absolute())
        command.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
        command.add_argument("--npm", type=Path)
        command.add_argument("--node", type=Path)
        command.add_argument("--requested-sha", type=_revision, required=True)
        command.add_argument("--executor-ssh-host", required=True)
        command.add_argument("--panels-server-url", required=True)
        command.add_argument("--relay-command", default="panels executor relay")
        command.add_argument("--backend-path")
        command.add_argument("--timeout-seconds", type=float, default=30.0)
        if name == "deploy-local":
            command.add_argument("--source-root", type=Path, required=True)
            command.add_argument("--manifest", type=Path, required=True)
    remote = commands.add_parser("deploy-remote", help="Deliver exact source to the configured Mac")
    for command in (remote, preflight):
        command.add_argument("--source-root", type=Path)
        command.add_argument("--manifest", type=Path)
        command.add_argument("--ssh-host")
        command.add_argument("--known-hosts", type=Path)
        command.add_argument("--identity", type=Path)
        command.add_argument("--python", type=Path)
        command.add_argument("--npm", type=Path)
        command.add_argument("--node", type=Path)
        command.add_argument("--executor-ssh-host")
        command.add_argument("--panels-server-url")
        command.add_argument("--relay-command", default="panels executor relay")
        command.add_argument("--backend-path")
        command.add_argument("--timeout-seconds", type=float, default=1200.0)
    remote.add_argument("--requested-sha", type=_revision, required=True)
    remote.add_argument("--vps-health-url", required=True)
    remote.add_argument("--report", type=Path, required=True)
    remote.add_argument("--deployment-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "configure-vps-launch":
            from planner.environments.resident.contracts import VpsExecutorLaunchConfiguration
            from planner.environments.resident.vps_launch import write_vps_launch

            write_vps_launch(
                arguments.path,
                VpsExecutorLaunchConfiguration(arguments.mode, arguments.socket),
            )
            print(json.dumps({"disposition": "prepared", "mode": arguments.mode}))
            return 0
        if arguments.command == "package-spec":
            from planner.environments.resident.driver import write_package_spec

            write_package_spec(
                arguments.source_root,
                arguments.requested_sha,
                arguments.build_contract,
                arguments.target_architecture,
                arguments.python_version,
                arguments.output,
            )
            print(json.dumps({"disposition": "prepared", "manifest": str(arguments.output)}))
            return 0
        if arguments.command in {"preflight", "deploy-remote"}:
            required = (
                "source_root",
                "manifest",
                "ssh_host",
                "known_hosts",
                "identity",
                "python",
                "executor_ssh_host",
                "panels_server_url",
            )
            if any(getattr(arguments, name) is None for name in required):
                if arguments.command != "preflight":
                    raise ResidentError(
                        "remote deployment requires complete explicit configuration"
                    )
                print(
                    json.dumps(
                        {
                            "requested_revision": arguments.requested_sha,
                            "disposition": "deferred",
                            "detail": (
                                "Explicit executor artifact and authenticated "
                                "native host configuration required"
                            ),
                        },
                        sort_keys=True,
                    )
                )
                return 3
            from planner.environments.resident.contracts import ExecutorLaunchOptions
            from planner.environments.resident.transport import (
                SshResidentDeployment,
                build_delivery_archive,
            )

            launch = ExecutorLaunchOptions(
                arguments.executor_ssh_host,
                arguments.requested_sha,
                arguments.panels_server_url,
                arguments.relay_command,
                arguments.backend_path,
            )
            adapter = SshResidentDeployment(
                source=arguments.source_root,
                manifest=arguments.manifest,
                host=arguments.ssh_host,
                known_hosts=arguments.known_hosts,
                identity=arguments.identity,
                python=arguments.python,
                launch=launch,
                timeout_seconds=arguments.timeout_seconds,
                npm=arguments.npm,
                node=arguments.node,
            )
            if arguments.command == "preflight":
                build_delivery_archive(
                    arguments.source_root, arguments.manifest, arguments.requested_sha
                )
                print(
                    json.dumps(
                        {
                            "requested_revision": arguments.requested_sha,
                            "disposition": "prepared",
                            "detail": (
                                "Exact source and authenticated native deployment "
                                "configuration admitted"
                            ),
                        },
                        sort_keys=True,
                    )
                )
                return 0
            from planner.environments.resident.driver import deploy_and_record_remote

            record = deploy_and_record_remote(
                adapter,
                arguments.requested_sha,
                arguments.vps_health_url,
                arguments.report,
                arguments.deployment_id,
            )
            print(json.dumps(asdict(record), sort_keys=True))
            return 0 if record.result.verified else 3
        if arguments.command in {"deploy-local", "deploy-prepared", "activate"}:
            from planner.environments.resident.contracts import ExecutorLaunchOptions
            from planner.environments.resident.driver import (
                activate,
                deploy_local_source,
                prepare_and_activate,
                print_host_result,
            )

            launch = ExecutorLaunchOptions(
                arguments.executor_ssh_host,
                arguments.requested_sha,
                arguments.panels_server_url,
                arguments.relay_command,
                arguments.backend_path,
            )
            if not 0 < arguments.timeout_seconds <= 3600:
                raise ResidentError("native control deadline must be between zero and one hour")
            common = dict(
                home=arguments.home, launch=launch, timeout_seconds=arguments.timeout_seconds
            )
            if arguments.command == "activate":
                result = activate(arguments.candidate_app, **common)
            elif arguments.command == "deploy-prepared":
                result = prepare_and_activate(
                    arguments.candidate_app,
                    python=arguments.python,
                    npm=arguments.npm,
                    node=arguments.node,
                    **common,
                )
            else:
                result = deploy_local_source(
                    arguments.source_root,
                    arguments.manifest,
                    arguments.candidate_app,
                    python=arguments.python,
                    npm=arguments.npm,
                    node=arguments.node,
                    **common,
                )
            return print_host_result(result)
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
