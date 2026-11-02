"""Execute a sealed archive in the pinned, isolated Debian target root."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return (result.stdout + result.stderr).strip()


def _native_inventory_proof(
    root: Path, external_libraries: tuple[str, ...]
) -> list[dict[str, str]]:
    inventory = json.loads((root / "runtime/inventory.json").read_text())
    results = []
    for item in inventory["native_files"]:
        if not item["active"] or item["format"] != "ELF":
            results.append(
                {
                    "path": item["path"],
                    "execution_exclusion": item.get("exclusion") or "foreign format",
                }
            )
            continue
        path = root / item["path"]
        if item.get("providers"):
            output = _run(["/lib64/ld-linux-x86-64.so.2", "--list", str(path)])
            for resolved in re.findall(r"(?:=>\s+|^\s*)(/\S+)", output, flags=re.MULTILINE):
                library = Path(resolved).resolve()
                if not library.is_relative_to(root) and library.name not in external_libraries:
                    raise ValueError(f"native dependency escaped the declared boundary: {resolved}")
            results.append({"path": item["path"], "loader": output})
    # Import extension modules with package context. Private dependency libraries
    # receive loader proof above. Developer tools are recorded, not app imports.
    for base in (
        root / ".venv/lib/python3.12/site-packages",
        root / ".venv/lib/python3.12/lib-dynload",
    ):
        for path in sorted(base.rglob("*.so")):
            relative = path.relative_to(base)
            if any(part.endswith(".libs") for part in relative.parts):
                results.append(
                    {
                        "path": str(relative),
                        "import_exclusion": "wheel-private dependency; loader proof only",
                    }
                )
                continue
            if relative.parts[0] in {"mypy", "mypyc"} or relative.name.startswith(
                ("_test", "_ctypes_test.", "xxlimited", "xxsubtype")
            ):
                results.append(
                    {
                        "path": str(relative),
                        "import_exclusion": "developer/test extension; loader proof only",
                    }
                )
                continue
            module = ".".join((*relative.parts[:-1], relative.name.split(".")[0]))
            if not all(part.isidentifier() for part in module.split(".")):
                results.append(
                    {
                        "path": str(relative),
                        "import_exclusion": "not an importable Python module; loader proof only",
                    }
                )
                continue
            imported = importlib.import_module(module)
            if imported.__file__ is None or not Path(imported.__file__).resolve().is_relative_to(
                root
            ):
                raise ValueError(f"native module escaped payload: {module}")
            results.append({"path": str(relative), "import": module})
    return results


def inside_proof(archive_digest: str, requested_sha: str) -> None:
    """Run only inside the private root, with the extracted payload interpreter."""
    start = time.monotonic()
    root = Path("/work/app")
    from planner.environments.app import validate_app_manifest
    from planner.environments.deployment_gate import smoke_candidate
    from planner.environments.runtime_admission import (
        NATIVE_PROBE,
        admit_runtime,
        native_tool_commands,
    )

    archive = Path("/input/archive.tar.gz")
    if _hash(archive) != archive_digest:
        raise ValueError("sealed archive changed inside target proof")
    forbidden = []
    for prefix in (Path("/usr"), Path("/lib"), Path("/opt")):
        for path in prefix.rglob("*"):
            if (
                (
                    path.is_file()
                    and path.name
                    in {"python3.12", "gcc", "g++", "cc", "npm", "npx", "pip", "apt-get", "dpkg"}
                )
                or re.fullmatch(r"libicu.*\.so\.74(?:\..*)?", path.name)
                or "hostedtoolcache" in str(path)
            ):
                forbidden.append(str(path))
    if forbidden:
        raise ValueError(f"target contains forbidden builder dependencies: {forbidden}")
    manifest = validate_app_manifest(root / "manifest.json", expected_sha=requested_sha)
    contract = manifest.runtime_contract
    if contract is None:
        raise ValueError("target proof requires a runtime contract")
    admit_runtime(root, manifest)
    native = _native_inventory_proof(root, contract.external_libraries)
    native_operations = json.loads(
        _run(
            [
                str(root / ".venv/bin/python"),
                "-I",
                "-B",
                "-c",
                NATIVE_PROBE,
                str(root),
                contract.python_version,
                json.dumps(contract.external_libraries),
            ]
        )
    )
    tool_commands = native_tool_commands(root, contract)
    frontend = {}
    for relative in (
        "web/node_modules/@esbuild/linux-x64/bin/esbuild",
        "web/node_modules/esbuild/bin/esbuild",
    ):
        path = root / relative
        if path.exists():
            frontend[relative] = _run([str(path), "--version"])
    rollup = root / "web/node_modules/rollup/dist/rollup.js"
    if rollup.exists():
        frontend["rollup"] = _run(
            [
                "node",
                "-e",
                "const r=require(process.argv[1]); console.log(r.VERSION)",
                str(rollup),
            ]
        )
    smoke_candidate(root, requested_sha)
    validate_app_manifest(root / "manifest.json", expected_sha=requested_sha)
    # The first interpreter remains mapped; all subsequent subprocesses use the
    # relocated prefix. No repair or installation occurs between paths.
    relocated = Path("/work/second-location/relocated-app")
    relocated.parent.mkdir()
    root.rename(relocated)
    # Start a new interpreter after relocation. Existing imports (for example
    # certifi) retain their original __file__ and are not a relocation failure.
    _run(
        [
            str(relocated / ".venv/bin/python"),
            "-I",
            "-B",
            "-c",
            "import sys; from pathlib import Path; "
            "from planner.environments.app import validate_app_manifest; "
            "from planner.environments.runtime_admission import admit_runtime; "
            "from planner.environments.deployment_gate import smoke_candidate; "
            "root=Path(sys.argv[1]); sha=sys.argv[2]; "
            "manifest=validate_app_manifest(root/'manifest.json', expected_sha=sha); "
            "admit_runtime(root, manifest); smoke_candidate(root, sha); "
            "assert validate_app_manifest(root/'manifest.json', expected_sha=sha)==manifest",
            str(relocated),
            requested_sha,
        ],
        timeout=240,
    )
    if _hash(archive) != archive_digest:
        raise ValueError("sealed archive changed after proof")
    evidence = {
        "passed": True,
        "requested_sha": requested_sha,
        "artifact_digest": manifest.artifact_digest,
        "archive_sha256": archive_digest,
        "contract_sha256": contract.digest,
        "native": native,
        "native_operations": native_operations,
        "admitted_tool_commands": tool_commands,
        "frontend": frontend,
        "relocation": True,
        "health_assets_api": True,
        "forbidden_dependencies": forbidden,
        "cpuinfo": Path("/proc/cpuinfo").read_text(),
        "mountinfo": Path("/proc/self/mountinfo").read_text(),
        "os_release": Path("/etc/os-release").read_text(),
        "duration_seconds": time.monotonic() - start,
    }
    Path("/evidence/target-execution.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")


def prove_archive(*, archive: Path, requested_sha: str, target_root: Path, evidence: Path) -> Path:
    # The control process imports its own helper only. The isolated process gets
    # this one harness, never the checkout or host libraries.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.runtime_target_root import (
        TARGET_IMAGE_DIGEST,
        TARGET_ROOT_INVENTORY_SHA256,
        inventory_digest,
        root_inventory,
    )

    archive = archive.resolve()
    target_root = target_root.resolve()
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    root_receipt = target_root.parent / (target_root.name + "-inventory.json")
    document = json.loads(root_receipt.read_text())
    if document["image_digest"] != TARGET_IMAGE_DIGEST:
        raise ValueError("wrong target image")
    root_digest = inventory_digest(root_inventory(target_root))
    if (
        root_digest != TARGET_ROOT_INVENTORY_SHA256
        or root_digest != document["root_inventory_sha256"]
    ):
        raise ValueError("target root changed after preparation")
    digest = _hash(archive)
    start = time.monotonic()
    command = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        "--clearenv",
        "--ro-bind",
        str(target_root),
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/input",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/work",
        "--dir",
        "/work/home",
        "--ro-bind",
        str(archive),
        "/input/archive.tar.gz",
        "--ro-bind",
        str(Path(__file__).resolve()),
        "/input/harness.py",
        "--bind",
        str(evidence),
        "/evidence",
        "--chdir",
        "/work",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/work/home",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "PLAN_DISPATCH_ENABLED",
        "false",
        "--",
        "/bin/sh",
        "-ec",
        "mkdir /work/app; tar --no-same-owner -xpf /input/archive.tar.gz -C /work/app; "
        'exec /work/app/.venv/bin/python -I -B /input/harness.py inside "$1" "$2"',
        "target-proof",
        digest,
        requested_sha,
    ]
    with (evidence / "target.log").open("w") as log:
        subprocess.run(command, check=True, timeout=900, stdout=log, stderr=subprocess.STDOUT)
    result = json.loads((evidence / "target-execution.json").read_text())
    if result.get("passed") is not True or result.get("archive_sha256") != digest:
        raise ValueError("target execution did not prove this archive")
    if _hash(archive) != digest or inventory_digest(root_inventory(target_root)) != root_digest:
        raise ValueError("proof inputs changed during execution")
    result.update(
        {
            "target_image_digest": TARGET_IMAGE_DIGEST,
            "root_inventory_sha256": root_digest,
            "total_duration_seconds": time.monotonic() - start,
        }
    )
    receipt = evidence / "target-proof.json"
    receipt.write_text(json.dumps(result, sort_keys=True) + "\n")
    (evidence / "target-root-inventory.json").write_bytes(root_receipt.read_bytes())
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    inside = subparsers.add_parser("inside")
    inside.add_argument("archive_digest")
    inside.add_argument("requested_sha")
    outside = subparsers.add_parser("prove")
    outside.add_argument("--archive", type=Path, required=True)
    outside.add_argument("--requested-sha", required=True)
    outside.add_argument("--target-root", type=Path, required=True)
    outside.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "inside":
        inside_proof(args.archive_digest, args.requested_sha)
    else:
        print(
            prove_archive(
                archive=args.archive,
                requested_sha=args.requested_sha,
                target_root=args.target_root,
                evidence=args.evidence,
            )
        )


if __name__ == "__main__":
    main()
