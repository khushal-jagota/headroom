"""Build and prove the native arm64 Mac runtime on a disposable hosted Mac.

This script never signs an app or accesses a personal Mac. The archive includes a
portable interpreter, installed runtime dependencies and native npm dependencies.
Node 22 and owner-managed provider credentials remain external prerequisites.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from planner.environments.mac_runtime_configuration import (  # noqa: E402
    MAC_BUILD_TOOL_REQUIREMENTS,
    MAC_PYTHON_SHA256,
    MAC_PYTHON_URL,
    MAC_PYTHON_VERSION,
    MAC_RUNTIME_MINIMUM_OS,
    MAC_RUNTIME_RECEIPT_NAME,
)
from planner.executor.update_configuration import (  # noqa: E402
    MAC_RUNTIME_ARCHIVE_NAME,
    MAC_RUNTIME_MANIFEST_NAME,
)

# Binary magic numbers cover native and universal Mach-O files, not their suffixes.
MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_pins(source: Path) -> dict[str, str]:
    pins = {}
    for line in (source / "requirements.txt").read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;#]+)", value)
        if match is None:
            raise ValueError(f"Mac publication requires an exact dependency pin: {value}")
        name, version = match.groups()
        pins[re.sub(r"[-_.]+", "-", name).lower()] = f"{name}=={version}"
    return pins


def runtime_requirements(source: Path) -> list[str]:
    """Select declared runtime roots, then constrain their entire dependency closure."""
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
    pins = canonical_pins(source)
    result = []
    for requirement in project["dependencies"]:
        match = re.match(r"([A-Za-z0-9_.-]+)(\[[A-Za-z0-9_,.-]+\])?", requirement)
        if match is None:
            raise ValueError("Invalid runtime dependency")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name not in pins:
            raise ValueError(f"Runtime dependency lacks a canonical pin: {name}")
        pinned_name, version = pins[name].split("==", 1)
        result.append(f"{pinned_name}{match.group(2) or ''}=={version}")
    return result


def validate_installed_pins(
    installed: dict[str, str], pins: dict[str, str], bootstrap: dict[str, str]
) -> None:
    """A newly resolved transitive dependency must also have a canonical exact pin."""
    for name, version in installed.items():
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized == "planner":
            continue
        if normalized in pins:
            if pins[normalized].partition("==")[2] != version:
                raise ValueError(f"Mac runtime dependency differs from its canonical pin: {name}")
        elif bootstrap.get(name) != version:
            raise ValueError(f"Mac runtime contains an unpinned dependency: {name}")


DISTRIBUTIONS = (
    "import importlib.metadata,json; print(json.dumps("
    "{d.metadata['Name']:d.version for d in importlib.metadata.distributions()}))"
)


def run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=1200,
    )
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"Native publication command failed: {command[0]}")
    return result.stdout


def write_panels_launcher(runtime: Path) -> None:
    launcher = runtime / "python/bin/panels"
    launcher.write_text(
        "#!/bin/sh\n"
        'panels_python_bin=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec "$panels_python_bin/python3" -I -B -m planner.executor.entrypoint "$@"\n'
    )
    launcher.chmod(0o755)


def _rpaths(path: Path, *, root: Path, environment: dict[str, str]) -> list[str]:
    text = run(["/usr/bin/otool", "-l", str(path)], cwd=root, environment=environment)
    return re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", text)


def _resolve_loader_path(value: str, binary: Path, executable: Path) -> Path | None:
    if value.startswith("@loader_path/"):
        return (binary.parent / value.removeprefix("@loader_path/")).resolve()
    if value.startswith("@executable_path/"):
        return (executable.parent / value.removeprefix("@executable_path/")).resolve()
    if value.startswith("/"):
        return Path(value).resolve()
    return None


def audit_native_dependencies(root: Path, environment: dict[str, str]) -> list[dict[str, object]]:
    """Reject any non-system dependency outside the extracted archive."""
    root = root.resolve()
    executable = root / "python/bin/python3"
    executable_rpaths = _rpaths(executable, root=root, environment=environment)
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root) or not path.exists():
                raise ValueError(f"Mac runtime contains an external or broken link: {path}")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO_MAGIC:
                continue
        architectures = run(
            ["/usr/bin/lipo", "-archs", str(path)], cwd=root, environment=environment
        )
        if "arm64" not in architectures.split():
            raise ValueError(f"Mac runtime contains a native binary without arm64: {path}")
        own_rpaths = _rpaths(path, root=root, environment=environment)
        search = []
        for value, owner in (
            *((value, path) for value in own_rpaths),
            *((value, executable) for value in executable_rpaths),
        ):
            resolved = _resolve_loader_path(value, owner, executable)
            if resolved is None or not (
                resolved.is_relative_to(root)
                or str(resolved).startswith(("/usr/lib/", "/System/Library/"))
            ):
                raise ValueError(f"Mac runtime contains an external library search path: {value}")
            search.append(resolved)
        # Dylib install IDs are names, not dependencies. Delocate uses unique /DLC IDs.
        identifiers = run(["/usr/bin/otool", "-D", str(path)], cwd=root, environment=environment)
        own_ids = {line.strip() for line in identifiers.splitlines()[1:]}
        listing = run(["/usr/bin/otool", "-L", str(path)], cwd=root, environment=environment)
        dependencies = []
        for line in listing.splitlines():
            match = re.match(r"\s+(.+?) \(compatibility version ", line)
            if match is None or match.group(1) in own_ids:
                continue
            dependency = match.group(1)
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                dependencies.append(dependency)
                continue
            if dependency.startswith("@rpath/"):
                candidates = [base / dependency.removeprefix("@rpath/") for base in search]
            else:
                candidate = _resolve_loader_path(dependency, path, executable)
                candidates = [] if candidate is None else [candidate]
            if not any(p.exists() and p.resolve().is_relative_to(root) for p in candidates):
                raise ValueError(f"Mac runtime has an unresolved/external library: {dependency}")
            dependencies.append(dependency)
        records.append(
            {
                "path": str(path.relative_to(root)),
                "architectures": architectures.split(),
                "libraries": dependencies,
                "rpaths": own_rpaths,
            }
        )
    if not records:
        raise ValueError("Mac runtime contains no native executable proof")
    return records


NATIVE_PROBE = r"""
import asyncio, json, os, pathlib, platform, sqlite3, sys
root = pathlib.Path(sys.argv[1]).resolve()
assert sys.platform == "darwin" and platform.machine() == "arm64"
assert sys.version.split()[0] == sys.argv[2]
for path in (sys.executable, sys.prefix, sys.base_prefix):
    pathlib.Path(path).resolve().relative_to(root)
