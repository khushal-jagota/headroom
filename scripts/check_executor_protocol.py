"""Check that executor schema edits increase the matching public version.

Run with .venv/bin/python. --write records the current schema fingerprint;
--base also checks the immutable manifest from the pull request base commit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import cast

from planner.executor.protocol_schema import (
    ProtocolManifest,
    check_protocol_manifest,
    protocol_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("tests/fixtures/executor_protocol/manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Pull request base commit")
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    actual = protocol_manifest()
    if arguments.write:
        (ROOT / MANIFEST).write_text(json.dumps(actual, sort_keys=True, indent=2) + "\n")
    recorded = cast(ProtocolManifest, json.loads((ROOT / MANIFEST).read_text()))
    previous = None
    if arguments.base:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", f"{arguments.base}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "show", f"{revision}:{MANIFEST.as_posix()}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.returncode == 0:
            previous = cast(ProtocolManifest, json.loads(result.stdout))
        else:
            # Bootstrap only when that valid base commit has no manifest yet.
            present = subprocess.run(
                ["git", "ls-tree", revision, "--", MANIFEST.as_posix()],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            if present.stdout.strip():
                raise RuntimeError(result.stderr)
    check_protocol_manifest(actual, recorded, previous)
    print(
        f"Executor protocol {actual['protocol_version']}, "
        f"spool {actual['spool_format_version']}, "
        f"command journal {actual['command_journal_format_version']}: OK"
    )


if __name__ == "__main__":
    main()
