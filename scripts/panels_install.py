#!/usr/bin/env python3
"""Install the server and machine code, or join an extra machine, without a source checkout.

This bootstrap uses Python's standard library and the authenticated GitHub CLI.
"""

from __future__ import annotations

import sys

# The Python that comes with macOS is 3.9. Refuse before an import that needs 3.11.
if sys.version_info < (3, 11):
    raise SystemExit(
        "Panels setup refused: Python 3.11 or newer is required. "
        "On a Mac, install it with Homebrew (brew install python), then run the command again."
    )

import argparse
import ctypes
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from http.client import HTTPMessage
from pathlib import Path
from types import FrameType
from typing import IO, Any

def _run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=20)


def host_target() -> str:
    pair = (platform.system(), platform.machine())
    if pair == ("Darwin", "arm64"):
        return "darwin"
    if pair == ("Linux", "x86_64"):
        return "linux"
    raise ValueError("Use an Apple Silicon Mac or a Linux x86_64 machine.")


def preflight(args: argparse.Namespace, target: str) -> None:
    """Refuse host and namespace conflicts before downloads or installation writes."""
    if not hasattr(tarfile, "data_filter"):
        raise ValueError(
            "Install Python 3.11.8 or newer, then run this command with that interpreter."
        )
    root = args.root
    if any(ord(character) < 32 or ord(character) == 127 for character in str(root)):
        raise ValueError("The installation root must not contain control characters.")
    if root.exists() or root.is_symlink():
        if args.command == "join" and target == "darwin":
            raise ValueError(f"This machine already has Panels machine code: {root}")
        raise ValueError(f"The installation root already exists: {root}. Select a new --root.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.service):
        raise ValueError("Use a service name with letters, numbers, dots, and hyphens.")
    if not 1 <= args.port <= 65535:
        raise ValueError("Use a --port from 1 through 65535.")
    if args.command == "home":
        command_link = Path.home() / ".local/bin/panels"
        if command_link.exists() or command_link.is_symlink():
            raise ValueError(
                f"The panels command already exists: {command_link}. It will not be replaced."
            )
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError as error:
                raise ValueError(f"Port {args.port} is busy. Select an unused --port.") from error
    # An Apple Silicon join runs on the Node inside the machine code release.
    if not (args.command == "join" and target == "darwin"):
        node = "/opt/homebrew/bin/node" if target == "darwin" else shutil.which("node")
        if node is None or not Path(node).is_file():
            raise ValueError(
                "Install Node.js 22 or newer before installation. "
                "On Mac use /opt/homebrew/bin/node."
            )
        version = _run(node, "--version").stdout.strip()
        node_version = re.fullmatch(r"v(\d+)\.\d+\.\d+", version)
        if node_version is None or int(node_version.group(1)) < 22:
            raise ValueError(f"Install Node.js 22 or newer. The selected Node reports {version}.")
        if args.command == "join":
            require_linux_join_node(version)
    if target == "darwin":
        if int(platform.mac_ver()[0].split(".")[0]) < 15:
            raise ValueError("Update this Mac to macOS 15 or newer.")
        domain = f"gui/{os.getuid()}"
        suffixes = (
            ("",)
            if args.command == "join"
            else ("", ".backup", ".maintenance", ".stall-watchdog")
        )
        labels = tuple(args.service + suffix for suffix in suffixes)
        for label in labels:
            path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
            # A join has one fixed job, so the person cannot choose another name.
            remedy = (
                " Remove the earlier Panels machine code first."
                if args.command == "join"
                else " Select another --service."
            )
            if path.exists() or path.is_symlink():
                raise ValueError(f"The LaunchAgent file already exists: {path}." + remedy)
            result = _run("/bin/launchctl", "print", f"{domain}/{label}", check=False)
            if result.returncode == 0:
                raise ValueError(f"The LaunchAgent already exists: {label}." + remedy)
            if "Could not find service" not in result.stderr:
                raise ValueError("Log in to the Mac graphical session before installation.")
    else:
        if shutil.which("systemctl") is None:
            raise ValueError(
                "Install systemd and enable a user service manager before installation."
            )
        if args.command == "home":
            linger = _run(
                "loginctl",
                "show-user",
                str(os.getuid()),
                "--property=Linger",
                "--value",
                check=False,
            )
            if linger.returncode != 0 or linger.stdout.strip() != "yes":
                raise ValueError(
                    'Enable user lingering before installation: loginctl enable-linger "$USER"'
                )
        _run("systemctl", "--user", "show-environment")
        services = (
            [args.service]
            if args.command == "join"
            else [
                *(
                    args.service + suffix
                    for suffix in (
                        ".service",
                        ".backup.service",
                        ".backup.timer",
                        ".maintenance.service",
                        ".maintenance.timer",
                        ".stall-watchdog.service",
                        ".stall-watchdog.timer",
                    )
                ),
            ]
        )
        for service in services:
            unit = Path.home() / ".config/systemd/user" / service
            if unit.exists() or unit.is_symlink():
                raise ValueError(
                    f"The service file already exists: {unit}. It will not be replaced."
                )
            result = _run(
                "systemctl",
                "--user",
                "show",
                service,
                "--property=LoadState",
                "--value",
                check=False,
            )
            if result.stdout.strip() != "not-found":
                raise ValueError(f"The service already exists or cannot be inspected: {service}.")


# --- Release source --------------------------------------------------------------------
# The only part of this script that knows where releases come from: GitHub Releases
# of one repository, read with the GitHub CLI login. The rest of the script
# uses find_release(), download_release() and release_update_token(). Another source
# replaces this section and nothing else.

REPOSITORY = "khushal-jagota/headroom"
PUBLICATION_WORKFLOW = "publish-typescript-release.yml"


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub's artifact redirect must not disclose its token to blob storage."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def _github_token() -> str:
    return _run("gh", "auth", "token").stdout.strip()


def _api(
    path: str, destination: Path | None = None, accept: str = "application/vnd.github+json"
) -> Any:
    token = _github_token()
    headers = {"Accept": accept, "User-Agent": "panels-install"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}", headers=headers
    )
    opener = urllib.request.build_opener(DownloadRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            if destination is None:
                return json.load(response)
            with destination.open("xb") as stream:
                shutil.copyfileobj(response, stream, 1024 * 1024)
    except urllib.error.HTTPError as error:
        raise ValueError(
            f"GitHub refused release access ({error.code}). Check the GitHub CLI login."
        ) from None
    return None


def release_update_token() -> str:
    """The credential the installed home reads its updates with. It never enters argv or output."""
    token = _github_token()
    if not token:
        raise ValueError("Log in to the GitHub CLI before installation: gh auth login")
    return token


def find_release(target: str, command: str, revision: str | None, work: Path) -> dict[str, Any]:
    """The newest permanent trusted server release for this target and command."""
    page = 1
    while True:
        releases = _api(f"releases?per_page=100&page={page}")
        for release in releases:
            tag = release.get("tag_name", "")
            if release.get("draft") or release.get("prerelease") or not tag.startswith("server-"):
                continue
            if revision is not None and release.get("target_commitish") != revision:
                continue
            assets = {item["name"]: item for item in release.get("assets", [])}
            if "publication.json" not in assets:
                continue
            original = _api(
                f"releases/assets/{assets['publication.json']['id']}",
                accept="application/octet-stream",
            )
            source = original.get("source_run", {})
            sha = source.get("source_sha")
            run_id, attempt = source.get("run_id"), source.get("run_attempt")
            workflow_path = f".github/workflows/{PUBLICATION_WORKFLOW}"
            if (
                source.get("repository") != REPOSITORY
                or source.get("event") != "push"
                or source.get("ref") != "refs/heads/main"
                or source.get("workflow_path") != workflow_path
                or source.get("workflow_ref") != f"{REPOSITORY}/{workflow_path}@refs/heads/main"
                or not isinstance(sha, str)
                or re.fullmatch(r"[0-9a-f]{40}", sha) is None
                or not isinstance(run_id, str)
                or re.fullmatch(r"[1-9][0-9]*", run_id) is None
                or not isinstance(attempt, str)
                or re.fullmatch(r"[1-9][0-9]*", attempt) is None
                or tag != f"server-{sha}-{run_id}-{attempt}"
                or release.get("target_commitish") != sha
                or original.get("publication_run_id") != int(run_id)
                or original.get("artifact_source") != "github-release"
                or "expires_at" not in original
                or original["expires_at"] is not None
            ):
                raise ValueError("The release identity differs from its trusted publication.")
            asset_name = (
                ("mac.zip" if target == "darwin" else "linux-executor.zip")
                if command == "join"
                else "mac-home.zip" if target == "darwin" else "app.zip"
            )
            receipt = select_receipt(original, target, command)
            asset = assets.get(asset_name, {})
            if (
                receipt["revision"] != sha
                or receipt.get("artifact_source") != "github-release"
                or not isinstance(receipt.get("artifact_id"), int)
                or isinstance(receipt["artifact_id"], bool)
                or receipt["artifact_id"] <= 0
                or receipt["artifact_id"] != asset.get("id")
            ):
                raise ValueError("The release identity differs from its trusted publication.")
            return receipt
        if len(releases) < 100:
            break
        page += 1
    raise ValueError("No supported release exists. Publish a checked release before installation.")


def download_release(receipt: dict[str, Any], work: Path) -> Path:
    """Fetch the sealed archive that a receipt from find_release() names."""
    artifact_zip = work / "artifact.zip"
    _api(
        f"releases/assets/{receipt['artifact_id']}",
        artifact_zip,
        accept="application/octet-stream",
    )
    with zipfile.ZipFile(artifact_zip) as zipped:
        candidates = [name for name in zipped.namelist() if name.endswith(".tar.gz")]
        if len(candidates) != 1:
            raise ValueError("The release must contain exactly one sealed archive.")
        archive = work / "runtime.tar.gz"
        with zipped.open(candidates[0]) as source, archive.open("xb") as stream:
            shutil.copyfileobj(source, stream, 1024 * 1024)
    return archive


# --- End of release source ---------------------------------------------------------------


def select_publication(
    args: argparse.Namespace,
    target: str,
    work: Path,
) -> tuple[Path, dict[str, Any]]:
    """A supplied archive and receipt, or the newest release from the release source."""
    if args.archive is not None:
        if args.receipt is None:
            raise ValueError("Supply --receipt with --archive.")
        receipt = json.loads(args.receipt.read_text())
        return args.archive, select_receipt(receipt, target, args.command)
    if args.receipt is not None:
        raise ValueError("Supply --archive with --receipt.")
    receipt = find_release(target, args.command, args.revision, work)
    return download_release(receipt, work), receipt


def select_receipt(receipt: dict[str, Any], target: str, command: str) -> dict[str, Any]:
    if command == "join":
        selected = receipt.get("mac" if target == "darwin" else "linux_executor", receipt)
        revision = receipt.get("manifest", {}).get("app_sha") or receipt.get("application_revision")
    else:
        selected = receipt.get("mac_home", receipt) if target == "darwin" else receipt
        manifest = selected.get("manifest", {})
        revision = manifest.get("app_sha")
        contract = manifest.get("runtime_contract", {})
        if (contract.get("system"), contract.get("architecture")) != (
            target,
            "arm64" if target == "darwin" else "x86_64",
        ):
            raise ValueError("The release target differs from this machine.")
        if contract.get("version") != 2:
            raise ValueError("This bootstrap needs a sealed Node home release.")
        if target == "darwin" and contract.get("role") != "home":
            raise ValueError("The release must be a Darwin home artifact.")
    if not isinstance(selected, dict) or not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise ValueError("The publication receipt has no exact application revision.")
    digest = selected.get("archive_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("The publication receipt has no valid archive SHA256.")
    return {**selected, "revision": revision}


def extract_bootstrap(archive: Path, destination: Path, digest: str) -> None:
    actual = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(chunk)
    if actual.hexdigest() != digest:
        raise ValueError("The release checksum differs from its publication. Download it again.")
    with tarfile.open(archive, "r:gz") as packed:
        members = packed.getmembers()
        names: set[str] = set()
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.name in names
                or member.mode & ~0o777
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
            ):
                raise ValueError("The release contains unsafe or duplicate archive entries.")
            names.add(member.name)

        def safe_member(member: tarfile.TarInfo, root: str) -> tarfile.TarInfo | None:
            checked = tarfile.data_filter(member, root)
            if checked is not None:
                checked.mode = member.mode
            return checked

        packed.extractall(destination, members=members, filter=safe_member)


def check_linux_contract(payload: Path) -> None:
    """Check loader prerequisites before the sealed runtime can start."""
    contract = json.loads((payload / "manifest.json").read_text())["runtime_contract"]
    release = platform.freedesktop_os_release()
    supported = next(
        (
            item
            for item in contract["platforms"]
            if (item["distribution"], item["version"])
            == (release.get("ID"), release.get("VERSION_ID"))
        ),
        None,
    )
    if supported is None:
        names = ", ".join(
            item["distribution"] + " " + item["version"] for item in contract["platforms"]
        )
        raise ValueError("Use a supported Linux distribution: " + names + ".")
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split("."))) < tuple(
        map(int, supported["minimum_glibc"].split("."))
    ):
        raise ValueError("This release requires glibc " + supported["minimum_glibc"] + " or newer.")
    feature_sets = [
        set(line.split(":", 1)[1].split())
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("flags")
    ]
    if not feature_sets or any(
        not set(contract["cpu_features"]) <= flags for flags in feature_sets
    ):
        raise ValueError("This processor lacks CPU features required by the published runtime.")
    for library in contract["external_libraries"]:
        try:
            ctypes.CDLL(library)
        except OSError as error:
            raise ValueError("Install the host runtime library " + library + ".") from error
    for executable in contract["external_executables"]:
        if shutil.which(executable, path="/usr/local/bin:/usr/bin:/bin") is None:
            raise ValueError("Install the host executable " + executable + ".")


