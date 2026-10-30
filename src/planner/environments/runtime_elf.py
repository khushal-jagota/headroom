"""Source loader contexts for build-only ELF dependency resolution."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from planner.environments.runtime_contracts import RuntimeContractError


@dataclass(frozen=True)
class ElfDynamic:
    needed: tuple[str, ...]
    rpath: tuple[str, ...]
    runpath: tuple[str, ...] | None
    interpreter: str | None


@dataclass(frozen=True)
class ElfObject:
    path: Path
    dynamic: ElfDynamic


def read_dynamic(path: Path) -> ElfDynamic:
    def read(flag: str) -> str:
        return subprocess.run(
            ["readelf", flag, str(path)], check=True, text=True, capture_output=True, timeout=60
        ).stdout

    output = read("-dW")
    header = read("-lW")
    interpreter = re.search(r"Requesting program interpreter: (.*?)\]", header)

    def paths(tag: str) -> tuple[str, ...]:
        entries = re.findall(r"\(" + tag + r"\).*?\[(.*?)\]", output)
        # Empty entries mean the working directory, which is not a packaging input.
        return tuple(part for entry in entries for part in entry.split(":"))

    return ElfDynamic(
        tuple(re.findall(r"\(NEEDED\).*?\[(.*?)\]", output)),
        paths("RPATH"),
        paths("RUNPATH") if "(RUNPATH)" in output else None,
        interpreter.group(1) if interpreter else None,
    )


def resolve_provider(
    name: str,
    requester: ElfObject,
    ancestors: tuple[ElfObject, ...],
    cache: dict[str, Path],
) -> Path:
    """Follow loader RPATH ancestry, with each ORIGIN owned by its declarer."""
    if "/" in name:
        raise RuntimeContractError(f"unsafe ELF dependency name: {requester.path}: {name}")
    searches: list[tuple[ElfObject, tuple[str, ...]]] = []
    if requester.dynamic.runpath is None:
        searches.extend(
            (owner, owner.dynamic.rpath)
            for owner in (requester, *ancestors)
            if owner.dynamic.runpath is None
        )
    else:
        searches.append((requester, requester.dynamic.runpath))
    for owner, entries in searches:
        def origin(_match: re.Match[str], directory: str = str(owner.path.parent)) -> str:
            return directory

        for entry in entries:
            expanded = re.sub(
                r"\$(?:\{ORIGIN\}|ORIGIN(?![A-Za-z0-9_]))",
                origin,
                entry,
            )
            directory = Path(expanded)
            if not entry or "$" in expanded or not directory.is_absolute():
                raise RuntimeContractError(
                    f"unsafe relative ELF search path: {owner.path}: {entry}"
                )
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
    if name in cache:
        return cache[name]
    raise RuntimeContractError(f"unresolved native dependency {name} required by {requester.path}")
