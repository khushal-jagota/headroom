"""Select requested-source checks using the trusted control checkout's map.

This command only describes checks. The workflow runs them directly and separately.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Literal, cast

from planner.environments.logic.related_checks import select_paths, test_suite
from planner.environments.related_checks_contracts import (
    SUITES,
    RelatedDomain,
    RelatedSelection,
    Suite,
)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    """Parse --name-status -z --no-renames, retaining deletion paths and whitespace."""
    if not raw:
        return []
    pieces = raw.split(b"\0")
    if pieces.pop() != b"" or len(pieces) % 2:
        raise ValueError("Malformed NUL-delimited Git diff")
    result = []
    for offset in range(0, len(pieces), 2):
        status = pieces[offset].decode("ascii")
        path = pieces[offset + 1].decode("utf-8")
        if status not in {"A", "D", "M", "T"} or not path:
            raise ValueError(f"Unsupported Git diff entry: {status!r} {path!r}")
        result.append((status, path))
    return result


def load_map(path: Path) -> tuple[tuple[RelatedDomain, ...], tuple[str, ...]]:
    raw = json.loads(path.read_text())
    domains = []
    for row in raw["domains"]:
        tests: dict[Suite, tuple[str, ...]] = {}
        for kind, paths in row["tests"].items():
            if kind not in SUITES or not isinstance(paths, list):
                raise ValueError("Invalid suite map")
            if any(not isinstance(item, str) or test_suite(item) != kind for item in paths):
                raise ValueError("Map entries must be explicit test paths in their suite")
            tests[cast(Suite, kind)] = tuple(paths)
        patterns = row["patterns"]
        if not patterns or any(not isinstance(item, str) for item in patterns):
            raise ValueError("Invalid domain patterns")
        domains.append(RelatedDomain(row["name"], tuple(patterns), tests))
    docs = raw["documentation"]
    if not isinstance(docs, list) or any(not isinstance(item, str) for item in docs):
        raise ValueError("Invalid documentation map")
    return tuple(domains), tuple(docs)


def select_release(
    source_root: Path,
    requested_sha: str,
    control_sha: str,
    base_sha: str | None,
    map_path: Path | None = None,
) -> RelatedSelection:
    for label, sha in (("requested", requested_sha), ("control", control_sha)):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"{label} SHA must be a full lowercase commit SHA")
    head = git(source_root, "rev-parse", "HEAD").decode().strip()
    if head != requested_sha:
        raise ValueError("Requested source checkout HEAD differs from requested SHA")
    # Tracked requested-revision files, not current-control tests or untracked local files.
    tracked = git(source_root, "ls-tree", "-r", "--name-only", "-z", requested_sha)
    inventory: dict[Suite, tuple[str, ...]] = {suite: () for suite in SUITES}
    for suite in SUITES:
        inventory[suite] = tuple(
            path for value in tracked.split(b"\0")
            if (path := value.decode()) and test_suite(path) == suite
            and (suite in ("vitest", "browser") or Path(path).name.startswith("test_"))
        )
    browser_format = "legacy"
    catalog_path = "web/tests/browser/catalog.json"
    if catalog_path.encode() in tracked.split(b"\0"):
        # Capability comes from the tracked requested revision, never a result file.
        catalog = json.loads(git(source_root, "show", f"{requested_sha}:{catalog_path}"))
        if catalog.get("schema") != 1 or not isinstance(catalog.get("entries"), list):
            raise ValueError("Unsupported requested-source browser catalog")
        browser_paths = tuple(entry["path"] for entry in catalog["entries"])
        if (not browser_paths or len(set(browser_paths)) != len(browser_paths)
                or any(path not in inventory["browser"] for path in browser_paths)):
            raise ValueError("Requested browser catalog entries are missing or duplicated")
        inventory["browser"] = browser_paths
        browser_format = "shared-v1"
    map_unavailable = False
    try:
        domains, docs = load_map(map_path or Path(__file__).with_name("related_checks_map.json"))
    except FileNotFoundError:
        domains, docs = (), ()
        map_unavailable = True
    changes: list[tuple[str, str]] = []
    unavailable: str | None = (
        "Selector map unavailable; selecting all default suites" if map_unavailable else None
    )
    if base_sha is None:
        unavailable = "No healthy resolved served baseline; selecting all default suites"
    else:
        try:
            if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
                raise ValueError("Invalid base SHA")
            git(source_root, "cat-file", "-e", f"{base_sha}^{{commit}}")
        except (subprocess.CalledProcessError, ValueError):
            unavailable = "Served baseline unavailable; selecting all default suites"
        else:
            changes = parse_name_status(git(
                source_root, "diff", "--name-status", "-z", "--no-renames",
                base_sha, requested_sha,
            ))
    selected, broad, reasons = select_paths(changes, inventory, domains, docs, unavailable)
    if broad and any(not inventory[suite] for suite in SUITES):
        raise ValueError("Broad selection requires nonempty coverage in every default suite")
    for paths in selected.values():
        for path in paths:
            if not (source_root / path).is_file():
                raise ValueError(f"Selected requested-source test is absent: {path}")
    backend = broad or any(path.startswith(("src/", "tests/", "agent_backends/"))
                           for _, path in changes)
    web_check = broad or any(path.startswith(("web/", "assets/")) for _, path in changes)
    ruff_paths = sorted({path for status, path in changes
                         if status != "D" and path.endswith(".py")}
                        | {path for suite in ("unit", "integration", "e2e")
                           for path in selected[cast(Suite, suite)]})
    if broad:
        ruff_paths = sorted(set(ruff_paths) | {
            value.decode() for value in tracked.split(b"\0")
            if value.endswith(b".py") and value.startswith((b"src/", b"tests/"))
        })
    tools = {"git", "python"}
    if any(selected[cast(Suite, suite)] for suite in ("unit", "integration", "e2e")):
        tools.add("pytest")
    if ruff_paths:
        tools.add("ruff")
    if backend:
        tools.add("mypy")
    if web_check or selected["vitest"] or selected["browser"] or selected["e2e"]:
        tools.update(("npm", "node"))
    if selected["browser"] or selected["e2e"]:
        tools.add("playwright")
    if selected["browser"] or selected["e2e"]:
        tools.add("bwrap")
    return RelatedSelection(
        browser_format=cast("Literal[\"legacy\", \"shared-v1\"]", browser_format),
        requested_sha=requested_sha, control_sha=control_sha, base_sha=base_sha,
        broad=broad, reasons=reasons, changed_paths=[path for _, path in changes],
        unit=selected["unit"], integration=selected["integration"], e2e=selected["e2e"],
        vitest=selected["vitest"], browser=selected["browser"], ruff=bool(ruff_paths),
        ruff_paths=ruff_paths, mypy=backend, web_check=web_check, required_tools=sorted(tools),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--requested-sha", required=True)
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--base-sha")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = select_release(
            args.source_root, args.requested_sha, args.control_sha, args.base_sha,
        )
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Related check selection refused: {error}\n")
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
