"""Let the installed 53d64f0fd adopt job install this release. Bridge only.

The next release deletes this file, together with the `adopt` job that runs it.

The workflow checks out the installed release and runs this from the new source
against that checkout, before the old runner starts. Two old checks refuse this
release for reasons that are no longer true, and nothing else changes:

- runtime admission probes two native tools this release no longer ships;
- the schema floor `video_upload_ledger` predates this release's migration graph,
  whose one entry revision is `goal_holders`, the live database's revision.

Every original text must occur exactly once, or nothing is written. Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "src/planner/environments/runtime_admission.py": (
        ('        modules / "@anthropic-ai/claude-agent-sdk-linux-x64/claude",\n', ""),
        ('        vendor / "codex-resources/zsh/bin/zsh",\n', ""),
    ),
    "src/planner/environments/update_runner.py": (
        (
            '    floor = "video_upload_ledger"\n',
            '    floor = "goal_holders" if graph.get_bases() == ["goal_holders"]'
            ' else "video_upload_ledger"\n',
        ),
    ),
}


def patched(relative_path: str, text: str) -> str:
    """Return the bridged text of one file, refusing anything but the exact original."""
    for original, replacement in PATCHES[relative_path]:
        if text.count(original) != 1:
            raise ValueError(f"{relative_path}: expected exactly one {original.strip()!r}")
        text = text.replace(original, replacement)
    return text


def main(checkout: Path) -> None:
    bridged = {
        path: patched(path, (checkout / path).read_text(encoding="utf-8")) for path in PATCHES
    }
    for path, text in bridged.items():
        (checkout / path).write_text(text, encoding="utf-8")
        print(f"bridged {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: bridge_legacy_adopt.py <installed-release-checkout>")
    try:
        main(Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Bridge patch refused: {exc}") from exc
