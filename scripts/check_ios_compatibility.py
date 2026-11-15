#!/usr/bin/env python3
"""Fail publication if a native-facing contract changed without fresh review."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = Path("ios/compatibility-review.json")


def contract_digest(root: Path) -> str:
    # Serializers and domain logic can break native clients without a route edit.
    # There is no narrower canonical surface inventory, so review all backend code.
    paths = sorted(
        path
        for path in root.glob("src/planner/**/*.py")
        if "skills" not in path.relative_to(root).parts
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def minimum_build(root: Path) -> int:
    tree = ast.parse((root / "src/planner/ios_compatibility/policy.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MINIMUM_SUPPORTED_BUILD"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if type(value) is int and value > 0:
                return value
    raise ValueError("minimum supported build must be a positive integer")


def check(root: Path) -> None:
    review = json.loads((root / REVIEW).read_text())
    if review.get("contract_sha256") != contract_digest(root):
        raise ValueError(
            "Native-facing contracts changed. Review iOS compatibility "
            "and refresh the source-bound review."
        )
    if review.get("oldest_supported_build") != minimum_build(root):
        raise ValueError("Review must name the policy's oldest supported build.")
    if review.get("decision") not in {"compatible", "minimum_raised"}:
        raise ValueError("Record compatible or minimum_raised after review.")
    if not isinstance(review.get("evidence"), str) or len(review["evidence"].strip()) < 40:
        raise ValueError(
            "Record concrete check evidence, or the break that required a minimum increase."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest", action="store_true")
    args = parser.parse_args()
    if args.digest:
        print(contract_digest(ROOT))
        return 0
    try:
        check(ROOT)
    except (ValueError, OSError) as error:
        print(f"iOS compatibility check failed: {error}")
        return 1
    print("iOS compatibility review matches native-facing contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
