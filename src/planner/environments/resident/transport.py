"""Pinned-host, bounded SSH delivery of exact source and deployment tooling."""

from __future__ import annotations

import base64
import io
import json
import re
import shlex
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import cast

from planner.environments.resident.contracts import (
    ExecutorCompatibilityVersions,
    ExecutorLaunchOptions,
    HostDeploymentResult,
    HostDisposition,
    InstalledExecutorCompatibility,
    ResidentError,
)
from planner.environments.resident.logic.native_diagnostic import parse_native_diagnostic
from planner.environments.resident.logic.upgrade import require_supported_upgrade
from planner.environments.resident.packaging import build_source_package, package_from_dict
from planner.environments.resident.preflight import (
    inspect_installed_vps,
    mac_admission_source,
    mac_probe_command_source,
    package_versions,
    parse_mac_identity,
    requested_versions,
    supported_versions,
)
from planner.environments.resident.report import MAX_REPORT_BYTES

# Tooling is separate from installed executor code. This explicit closure imports
# shared immutable types but excludes server, database and web implementations.
_TOOL_SHARED = (
    "src/planner/__init__.py",
    "src/planner/environments/__init__.py",
    "src/planner/environments/app.py",
    "src/planner/executor/__init__.py",
    "src/planner/executor/contracts.py",
    "src/planner/executor/configuration.py",
    "src/planner/executor/files.py",
    "src/planner/executor/legacy_preflight.py",
    "src/planner/executor/outbox_storage.py",
    "src/planner/executor/preflight.py",
    "src/planner/conversation/__init__.py",
    "src/planner/conversation/backends/__init__.py",
    "src/planner/conversation/backends/contracts.py",
    "src/planner/conversation/contracts.py",
    "src/planner/conversation/in_memory_conversation_system.py",
    "src/planner/conversation/logic/__init__.py",
    "src/planner/conversation/logic/addressed_reply.py",
    "src/planner/conversation/logic/conversation_start_resolution.py",
    "src/planner/conversation/logic/held_line.py",
    "src/planner/conversation/events.py",
    "src/planner/conversation/message_content.py",
    "src/planner/conversation/message_files.py",
    "src/planner/core/__init__.py",
    "src/planner/core/contracts.py",
    "src/planner/files/__init__.py",
    "src/planner/files/contracts.py",
    "src/planner/files/logic/__init__.py",
    "src/planner/files/logic/paths.py",
)

# No shell expansion, tar extraction API, existing Mac git checkout or Panels
# installation is trusted. A fresh owner-only staging directory retains evidence.
_REMOTE_BOOTSTRAP = """import base64,json,os,pathlib,sys,tarfile,tempfile
cfg=json.loads(base64.b64decode(sys.argv[1]))
home=pathlib.Path.home().absolute()
root=home/'Deployments/PanelsExecutor/staging'
for p in (root,*root.parents):
 if p.is_symlink(): raise RuntimeError('symlink in staging root')
root.mkdir(parents=True,exist_ok=True,mode=0o700)
if root.stat().st_uid != os.getuid(): raise RuntimeError('staging owner mismatch')
stage=pathlib.Path(tempfile.mkdtemp(prefix='deploy-',dir=root))
total=0;names=set()
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
 for member in archive:
  name=pathlib.PurePosixPath(member.name)
  if (not member.isfile() or name.is_absolute() or '..' in name.parts
      or str(name)!=member.name or name.parts[0] not in ('app','tools')
      or member.name in names): raise RuntimeError('unsafe deployment archive')
  names.add(member.name);total+=member.size
  if total>134217728 or len(names)>10000: raise RuntimeError('deployment archive too large')
  target=stage/member.name;target.parent.mkdir(parents=True,exist_ok=True)
  stream=archive.extractfile(member)
  with target.open('xb') as output:
   while chunk:=stream.read(65536): output.write(chunk)
sys.path.insert(0,str(stage/'tools/src'))
from planner.environments.resident.cli import main
args=['deploy-prepared','--candidate-app',str(stage/'app'),'--home',str(home),*cfg]
raise SystemExit(main(args))
"""


