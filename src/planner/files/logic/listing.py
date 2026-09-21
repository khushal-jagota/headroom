"""Fold a directory of managed files into the things a reader would open.

The flat truth — every file at every depth — is what an agent needs to find and write its
own files. It is not what a person reads. A design exploration of twenty-four files is one
thing on the page, not twenty-four, so this is the rule that turns a directory into entries
rather than paths. It takes no database and no request, so any surface that lists managed
files can use it.
"""

from __future__ import annotations

from pathlib import Path

from planner.files.contracts import ArtifactEntry

INDEX_FILE_NAME = "index.html"


def fold_directory(root: Path, path_prefix: str = "") -> tuple[ArtifactEntry, ...]:
    """Return what is directly in `root`, with every directory folded to one entry.

    `path_prefix` goes in front of each path an entry opens, so a caller that serves the
    directory from a longer address gets back paths it can hand straight to a link. Entries
    come newest first, because the strip that shows them puts recent work at its head.
    """
    if root.is_symlink() or not root.is_dir():
        return ()
    entries: list[ArtifactEntry] = []
    for path in _readable_children(root):
        opened_path = f"{path_prefix}{path.name}"
        if path.is_file():
            entries.append(
                ArtifactEntry(
                    name=path.name,
                    opens=opened_path,
                    modified_at=path.stat().st_mtime,
                    children=(),
                )
            )
            continue
        index = path / INDEX_FILE_NAME
        if index.is_file() and not index.is_symlink():
            entries.append(
                ArtifactEntry(
                    name=path.name,
                    opens=f"{opened_path}/{INDEX_FILE_NAME}",
                    modified_at=_newest_modified_at(path),
                    children=(),
                )
            )
            continue
        children = fold_directory(path, f"{opened_path}/")
        # A directory that folds to nothing opens nothing, so it is not a thing on the page.
        if children:
            entries.append(
                ArtifactEntry(
                    name=path.name,
                    opens=None,
                    modified_at=max(child.modified_at for child in children),
                    children=children,
                )
            )
    entries.sort(key=lambda entry: (-entry.modified_at, entry.name.lower(), entry.name))
    return tuple(entries)


def entries_as_json(entries: tuple[ArtifactEntry, ...]) -> list[dict[str, object]]:
    return [
        {
            "name": entry.name,
            "opens": entry.opens,
            "modified_at": entry.modified_at,
            "children": entries_as_json(entry.children),
        }
        for entry in entries
    ]


def _readable_children(directory: Path) -> list[Path]:
    """Files and directories directly inside, skipping symlinks as the flat listing does."""
    return [
        path
        for path in sorted(directory.iterdir())
        if not path.is_symlink() and (path.is_file() or path.is_dir())
    ]


def _newest_modified_at(directory: Path) -> float:
    """When a folded directory last changed, which is when its newest file was written.

    Directory times are not counted. A directory is touched when anything inside it is
    added or removed, so counting them would make a folder look newer than every file a
    reader would actually find in it.
    """
    times = [
        path.stat().st_mtime if path.is_file() else _newest_modified_at(path)
        for path in _readable_children(directory)
    ]
    return max(times, default=0.0)
