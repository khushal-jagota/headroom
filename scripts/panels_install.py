#!/usr/bin/env python3
"""Install a sealed Panels home or join an extra machine without a source checkout.

This bootstrap uses only Python's standard library. Repository tokens stay in files.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any


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
    if sys.version_info < (3, 11) or not hasattr(tarfile, "data_filter"):
        raise ValueError(
            "Install Python 3.11.8 or newer, then run this command with that interpreter."
        )
    root = args.root
    if root.exists() or root.is_symlink():
        raise ValueError(f"The installation root already exists: {root}. Select a new --root.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.service):
        raise ValueError("Use a service name with letters, numbers, dots, and hyphens.")
    if not 1 <= args.port <= 65535:
        raise ValueError("Use a --port from 1 through 65535.")
    if args.command == "home":
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError as error:
                raise ValueError(f"Port {args.port} is busy. Select an unused --port.") from error
    node = "/opt/homebrew/bin/node" if target == "darwin" else shutil.which("node")
    if node is None or not Path(node).is_file():
        raise ValueError(
            "Install Node.js 22 before installation. On Mac use /opt/homebrew/bin/node."
        )
    version = _run(node, "--version").stdout.strip()
    if re.fullmatch(r"v22\.\d+\.\d+", version) is None:
        raise ValueError(f"Install Node.js 22. The selected Node reports {version}.")
    if target == "darwin":
        if int(platform.mac_ver()[0].split(".")[0]) < 15:
            raise ValueError("Update this Mac to macOS 15 or newer.")
        domain = f"gui/{os.getuid()}"
        labels = (
            (args.service, args.service + ".backup") if args.command == "home" else (args.service,)
        )
        for label in labels:
            path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
            if path.exists() or path.is_symlink():
                raise ValueError(
                    f"The LaunchAgent file already exists: {path}. Select another --service."
                )
            result = _run("/bin/launchctl", "print", f"{domain}/{label}", check=False)
            if result.returncode == 0:
                raise ValueError(
                    f"The LaunchAgent already exists: {label}. Select another --service."
                )
            if "Could not find service" not in result.stderr:
                raise ValueError("Log in to the Mac graphical session before installation.")
        if args.command == "home":
            interpreter = args.installer_python.resolve(strict=True)
            if interpreter.is_relative_to(root) or not os.access(interpreter, os.X_OK):
                raise ValueError(
                    "Select an executable --installer-python outside the installation root."
                )
            result = _run(
                str(interpreter),
                "-I",
                "-S",
                "-B",
                "-c",
                "import sys; raise SystemExit(sys.version_info < (3, 11))",
                check=False,
            )
            if result.returncode:
                raise ValueError("Install an external Python 3.11 or newer for Mac updates.")
        else:
            if shutil.which("xcrun") is None:
                raise ValueError(
                    "Install the Xcode command line tools with xcode-select --install."
                )
            _run("xcrun", "--find", "clang")
            if not args.signing_identity or not re.fullmatch(
                r"[0-9a-fA-F]{40}", args.signing_identity
            ):
                raise ValueError(
                    "Select an Apple Development certificate fingerprint with --signing-identity."
                )
            identities = _run(
                "/usr/bin/security", "find-identity", "-v", "-p", "codesigning"
            ).stdout
            if not any(
                args.signing_identity.lower() in line.lower() and "Apple Development" in line
                for line in identities.splitlines()
            ):
                raise ValueError(
                    "This login keychain lacks the selected Apple Development certificate."
                )
    else:
        if shutil.which("systemctl") is None:
            raise ValueError(
                "Install systemd and enable a user service manager before installation."
            )
        _run("systemctl", "--user", "show-environment")
        service = args.service if args.command == "join" else args.service + ".service"
        unit = Path.home() / ".config/systemd/user" / service
        if unit.exists() or unit.is_symlink():
            raise ValueError(f"The service file already exists: {unit}. Select another --service.")
        result = _run(
            "systemctl",
            "--user",
            "show",
            service,
            "--property=LoadState",
            "--value",
            check=False,
        )
        if result.stdout.strip() not in ("", "not-found"):
            raise ValueError("The service already exists. Select another --service.")


def _api(
    repository: str,
    path: str,
    token_file: Path | None,
    destination: Path | None = None,
    *,
    github_cli: bool = False,
) -> Any:
    token = token_file.read_text().strip() if token_file else ""
    if not token and github_cli:
        token = _run("gh", "auth", "token").stdout.strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "panels-install"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{path}", headers=headers
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
            f"GitHub refused release access ({error.code}). Check --repository and --token-file."
        ) from None
    return None


def select_publication(
    args: argparse.Namespace,
    target: str,
    work: Path,
) -> tuple[Path, dict[str, Any]]:
    """Resolve immutable artifact IDs through the same trusted publication as updates."""
    if args.archive is not None:
        if args.receipt is None:
            raise ValueError("Supply --receipt with --archive.")
        receipt = json.loads(args.receipt.read_text())
        return args.archive, select_receipt(receipt, target, args.command)
    if args.receipt is not None:
        raise ValueError("Supply --archive with --receipt.")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository) is None:
        raise ValueError("Use --repository OWNER/REPOSITORY.")
    if args.run_id is None:
        runs = _api(
            args.repository,
            "actions/workflows/deploy.yml/runs?branch=main&event=push&status=success&per_page=20",
            args.token_file,
            github_cli=args.github_cli,
        )["workflow_runs"]
    else:
        runs = [
            _api(
                args.repository,
                f"actions/runs/{args.run_id}",
                args.token_file,
                github_cli=args.github_cli,
            )
        ]
    for run in runs:
        if args.revision is not None and run.get("head_sha") != args.revision:
            continue
        if (
            run.get("path") != ".github/workflows/deploy.yml"
            or run.get("head_branch") != "main"
            or run.get("event") != "push"
            or run.get("conclusion") != "success"
            or run.get("head_repository", {}).get("full_name") != args.repository
        ):
            continue
        artifacts = _api(
            args.repository,
            f"actions/runs/{run['id']}/artifacts?per_page=100",
            args.token_file,
            github_cli=args.github_cli,
        )["artifacts"]
        by_name = {item["name"]: item for item in artifacts if not item["expired"]}
        artifact_name = (
            ("panels-mac-runtime" if target == "darwin" else "panels-linux-executor")
            if args.command == "join"
            else "panels-mac-home"
            if target == "darwin"
            else "panels-app"
        )
        if "panels-publication-proof" not in by_name or artifact_name not in by_name:
            continue
        proof_zip = work / f"proof-{run['id']}.zip"
        _api(
            args.repository,
            f"actions/artifacts/{by_name['panels-publication-proof']['id']}/zip",
            args.token_file,
            proof_zip,
            github_cli=args.github_cli,
        )
        with zipfile.ZipFile(proof_zip) as zipped:
            original = json.loads(zipped.read("publication.json"))
        receipt = select_receipt(original, target, args.command)
        if (
            original["publication_run_id"] != run["id"]
            or receipt["artifact_id"] != by_name[artifact_name]["id"]
            or receipt["revision"] != run["head_sha"]
        ):
            raise ValueError("The release identity differs from its trusted publication.")
        if datetime.fromisoformat(original["expires_at"].replace("Z", "+00:00")) <= datetime.now(
            UTC
        ):
            continue
        artifact_zip = work / "artifact.zip"
        _api(
            args.repository,
            f"actions/artifacts/{receipt['artifact_id']}/zip",
            args.token_file,
            artifact_zip,
            github_cli=args.github_cli,
        )
        with zipfile.ZipFile(artifact_zip) as zipped:
            candidates = [name for name in zipped.namelist() if name.endswith(".tar.gz")]
            if len(candidates) != 1:
                raise ValueError("The release must contain exactly one sealed archive.")
            archive = work / "runtime.tar.gz"
            with zipped.open(candidates[0]) as source, archive.open("xb") as stream:
                shutil.copyfileobj(source, stream, 1024 * 1024)
        return archive, receipt
    raise ValueError(
        "No supported, unexpired release exists. Publish a checked release before installation."
    )


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
        if contract.get("version") != 1:
            raise ValueError("This bootstrap needs a sealed Python release from deploy.yml.")
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
    """Check loader prerequisites before the sealed Python interpreter can start."""
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("home", "join"))
    result.add_argument("--repository", default="khushal-jagota/headroom")
    result.add_argument("--token-file", type=Path)
    result.add_argument("--run-id", type=int)
    result.add_argument("--revision")
    result.add_argument("--github-cli", action="store_true")
    result.add_argument("--archive", type=Path)
    result.add_argument("--receipt", type=Path)
    result.add_argument("--root", type=Path)
    result.add_argument("--service")
    result.add_argument("--port", type=int, default=8767)
    result.add_argument("--installer-python", type=Path, default=Path(sys.executable))
    result.add_argument("--isolated-providers", action="store_true")
    result.add_argument("--home")
    result.add_argument("--machine-id")
    result.add_argument("--signing-identity")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        target = host_target()
        args.service = args.service or (
            "com.panels.home" if args.command == "home" else "com.panels.executor"
        )
        default_root = (
            Path.home() / "Library/Application Support/PanelsHome"
            if target == "darwin"
            else Path.home() / ".local/share/panels-home"
        )
        if args.command == "join":
            default_root = Path.home() / ".local/share/panels-executor"
            if not args.home or not args.machine_id:
                raise ValueError(
                    "Join needs --home URL and --machine-id from the owner's join command."
                )
            if target == "linux":
                if args.service == "com.panels.executor":
                    args.service = "panels-executor.service"
                if re.fullmatch(r"[A-Za-z0-9_-]+\.service", args.service) is None:
                    raise ValueError(
                        "Linux join needs --service NAME.service with letters, digits, - or _."
                    )
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
            if target == "linux":
                check_linux_contract(payload)
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("PLAN_")
            }
            if args.token_file:
                environment["PLAN_UPDATE_TOKEN_FILE"] = str(args.token_file.expanduser().resolve())
                environment["PLAN_UPDATE_REPOSITORY"] = args.repository
            if args.command == "home":
                command = [
                    str(payload / ".venv/bin/python"),
                    "-I",
                    "-B",
                    "-c",
                    "from planner.installation.home import main; main()",
                ]
                value = {
                    "root": str(args.root),
                    "service": args.service,
                    "port": args.port,
                    "installer_python": str(args.installer_python.expanduser().resolve()),
                    "isolated_providers": args.isolated_providers,
                    "archive": str(archive),
                    "archive_sha256": receipt["archive_sha256"],
                    "revision": receipt["revision"],
                }
            else:
                command = [
                    str(
                        payload
                        / ("python/bin/python3" if target == "darwin" else ".venv/bin/python")
                    ),
                    "-I",
                    "-B",
                    "-c",
                    "from planner.installation.join import main; main()",
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
                    "signing_identity": args.signing_identity,
                    "label": args.service,
                }
            print("Release checksum passed. Install the new service.", flush=True)
            subprocess.run(command, input=json.dumps(value), text=True, env=environment, check=True)
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