def require_linux_join_node(version: str) -> None:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version.strip())
    if (
        match is None
        or int(match.group(1)) != 22
        or (int(match.group(2)), int(match.group(3))) < (18, 0)
    ):
        raise ValueError("Linux executor join requires host Node 22 >=22.18.0.")


def linux_executor_node(payload: Path, revision: str) -> Path:
    """Bind the native executor target before any downloaded helper executes."""
    manifest = json.loads((payload / "manifest.json").read_text())
    contract = manifest.get("runtime_contract") if isinstance(manifest, dict) else None
    if (
        not isinstance(contract, dict)
        or type(contract.get("version")) is not int
        or contract["version"] != 2
        or (contract.get("system"), contract.get("architecture")) != ("linux", "x86_64")
        or contract.get("node_major") != 22
        or contract.get("minimum_node_version") != "22.18.0"
        or manifest.get("app_sha") != revision
    ):
        raise ValueError("Linux join requires a native version 2 Linux executor release.")
    marker = json.loads((payload / "mac-runtime.json").read_text())
    if (
        not isinstance(marker, dict)
        or type(marker.get("schema_version")) is not int
        or marker["schema_version"] != 1
        or (
            marker.get("platform"),
            marker.get("architecture"),
            marker.get("role"),
            marker.get("application_revision"),
        )
        != ("linux", "x86_64", "executor", revision)
    ):
        raise ValueError("Linux executor target or revision differs from the approved release.")
    found = shutil.which("node")
    if found is None:
        raise ValueError("Linux executor join requires host Node 22 >=22.18.0.")
    node = Path(found).resolve(strict=True)
    require_linux_join_node(_run(str(node), "--version").stdout)
    return node


