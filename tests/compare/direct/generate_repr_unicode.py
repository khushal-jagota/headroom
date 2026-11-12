"""Regenerate the repr Unicode delta from explicit official UnicodeData files.

Inputs: https://www.unicode.org/Public/{15.0.0,17.0.0}/ucd/UnicodeData.txt
This generator performs no network access. Review the generated diff on upgrades.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def assigned(path: Path) -> set[int]:
    points: set[int] = set()
    start = 0
    for line in path.read_text().splitlines():
        raw, name, *_ = line.split(";")
        point = int(raw, 16)
        if name.endswith(", First>"):
            start = point
        elif name.endswith(", Last>"):
            points.update(range(start, point + 1))
        else:
            points.add(point)
    return points


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unicode_15", type=Path)
    parser.add_argument("unicode_17", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    ranges: list[list[int]] = []
    for point in sorted(assigned(args.unicode_17) - assigned(args.unicode_15)):
        if ranges and ranges[-1][1] + 1 == point:
            ranges[-1][1] = point
        else:
            ranges.append([point, point])
    rows = "\n".join(f"  [0x{start:x}, 0x{end:x}]," for start, end in ranges)
    args.output.write_text(
        "/** Generated Unicode 17.0.0 assignments absent from Python 3.12 Unicode 15.0.0.\n"
        " * Source: unicode.org/Public/{15.0.0,17.0.0}/ucd/UnicodeData.txt\n"
        " * Regenerate with tests/compare/direct/generate_repr_unicode.py. */\n"
        "export const NODE_UNICODE_VERSION = '17.0';\n\n"
        "const newer_assignments: readonly (readonly [number, number])[] = [\n" + rows + "\n];\n\n"
        "export function assigned_after_unicode_15(point: number): boolean {\n"
        "  return newer_assignments.some(([start, end]) => point >= start && point <= end);\n"
        "}\n"
    )
    print(f"Generated {len(ranges)} ranges")


if __name__ == "__main__":
    main()