def parse_host_result(output: str, revision: str) -> HostDeploymentResult:
    if len(output.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ResidentError("native host report is too large")
    try:
        data = json.loads(output)
        if not isinstance(data, dict) or set(data) != {
            "host",
            "requested_revision",
            "disposition",
            "observed_revision",
            "detail",
        }:
            raise ValueError("invalid host report")
        disposition = data["disposition"]
        observed, detail = data["observed_revision"], data["detail"]
        if (
            data["host"] != "mac"
            or data["requested_revision"] != revision
            or disposition not in {"verified", "unreachable", "deferred", "failed"}
            or (
                observed is not None
                and (
                    not isinstance(observed, str) or re.fullmatch(r"[0-9a-f]{40}", observed) is None
                )
            )
            or (disposition == "verified" and observed != revision)
            or not isinstance(detail, str)
            or not 0 < len(detail) <= 500
            or not detail.isprintable()
        ):
            raise ValueError("mismatched host report")
        diagnostic = parse_native_diagnostic(detail)
        if diagnostic is not None and (disposition != "failed" or observed is not None):
            raise ValueError("native diagnostic conflicts with host evidence")
        return HostDeploymentResult(
            "mac", revision, cast(HostDisposition, disposition), observed, detail
        )
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ResidentError("native host returned no valid exact-revision report") from exc


def _regular_owner_file(path: Path, *, secret: bool) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ResidentError("SSH credential paths must be absolute and contain no symlinks")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ResidentError("SSH configuration must name regular files")
    if secret and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ResidentError("SSH identity must be owner-only")


def validate_remote_configuration(
    *,
    host: str,
    known_hosts: Path,
    identity: Path,
    python: Path,
    timeout_seconds: float,
    launch: ExecutorLaunchOptions,
) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) is None or host.startswith("-"):
        raise ResidentError("invalid SSH host")
    _regular_owner_file(known_hosts, secret=False)
    _regular_owner_file(identity, secret=True)
    if not known_hosts.read_text().strip():
        raise ResidentError("pinned known_hosts file is empty")
    if not python.is_absolute() or any(ord(c) < 32 for c in str(python)):
        raise ResidentError("native Python path must be an absolute path")
    if not 0 < timeout_seconds <= 3600:
        raise ResidentError("remote deployment deadline must be between zero and one hour")
    if launch.backend_path is not None and (
        any(not part or not Path(part).is_absolute() for part in launch.backend_path.split(":"))
        or any(ord(char) < 32 for char in launch.backend_path)
    ):
        raise ResidentError("backend PATH entries must be concrete absolute paths")
    from planner.environments.resident.launch_agent import validate_executor_launch

    validate_executor_launch(launch, check_files=False)


def build_delivery_archive(source: Path, manifest: Path, revision: str) -> bytes:
    spec = json.loads(manifest.read_bytes())
    package = package_from_dict(spec)
    if package.application_revision != revision:
        raise ResidentError("executor artifact specification does not match requested SHA")
    with tempfile.TemporaryDirectory(prefix="panels-resident-delivery-") as directory:
        app = build_source_package(source, package, Path(directory) / "app")
        # Deployment tools come from this executing checkout, independently of
        # the exact requested app payload (which can be an earlier same-protocol SHA).
        tool_source = Path(__file__).resolve().parents[4]
        tools = tuple(
            sorted(
                set(_TOOL_SHARED)
                | {
                    path.relative_to(tool_source).as_posix()
                    for path in (tool_source / "src/planner/environments/resident").rglob("*.py")
                }
            )
        )
        output = io.BytesIO()
        found: set[str] = set()
        with tarfile.open(fileobj=output, mode="w") as bundle:
            for name in tools:
                path = tool_source / name
                if path.is_symlink() or not path.is_file():
                    raise ResidentError("deployment tooling contains nonregular files")
                found.add(name)
                bundle.add(path, arcname="tools/" + name, recursive=False)
            if found != set(tools) or not any(name.endswith("/driver.py") for name in found):
                raise ResidentError("Executing deployment tooling closure is incomplete")
            for path in sorted(app.rglob("*")):
                if path.is_file():
                    bundle.add(
                        path, arcname="app/" + path.relative_to(app).as_posix(), recursive=False
                    )
        return output.getvalue()


