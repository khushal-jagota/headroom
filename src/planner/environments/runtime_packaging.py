"""Build-only completion and static audit of the relocatable archive runtime.

readelf and patchelf are packaging tools. Neither is a target prerequisite.
Only glibc and its loader remain external to the native payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from planner.environments.runtime_contracts import (
    GLIBC_LIBRARIES,
    RUNTIME_CONTRACT_PATH,
    RUNTIME_INVENTORY_PATH,
    RuntimeContract,
    RuntimeContractError,
    canonical_json_bytes,
    runtime_bytes_digest,
)


def _run(*arguments: str) -> str:
    return subprocess.run(arguments, check=True, text=True, capture_output=True, timeout=60).stdout


def _elf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def _dynamic(path: Path) -> tuple[list[str], list[str], str | None, bool]:
    output = _run("readelf", "-dW", str(path))
    needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", output)
    paths = re.findall(r"\((?:RPATH|RUNPATH)\).*?\[(.*?)\]", output)
    header = _run("readelf", "-lW", str(path))
    interpreter = re.search(r"Requesting program interpreter: (.*?)\]", header)
    return (
        needed,
        [part for entry in paths for part in entry.split(":") if part],
        (interpreter.group(1) if interpreter else None),
        "(RPATH)" in output and "(RUNPATH)" not in output,
    )


def _relative_paths(paths: list[str], path: Path, library_root: Path) -> list[str]:
    # Keep wheel-private order. Absolute builder paths are used for source
    # resolution only, never retained in the shipped runtime.
    result = [
        entry
        for entry in paths
        if entry.startswith(("$ORIGIN/", "${ORIGIN}/")) or entry in ("$ORIGIN", "${ORIGIN}")
    ]
    shared = "$ORIGIN/" + os.path.relpath(library_root, path.parent)
    if shared not in result:
        result.append(shared)
    return result


def _provider(name: str, source: Path, paths: list[str], cache: dict[str, Path]) -> Path:
    for entry in paths:
        directory = Path(
            entry.replace("${ORIGIN}", str(source.parent)).replace("$ORIGIN", str(source.parent))
        )
        if not directory.is_absolute():
            raise RuntimeContractError(f"unsafe relative ELF search path: {source}: {entry}")
        candidate = directory / name
        if candidate.is_file():
            return candidate.resolve()
    if name in cache:
        return cache[name]
    raise RuntimeContractError(f"unresolved native dependency {name} required by {source}")


def _host_cache() -> dict[str, Path]:
    command = shutil.which("ldconfig") or "/sbin/ldconfig"
    cache: dict[str, Path] = {}
    for line in _run(command, "-p").splitlines():
        match = re.match(r"\s*(\S+)\s+\(.*x86-64.*\) => (\S+)", line)
        if match:
            cache.setdefault(match.group(1), Path(match.group(2)).resolve())
    return cache


def _rewrite_console_scripts(prefix: Path) -> None:
    for path in (prefix / "bin").iterdir():
        if path.is_symlink() or not path.is_file() or _elf(path):
            continue
        content = path.read_bytes()
        first, separator, remainder = content.partition(b"\n")
        if first.startswith(b"#!") and b"python" in first and separator:
            # Shell/Python polyglot preserves the original script body and args.
            path.write_bytes(
                b'#!/bin/sh\n""":"\nexec "$(dirname -- "$0")/python" "$0" "$@"\n":"""\n' + remainder
            )


def _inactive_musl(path: Path, needed: list[str], interpreter: str | None) -> bool:
    if interpreter is not None and "ld-musl-" in interpreter:
        return True
    if any(name.startswith("libc.musl-") for name in needed):
        return True
    # Rollup's addon uses musl's generic SONAME with no PT_INTERP. Its
    # package selector, libc declaration, and actual ELF edge must all agree.
    metadata = path.parent / "package.json"
    if "libc.so" in needed and metadata.is_file():
        package = json.loads(metadata.read_text())
        return bool(
            package.get("name") == "@rollup/rollup-linux-x64-musl"
            and package.get("libc") == ["musl"]
        )
    return False


def _native_role(relative: str, header: str, *, inactive: bool) -> str:
    if inactive:
        return "inactive platform asset"
    if re.search(r"Type:\s+REL\b", header):
        return "build object"
    if relative.startswith("web/node_modules/"):
        return "frontend build tool"
    if relative == ".venv/bin/ruff" or any(
        part in relative
        for part in (
            "/ruff/",
            "/mypy/",
            "/mypyc/",
            "/black/",
            "/pytokens/",
            "/librt/",
            "/ast_serialize/",
            "__mypyc.",
        )
    ):
        return "development tool"
    if (
        re.search(r"Type:\s+EXEC\b", header)
        or "DYN (Position-Independent Executable file)" in header
    ):
        return "runtime entrypoint"
    return "runtime library"


def _copied_component(
    source: Path,
    app_root: Path,
    python_base: Path,
    *,
    python_stdlib: Path,
    python_executable: Path,
    python_version: str,
) -> dict[str, str]:
    """Retain distribution attribution and license text for newly copied files."""
    source = source.resolve()
    package = ""
    if shutil.which("dpkg-query"):
        for candidate in (source, Path(str(source).replace("/usr/lib/", "/lib/", 1))):
            result = subprocess.run(
                ["dpkg-query", "-S", str(candidate)], text=True, capture_output=True, timeout=30
            )
            if result.returncode == 0:
                package = result.stdout.split(": ", 1)[0].splitlines()[0]
                break
    if package:
        version = _run("dpkg-query", "-W", "-f=${Version}", package)
        license_source = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
    elif (
        source.is_relative_to(python_stdlib.resolve())
        or source == python_executable.resolve()
        or (
            source.is_relative_to(python_base.resolve())
            and re.fullmatch(r"libpython3\.12\.so(?:\.\d+)*", source.name)
        )
    ):
        package = "CPython"
        version = python_version
        candidates = (
            python_base / "LICENSE.txt",
            python_base / "LICENSE",
            python_base / "lib/python3.12/LICENSE.txt",
        )
        license_source = next((path for path in candidates if path.is_file()), candidates[0])
    else:
        raise RuntimeContractError(f"no package provenance for copied library: {source}")
    if not license_source.is_file():
        raise RuntimeContractError(f"no license notice for copied library: {source}")
    destination = app_root / "runtime/licenses" / (package.replace(":", "_") + ".txt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(license_source, destination)
    return dict(
        package=package,
        version=version,
        license=str(destination.relative_to(app_root)),
        source=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def complete_private_runtime(app_root: Path) -> RuntimeContract:
    """Complete an installed venv, audit every native file, and seal its contract."""
    app_root = app_root.resolve()
    prefix = app_root / ".venv"
    python = prefix / "bin/python"
    if not shutil.which("patchelf") or not shutil.which("readelf"):
        raise RuntimeContractError("packaging requires build-only patchelf and readelf")
    config = json.loads(
        _run(
            str(python),
            "-I",
            "-c",
            "import json,sys,sysconfig; print(json.dumps(dict(version=sys.version.split()[0], "
            'stdlib=sysconfig.get_path("stdlib"), base=sys.base_prefix, '
            "executable=sys._base_executable, "
            'libdir=sysconfig.get_config_var("LIBDIR"), '
            'libpython=sysconfig.get_config_var("LDLIBRARY"))))',
        )
    )
    if not re.fullmatch(r"3\.12\.\d+", config["version"]):
        raise RuntimeContractError("archive packaging requires CPython 3.12")
    stdlib = Path(config["stdlib"])
    target_stdlib = prefix / "lib/python3.12"
    shutil.copytree(
        stdlib,
        target_stdlib,
        dirs_exist_ok=True,
        symlinks=False,
        ignore=shutil.ignore_patterns("site-packages", "dist-packages", "__pycache__"),
    )
    library_root = prefix / "lib"
    library_root.mkdir(parents=True, exist_ok=True)
    origins: dict[str, str] = {}
    # Shared libpython is included by the actual interpreter's dependency
    # closure. Static distro interpreters do not require an unused libpython.
    copied_python_files = {
        str(path.relative_to(app_root)): {
            "source": str(stdlib / path.relative_to(target_stdlib)),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in target_stdlib.rglob("*")
        if path.is_file() and "site-packages" not in path.parts
    }
    for relative, copied_file in copied_python_files.items():
        if _elf(app_root / relative):
            origins[relative] = copied_file["source"]
    # A private prefix uses its own stdlib landmarks, never a builder venv home.
    (prefix / "pyvenv.cfg").unlink(missing_ok=True)
    for path in (prefix / "bin").glob("python*"):
        if path.is_symlink():
            source = path.resolve()
            path.unlink()
            shutil.copy2(source, path)
    for path in (prefix / "bin").glob("python*"):
        if path.is_file() and _elf(path):
            origins[str(path.relative_to(app_root))] = config["executable"]
    _rewrite_console_scripts(prefix)
    cache = _host_cache()
    pending = [
        path
        for path in app_root.rglob("*")
        if path.is_file() and not path.is_symlink() and _elf(path)
    ]
    visited: set[Path] = set()
    inventory: list[dict[str, object]] = []
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        relative = str(path.relative_to(app_root))
        source = Path(origins.get(relative, str(path)))
        needed, paths, interpreter, preserve_rpath = _dynamic(path)
        header = _run("readelf", "-hW", str(path))
        if "Advanced Micro Devices X86-64" not in header:
            raise RuntimeContractError(f"unknown ELF architecture: {relative}")
        inactive = _inactive_musl(path, needed, interpreter)
        if interpreter and not inactive and interpreter != "/lib64/ld-linux-x86-64.so.2":
            raise RuntimeContractError(f"unknown ELF loader: {relative}: {interpreter}")
        notes = _run("readelf", "-nW", str(path))
        if not inactive and "x86-64-v4" in notes:
            raise RuntimeContractError(f"ELF requires unsupported x86-64-v4: {relative}")
        providers: dict[str, str] = {}
        if not inactive:
            for name in needed:
                if name in GLIBC_LIBRARIES:
                    providers[name] = "host:" + name
                    continue
                provider = _provider(name, source, paths, cache)
                try:
                    provider.relative_to(app_root)
                except ValueError:
                    destination = library_root / name
                    if destination.exists() and (
                        Path(
                            origins.get(str(destination.relative_to(app_root)), str(destination))
                        ).read_bytes()
                        != provider.read_bytes()
                    ):
                        raise RuntimeContractError(f"conflicting bundled library: {name}") from None
                    if not destination.exists():
                        shutil.copy2(provider, destination)
                        origins[str(destination.relative_to(app_root))] = str(provider)
                        pending.append(destination)
                    provider = destination
                providers[name] = str(provider.relative_to(app_root))
            requires_private_search = any(name not in GLIBC_LIBRARIES for name in needed)
            has_builder_search = any(
                not entry.startswith(("$ORIGIN", "${ORIGIN}")) for entry in paths
            )
            # Avoid rewriting glibc-only standalone programs. Some carry
            # embedded application payloads beyond their ordinary ELF sections.
            if requires_private_search or has_builder_search:
                _run(
                    "patchelf",
                    *(["--force-rpath"] if preserve_rpath else []),
                    "--set-rpath",
                    ":".join(_relative_paths(paths, path, library_root)),
                    str(path),
                )
        versions = sorted(
            set(re.findall(r"\bGLIBC_(\d+\.\d+)\b", _run("readelf", "-VW", str(path))))
        )
        if not inactive and any(tuple(map(int, value.split("."))) > (2, 39) for value in versions):
            raise RuntimeContractError(f"ELF exceeds supported glibc 2.39 boundary: {relative}")
        inventory.append(
            dict(
                path=relative,
                format="ELF",
                active=not inactive,
                exclusion="musl dynamic variant selected only on musl hosts" if inactive else None,
                role=_native_role(relative, header, inactive=inactive),
                origin=origins.get(relative, "installed payload"),
                providers=providers,
                interpreter=interpreter,
                gnu_property_notes=notes,
                glibc_versions=versions,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    for path in app_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                magic = stream.read(2)
            if magic == b"MZ":
                inventory.append(
                    dict(
                        path=str(path.relative_to(app_root)),
                        format="PE",
                        active=False,
                        exclusion="Windows asset",
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
    # Re-resolve from the final files with no host cache. This catches bad
    # relative paths and ensures every non-glibc edge stays within the archive.
    for item in inventory:
        if item["format"] != "ELF" or not item["active"]:
            continue
        path = app_root / str(item["path"])
        needed, paths, _, _ = _dynamic(path)
        for entry in paths:
            if not entry.startswith(("$ORIGIN", "${ORIGIN}")):
                raise RuntimeContractError(f"nonrelative final loader path: {path}: {entry}")
        for name in needed:
            if name not in GLIBC_LIBRARIES:
                provider = _provider(name, path, paths, {})
                if not provider.is_relative_to(app_root):
                    raise RuntimeContractError(f"native dependency escapes archive: {path}: {name}")
        item["loader_paths"] = paths
    for relative, copied_file in copied_python_files.items():
        copied_file["payload_sha256"] = hashlib.sha256(
            (app_root / relative).read_bytes()
        ).hexdigest()
    provenance_arguments = dict(
        python_stdlib=stdlib,
        python_executable=Path(config["executable"]),
        python_version=config["version"],
    )
    stdlib_component = _copied_component(
        stdlib / "os.py", app_root, Path(config["base"]), **provenance_arguments
    )
    copied_components = {
        path: _copied_component(
            Path(source), app_root, Path(config["base"]), **provenance_arguments
        )
        for path, source in origins.items()
    }
    inventory_bytes = canonical_json_bytes(
        dict(
            version=1,
            python=config,
            copied_components=copied_components,
            standard_library=dict(component=stdlib_component, files=copied_python_files),
            package_metadata="Python dist-info and npm package.json files remain in payload",
            cpu_policy="conservative x86-64-v3 support boundary, not vendor minimum",
            native_files=sorted(inventory, key=lambda item: str(item["path"])),
            dynamic_load_policy=(
                "ctypes, ICU, OpenSSL and native package operations require target execution proof"
            ),
            execution_exclusions=[
                "Windows assets",
                "dynamic musl variants",
                "Tk GUI, provider network calls, browser downloads",
                "OpenSSL legacy/FIPS providers and arbitrary ctypes dlopen requests",
            ],
            packaging_tools=["readelf", "patchelf"],
        )
    )
    inventory_path = app_root / RUNTIME_INVENTORY_PATH
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(inventory_bytes)
    contract = RuntimeContract(
        python_version=config["version"], inventory_sha256=runtime_bytes_digest(inventory_bytes)
    )
    (app_root / RUNTIME_CONTRACT_PATH).write_bytes(canonical_json_bytes(contract.as_dict()))
    return contract