import planner.cli.main, planner.executor.mac_service, aiohttp, cryptography, icu, ssl
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from planner.conversation.production_backends import PANELS_OWNED_CLAUDE_EXECUTABLE
for module in (planner.cli.main, planner.executor.mac_service, aiohttp, cryptography, icu):
    pathlib.Path(module.__file__).resolve().relative_to(root)
assert PANELS_OWNED_CLAUDE_EXECUTABLE.resolve().is_relative_to(root)
assert PANELS_OWNED_CLAUDE_EXECUTABLE.is_file()
with sqlite3.connect(":memory:") as db:
    assert db.execute("select 6 * 7").fetchone()[0] == 42
cipher = AESGCM(bytes(32))
assert cipher.decrypt(bytes(12), cipher.encrypt(bytes(12), b"proof", b""), b"") == b"proof"
formatter = icu.SimpleDateFormat("yyyy", icu.Locale("en_US"))
formatter.setTimeZone(icu.TimeZone.createTimeZone("UTC"))
assert formatter.format(0.0) == "1970"
ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
async def check_http():
    async with aiohttp.ClientSession() as session:
        assert not session.closed
asyncio.run(check_http())
print(json.dumps({"native": "passed", "python": sys.version.split()[0],
                  "platform": sys.platform, "architecture": platform.machine(),
                  "cli_import": True, "mac_service_import": True,
                  "sqlite": True, "aiohttp": True, "cryptography": True, "icu": True}))
