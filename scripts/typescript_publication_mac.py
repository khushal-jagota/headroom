"""Call the unchanged selected-source Mac validator from host Python only."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 6:
        raise ValueError(
            "Expected Mac directory, source SHA, run ID, artifact ID and build result"
        )
    directory, requested, run_id, artifact_id, build_result = sys.argv[1:]
    if any(
        re.fullmatch(r"[1-9][0-9]*", value) is None for value in (run_id, artifact_id)
    ):
        raise ValueError("Invalid Mac publication identifier")
    source = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(source), str(source / "src")]
    from scripts.update_publication import validate_mac_publication

    result = validate_mac_publication(
        Path(directory),
        requested=requested,
        run_id=int(run_id),
        artifact_id=int(artifact_id),
        build_result=build_result,
    )
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
