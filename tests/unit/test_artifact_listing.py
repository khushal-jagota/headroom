"""The rule that turns a directory of managed files into the things a reader would open."""

from __future__ import annotations

import os
from pathlib import Path

from planner.files.logic.listing import fold_directory


def _write(root: Path, relative_path: str, *, modified_at: float | None = None) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(relative_path, encoding="utf-8")
    if modified_at is not None:
        os.utime(path, (modified_at, modified_at))
    return path


def test_a_folder_with_an_index_is_one_entry_and_hides_what_the_index_loads(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "explore/index.html")
    _write(tmp_path, "explore/style.css")
    _write(tmp_path, "explore/img/card.png")
    _write(tmp_path, "explore/systems/minimal.md")

    entries = fold_directory(tmp_path)

    assert [entry.name for entry in entries] == ["explore"]
    assert entries[0].opens == "explore/index.html"
    assert entries[0].children == ()


def test_a_folder_without_an_index_carries_what_is_directly_inside(tmp_path: Path) -> None:
    _write(tmp_path, "open/one.md")
    _write(tmp_path, "open/two.md")
    _write(tmp_path, "open/deeper/three.md")

    (entry,) = fold_directory(tmp_path)

    assert entry.name == "open"
    assert entry.opens is None
    assert [child.name for child in entry.children] == ["deeper", "one.md", "two.md"]
    deeper = next(child for child in entry.children if child.name == "deeper")
    assert [grandchild.opens for grandchild in deeper.children] == ["open/deeper/three.md"]


def test_the_prefix_makes_paths_that_read_the_file_back(tmp_path: Path) -> None:
    _write(tmp_path, "site/index.html")
    _write(tmp_path, "loose.md")

    entries = fold_directory(tmp_path, "artifacts/")

    assert sorted(entry.opens or "" for entry in entries) == [
        "artifacts/loose.md",
        "artifacts/site/index.html",
    ]


def test_recent_work_comes_first_and_a_folder_is_as_new_as_its_newest_file(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "old.md", modified_at=1000)
    _write(tmp_path, "newest.md", modified_at=3000)
    _write(tmp_path, "folder/stale.md", modified_at=500)
    _write(tmp_path, "folder/fresh.md", modified_at=2000)

    entries = fold_directory(tmp_path)

    assert [entry.name for entry in entries] == ["newest.md", "folder", "old.md"]


def test_a_symlink_is_skipped_and_an_empty_folder_is_not_a_thing_on_the_page(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "real.md")
    (tmp_path / "link.md").symlink_to(tmp_path / "real.md")
    (tmp_path / "hollow").mkdir()
    (tmp_path / "hollow" / "deeper").mkdir()

    entries = fold_directory(tmp_path)

    assert [entry.name for entry in entries] == ["real.md"]


def test_a_root_that_is_not_there_folds_to_nothing(tmp_path: Path) -> None:
    assert fold_directory(tmp_path / "missing") == ()