"""


def prove_archive(archive: Path, work: Path, *, revision: str) -> dict[str, object]:
    extracted = work / "relocated runtime with spaces"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as packed:
        packed.extractall(extracted, filter="data")
    state = work / "proof-home"
    state.mkdir()
    node = shutil.which("node")
    if node is None:
        raise ValueError("Native proof requires the external Node 22 prerequisite")
    environment = {
        "PATH": f"{extracted / 'python/bin'}:{Path(node).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(state),
        "TMPDIR": str(state),
        "PLAN_APP_ROOT": str(extracted),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CODEX_HOME": str(state / "codex"),
        "CLAUDE_CONFIG_DIR": str(state / "claude"),
    }
    native = json.loads(
        run(
            [
                str(extracted / "python/bin/python3"),
                "-I",
                "-B",
                "-c",
                NATIVE_PROBE,
                str(extracted),
                MAC_PYTHON_VERSION,
            ],
            cwd=state,
            environment=environment,
        )
    )
    help_text = run(
        [str(extracted / "python/bin/panels"), "--help"], cwd=state, environment=environment
    )
    if "Usage:" not in help_text:
        raise ValueError("Relocated Panels CLI did not return help")
    run(
        [str(extracted / "python/bin/panels"), "executor", "run", "--help"],
        cwd=state,
        environment=environment,
    )
    backend_root = extracted / "agent_backends/node_modules"
    for relative in (
        "@agentclientprotocol/claude-agent-acp/dist/index.js",
        "@agentclientprotocol/codex-acp/dist/index.js",
    ):
        entry = backend_root / relative
        if not entry.is_file():
            raise ValueError("Mac runtime lacks a locked backend entry point")
        run([node, "--check", str(entry)], cwd=state, environment=environment)
    claude = backend_root / "@anthropic-ai/claude-code/bin/claude.exe"
    run([str(claude), "--version"], cwd=state, environment=environment)
    libraries = audit_native_dependencies(extracted, environment)
    return {
        "passed": True,
        "application_revision": revision,
        "relocation": True,
        "native_operations": native,
        "native": libraries,
        "cli_help": True,
        "backend_entrypoints": True,
        "forbidden_dependencies": [],
        "external_requirements": [
            "macOS 15 or newer on arm64",
            "Node.js 22 or newer",
            "fixed signed Panels launcher",
            "owner-managed provider runtimes and credentials",
        ],
    }


def build(source: Path, output: Path, revision: str, run_id: int) -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("Mac publication requires a native darwin arm64 builder")
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None or run_id < 1:
        raise ValueError("Mac publication requires exact source and workflow identities")
    if (
        subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        != revision
    ):
        raise ValueError("Mac source differs from requested publication")
    if subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("Mac publication source must be clean")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="panels-mac-build-") as temporary:
        work = Path(temporary)
        builder = work / "builder"
        builder.mkdir()
        home = builder / "home"
        home.mkdir()
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "TMPDIR": str(builder),
            "MACOSX_DEPLOYMENT_TARGET": MAC_RUNTIME_MINIMUM_OS,
        }
        # Invoke the repository scanner for all writable builder destinations.
        # Its documented Darwin branch reports the Linux-only mount check limit.
        sys.path.insert(0, str(source / "scripts"))
        from development_safety.scanner import check_paths

        check_paths([source, output, builder], environment=environment)
        runtime = builder / "runtime"
        runtime.mkdir()
        python_archive = builder / "python.tar.gz"
        with urllib.request.urlopen(MAC_PYTHON_URL, timeout=120) as response:
            with python_archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        if digest_file(python_archive) != MAC_PYTHON_SHA256:
            raise ValueError("Pinned portable Python digest differs")
        with tarfile.open(python_archive, "r:gz") as packed:
            packed.extractall(runtime, filter="data")
        python = runtime / "python/bin/python3"
        bootstrap = json.loads(
            run(
                [str(python), "-I", "-c", DISTRIBUTIONS],
                cwd=builder,
                environment=environment,
            )
        )
        tools = builder / "tools"
        # Build wheels with the runtime's arm64 sysconfig. The hosted Python can
        # request universal2 extensions that cannot link the arm64-only ICU build.
        run([str(python), "-m", "venv", str(tools)], cwd=builder, environment=environment)
        tool_python = str(tools / "bin/python")
        run(
            [
                tool_python,
                "-m",
                "pip",
                "install",
                *MAC_BUILD_TOOL_REQUIREMENTS,
                "--constraint",
                str(source / "requirements.txt"),
            ],
            cwd=builder,
            environment=environment,
        )
        wheels = builder / "wheels"
        wheels.mkdir()
        requirements = runtime_requirements(source)
        regular = [value for value in requirements if not value.lower().startswith("pyicu==")]
        run(
            [
                tool_python,
                "-m",
                "pip",
                "wheel",
                "--wheel-dir",
                str(wheels),
                # http_ece is a pinned pure-Python sdist. Build its wheel with
                # the isolated, pinned builder tools instead of skipping it.
                "--no-build-isolation",
                "--constraint",
                str(source / "requirements.txt"),
                *regular,
            ],
            cwd=builder,
            environment=environment,
        )
        icu = run(["brew", "--prefix", "icu4c"], cwd=builder, environment=environment).strip()
        icu_environment = {**environment, "PKG_CONFIG_PATH": str(Path(icu) / "lib/pkgconfig")}
        icu_wheels = builder / "icu-wheels"
        icu_wheels.mkdir()
        icu_pin = next(value for value in requirements if value.lower().startswith("pyicu=="))
        run(
            [
                tool_python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(icu_wheels),
                icu_pin,
            ],
            cwd=builder,
            environment=icu_environment,
        )
        for wheel in icu_wheels.glob("*.whl"):
            run(
                [
                    str(tools / "bin/delocate-wheel"),
                    "--require-archs",
                    "arm64",
                    "-w",
                    str(wheels),
                    str(wheel),
                ],
                cwd=builder,
                environment=icu_environment,
            )
        run(
            [
                tool_python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheels),
                str(source),
            ],
            cwd=builder,
            environment=environment,
        )
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-compile",
                "--find-links",
                str(wheels),
                "--constraint",
                str(source / "requirements.txt"),
                *requirements,
            ],
            cwd=builder,
            environment=environment,
        )
        planner_wheel = next(wheels.glob("planner-*.whl"))
        run(
            [str(python), "-m", "pip", "install", "--no-deps", "--no-compile", str(planner_wheel)],
            cwd=builder,
            environment=environment,
        )
        run([str(python), "-m", "pip", "check"], cwd=builder, environment=environment)
        installed = json.loads(
            run(
                [str(python), "-I", "-c", DISTRIBUTIONS],
                cwd=builder,
                environment=environment,
            )
        )
        validate_installed_pins(installed, canonical_pins(source), bootstrap)
        # Use the requested source's guarded npm entry, then copy only its exact
        # lock-controlled, development-owned tree into the runtime archive.
        run(
            [str(source / "scripts/dev"), "npm", "--prefix", "agent_backends", "ci"],
            cwd=source,
            environment=environment,
        )
        backend = runtime / "agent_backends"
        shutil.copytree(source / "agent_backends", backend, symlinks=True)
        # Every pip-generated script embeds the temporary interpreter path. Only
        # the interpreter family and our relative CLI launcher belong in the archive.
        for entry in (runtime / "python/bin").iterdir():
            if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", entry.name) is None:
                entry.unlink()
        write_panels_launcher(runtime)
        (runtime / MAC_RUNTIME_MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "application_revision": revision,
                    "platform": "darwin",
                    "architecture": "arm64",
                },
                sort_keys=True,
            )
            + "\n"
        )
        archive = output / MAC_RUNTIME_ARCHIVE_NAME
        with tarfile.open(archive, "w:gz", dereference=False) as packed:
            for entry in sorted(runtime.iterdir()):
                packed.add(entry, arcname=entry.name)
        # The absolute original build path does not exist during the sealed proof.
        builder.rename(work / "inaccessible-builder")
        proof = prove_archive(archive, work, revision=revision)
        receipt = {
            "schema_version": 1,
            "application_revision": revision,
            "publication_run_id": run_id,
            "archive_sha256": digest_file(archive),
            "archive_size": archive.stat().st_size,
            "python_archive_sha256": MAC_PYTHON_SHA256,
            "python_version": MAC_PYTHON_VERSION,
            "proof": proof,
            "duration_seconds": time.monotonic() - started,
        }
        (output / MAC_RUNTIME_RECEIPT_NAME).write_text(json.dumps(receipt, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.revision, args.run_id)


if __name__ == "__main__":
    main()