class SshResidentDeployment:
    """One explicit authenticated delivery; an unreachable host is never queued."""

    def __init__(
        self,
        *,
        source: Path,
        manifest: Path,
        host: str,
        known_hosts: Path,
        identity: Path,
        python: Path,
        launch: ExecutorLaunchOptions,
        timeout_seconds: float,
        npm: Path | None = None,
        node: Path | None = None,
    ) -> None:
        validate_remote_configuration(
            host=host,
            known_hosts=known_hosts,
            identity=identity,
            python=python,
            timeout_seconds=timeout_seconds,
            launch=launch,
        )
        self.source, self.manifest = source, manifest
        self.host, self.known_hosts, self.identity = host, known_hosts, identity
        package = package_from_dict(json.loads(manifest.read_bytes()))
        if package.closure.node_package_roots and (npm is None or node is None):
            raise ResidentError("native node and npm paths required for executor backend closure")
        for executable in (npm, node):
            if executable is not None and not executable.is_absolute():
                raise ResidentError("native node and npm paths must be absolute")
        self.npm, self.node = npm, node
        self.python, self.launch, self.timeout_seconds = python, launch, timeout_seconds

    def _ssh(self, command: str) -> list[str]:
        return [
            "ssh",
            "-F",
            "/dev/null",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=2",
            "-i",
            str(self.identity),
            "--",
            self.host,
            command,
        ]

    def inspect_installed(self) -> InstalledExecutorCompatibility:
        command = shlex.join([str(self.python), "-I", "-B", "-c", mac_probe_command_source()])
        try:
            result = subprocess.run(
                self._ssh(command),
                capture_output=True,
                timeout=min(self.timeout_seconds, 60.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ResidentError("Mac read-only installation inspection failed") from exc
        if result.returncode != 0:
            raise ResidentError("Mac read-only installation inspection refused")
        return parse_mac_identity(result.stdout)

    def _candidate_versions(self, requested_revision: str) -> ExecutorCompatibilityVersions:
        package = package_from_dict(json.loads(self.manifest.read_bytes()))
        if (
            requested_revision != self.launch.application_revision
            or package.application_revision != requested_revision
        ):
            raise ResidentError("candidate/launch revision differs from requested deployment")
        versions = requested_versions(self.source, requested_revision)
        if versions != package_versions(package):
            raise ResidentError("candidate manifest differs from exact requested source versions")
        require_supported_upgrade(versions, (), supported_versions())
        return versions

    def preflight(self, requested_revision: str, *, vps_app: Path) -> None:
        versions = self._candidate_versions(requested_revision)
        vps = inspect_installed_vps(vps_app)
        mac = self.inspect_installed()
        require_supported_upgrade(versions, (vps, mac), supported_versions())

    def deploy(self, requested_revision: str) -> HostDeploymentResult:
        if requested_revision != self.launch.application_revision:
            raise ResidentError("transport launch revision differs from requested deployment")
        versions = self._candidate_versions(requested_revision)
        require_supported_upgrade(versions, (self.inspect_installed(),), supported_versions())
        archive = build_delivery_archive(self.source, self.manifest, requested_revision)
        arguments = [
            "--python",
            str(self.python),
            "--requested-sha",
            requested_revision,
            "--executor-ssh-host",
            self.launch.ssh_host,
            "--executor-ssh-port",
            str(self.launch.ssh_port),
            "--executor-ssh-user",
            self.launch.ssh_user,
            "--executor-ssh-identity-file",
            str(self.launch.ssh_identity_file),
            "--executor-ssh-known-hosts",
            str(self.launch.ssh_known_hosts_file),
            "--panels-server-url",
            self.launch.panels_server_url,
            "--relay-command",
            self.launch.remote_command,
            "--timeout-seconds",
            str(min(self.timeout_seconds, 60.0)),
        ]
        if self.launch.backend_path is not None:
            arguments += ["--backend-path", self.launch.backend_path]
        if self.npm is not None:
            arguments += ["--npm", str(self.npm)]
        if self.node is not None:
            arguments += ["--node", str(self.node)]
        encoded = base64.b64encode(json.dumps(arguments).encode()).decode()
        command = shlex.join(
            [
                str(self.python),
                "-I",
                "-B",
                "-c",
                mac_admission_source(versions) + "\n" + _REMOTE_BOOTSTRAP,
                encoded,
            ]
        )
        ssh = self._ssh(command)
        try:
            completed = subprocess.run(
                ssh,
                input=archive,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return HostDeploymentResult(
                "mac",
                requested_revision,
                "unreachable",
                None,
                (
                    "Mac deadline expired; remote outcome unknown; "
                    "requested revision unverified; no retry queued"
                ),
            )
        except OSError as exc:
            raise ResidentError("SSH transport could not start") from exc
        if completed.returncode == 255:
            return HostDeploymentResult(
                "mac",
                requested_revision,
                "unreachable",
                None,
                (
                    "Mac SSH connection lost or unavailable; remote outcome unknown; "
                    "requested revision unverified"
                ),
            )
        result = parse_host_result(completed.stdout.decode(), requested_revision)
        if completed.returncode != 0 and result.disposition == "verified":
            raise ResidentError("native success report conflicts with command failure")
        return result
