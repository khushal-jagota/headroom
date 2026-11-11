"""Select area lists for pull requests; branch pushes always run every list."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def select_lists(root: Path, base: str | None) -> list[str]:
    lists = sorted((root / "tests/compare/lists").glob("unit-*.json"))
    if base is None:
        return [str(path.relative_to(root)) for path in lists]
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", base, "HEAD"],
        cwd=root,
        text=True,
    ).splitlines()
    # Area files declare their Python source roots. Shared runtime or harness changes
    # cannot be attributed safely to one area, so they exercise all lists.
    shared = (
        "tests/compare/",
        "server/",
        "src/planner/core/",
        "tests/support/",
        ".github/workflows/typescript-backend.yml",
        "requirements.txt",
        "pyproject.toml",
    )
    if any(path.startswith(shared) for path in changed):
        return [str(path.relative_to(root)) for path in lists]
    selected = []
    known_roots: list[str] = []
    for path in lists:
        roots = json.loads(path.read_text()).get("source_roots", [])
        known_roots.extend(roots)
        if not roots or any(name.startswith(tuple(roots)) for name in changed):
            selected.append(str(path.relative_to(root)))
    if any(
        name.startswith("src/planner/") and not name.startswith(tuple(known_roots))
        for name in changed
    ):
        return [str(path.relative_to(root)) for path in lists]
    return selected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    arguments = parser.parse_args()
    for selected_list in select_lists(Path(__file__).resolve().parents[2], arguments.base):
        print(selected_list)