def mac_executor_node(payload: Path, revision: str) -> Path:
    """Bind the Apple Silicon machine code and its own Node before any of it executes."""
    marker = json.loads((payload / "mac-runtime.json").read_text())
    if not isinstance(marker, dict) or marker != {
        "schema_version": 1,
        "application_revision": revision,
        "platform": "darwin",
        "architecture": "arm64",
    }:
        raise ValueError("The release is not the Apple Silicon machine code of this revision.")
    node = payload / "node/bin/node"
    if not node.is_file() or node.is_symlink():
        raise ValueError("The Apple Silicon release has no Node of its own.")
    return node


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("home", "join"))
    result.add_argument("--revision")
    result.add_argument("--archive", type=Path)
    result.add_argument("--receipt", type=Path)
    result.add_argument("--root", type=Path)
    result.add_argument(
        "--service",
        help="Home service label. Joins use their fixed machine service.",
    )
    result.add_argument("--port", type=int, default=8767)
    result.add_argument("--isolated-providers", action="store_true")
    result.add_argument("--home")
    result.add_argument("--machine-id")
    return result


def run_installer(
    command: list[str],
    payload: str,
    environment: dict[str, str],
) -> None:
    """Forward the first interruption and wait through the installer's rollback."""
    process: subprocess.Popen[str] | None = None
    interrupted: int | None = None
    forwarded = False

    def interrupt(signum: int, frame: FrameType | None) -> None:
        nonlocal interrupted, forwarded
        if interrupted is None:
            interrupted = signum
        if process is not None and not forwarded:
            forwarded = True
            try:
                process.send_signal(interrupted)
            except ProcessLookupError:
                pass

    handled = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.signal(signum, interrupt) for signum in handled}
    try:
        # A separate session prevents repeated terminal interrupts from reaching cleanup.
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
        if interrupted is not None:
            interrupt(interrupted, None)
        process.communicate(payload)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if interrupted is not None:
        raise SystemExit(128 + interrupted)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)


def main() -> None:
    args = parser().parse_args()
    try:
        target = host_target()
        if args.command == "join" and args.service is not None:
            raise ValueError("A join uses its fixed machine service. Omit --service.")
        if args.command == "join" and target == "darwin" and args.root is not None:
            raise ValueError("An Apple Silicon join uses fixed paths. Omit --root.")
        if args.command == "join" and target == "darwin" and args.isolated_providers:
            raise ValueError("Isolated provider homes are a Linux join option.")
        args.service = args.service or "com.panels.home"
        default_root = (
            Path.home() / "Library/Application Support/PanelsHome"
            if target == "darwin"
            else Path.home() / ".local/share/panels-home"
        )
        if args.command == "join":
            if not args.home or not args.machine_id:
                raise ValueError(
                    "Join needs --home URL and --machine-id from the owner's join command."
                )
            if target == "darwin":
                # The join step owns the fixed paths. Preflight checks the state folder and the job.
                default_root = (
                    Path.home() / "Library/Application Support/Panels/executor-typescript"
                )
                args.service = "com.panels.executor.typescript"
            else:
                xdg_data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
                if args.root is None and not xdg_data.is_absolute():
                    raise ValueError("Linux executor XDG_DATA_HOME must be absolute.")
                default_root = xdg_data / "panels/executor-typescript"
                args.service = "panels-executor-typescript.service"
        args.root = (args.root or default_root).expanduser().absolute()
        preflight(args, target)
        print("Host prerequisites passed. Select and check the release.", flush=True)
        with tempfile.TemporaryDirectory(prefix="panels-install-") as temporary:
            work = Path(temporary)
            archive, receipt = select_publication(args, target, work)
            if args.revision is not None and receipt["revision"] != args.revision:
                raise ValueError("The publication differs from the requested --revision.")
            archive = archive.resolve()
            payload = work / "payload"
            extract_bootstrap(archive, payload, receipt["archive_sha256"])
            linux_node: Path | None = None
            mac_node: Path | None = None
            if target == "darwin" and args.command == "join":
                mac_node = mac_executor_node(payload, receipt["revision"])
            if target == "linux":
                if args.command == "join":
                    linux_node = linux_executor_node(payload, receipt["revision"])
                check_linux_contract(payload)
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("PLAN_")
            }
            if args.command == "home":
                node = "/opt/homebrew/bin/node" if target == "darwin" else shutil.which("node")
                if node is None:
                    raise ValueError("Install Node.js 22 or newer before installation.")
                command = [node, str(payload / "server/installation/home_main.ts")]
                value = {
                    "root": str(args.root),
                    "service": args.service,
                    "port": args.port,
                    "isolated_providers": args.isolated_providers,
                    "archive": str(archive),
                    "archive_sha256": receipt["archive_sha256"],
                    "revision": receipt["revision"],
                    "update_token": release_update_token(),
                }
            elif mac_node is not None:
                command = [
                    str(mac_node),
                    str(payload / "server/environments/mac_executor/join_main.ts"),
                ]
                value = {
                    "archive": str(archive),
                    "archive_sha256": receipt["archive_sha256"],
                    "archive_size": receipt.get("archive_size", archive.stat().st_size),
                    "revision": receipt["revision"],
                    "home_url": args.home,
                    "machine_id": args.machine_id,
                    "credential": getpass.getpass("Private machine credential: "),
                    "home": str(Path.home()),
                }
                for variable in list(environment):
                    if variable in ("NODE_OPTIONS", "NODE_PATH") or variable.startswith("DYLD_"):
                        environment.pop(variable)
            else:
                assert linux_node is not None
                command = [
                    str(linux_node),
                    str(payload / "server/environments/linux_executor/join_main.ts"),
                ]
                value = {
                    "archive": str(archive),
                    "archive_sha256": receipt["archive_sha256"],
                    "archive_size": receipt.get("archive_size", archive.stat().st_size),
                    "revision": receipt["revision"],
                    "home_url": args.home,
                    "machine_id": args.machine_id,
                    "isolated_providers": args.isolated_providers,
                    "credential": getpass.getpass("Private machine credential: "),
                    "root": str(args.root),
                    "home": str(Path.home()),
                    "node_executable": str(linux_node),
                }
                for variable in ("NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
                    environment.pop(variable, None)
            print(
                "Release checksum passed. Install the server and machine code."
                if args.command == "home"
                else "Release checksum passed. Install the machine code.",
                flush=True,
            )
            run_installer(
                command,
                json.dumps(value),
                environment,
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        # Child stderr contains the specific refusal; process arguments contain no secret.
        if isinstance(error, subprocess.CalledProcessError):
            detail = (
                error.stderr.strip()
                if error.stderr
                else "The release installer refused the operation. Read its error above."
            )
        else:
            detail = str(error)
        raise SystemExit("Panels setup refused: " + detail) from None


if __name__ == "__main__":
    main()
