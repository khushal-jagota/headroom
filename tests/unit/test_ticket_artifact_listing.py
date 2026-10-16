from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.files.lifecycle import database_path
from planner.files.logic.paths import ticket_files_root
from planner.tickets import data, views


def _ticket(conn: Connection) -> str:
    return data.create_ticket(
        conn, title="Unlinked artifacts", worker_type="coding", principal=OWNER_PRINCIPAL,
        now=1, title_max_chars=200,
    ).id


def test_full_detail_lists_unlinked_artifacts_and_folds_folders(tmp_db: Connection) -> None:
    ticket_id = _ticket(tmp_db)
    root = ticket_files_root(database_path(tmp_db)) / ticket_id / "artifacts"
    (root / "site").mkdir(parents=True)
    (root / "site" / "index.html").write_text("site")
    (root / "site" / "style.css").write_text("style")
    (root / "notes").mkdir()
    (root / "notes" / "unlinked.md").write_text("unlinked")
    (root.parent / "proof.md").write_text("root file")
    entries = views.ticket_detail(tmp_db, ticket_id, 2)["artifacts"]
    assert {entry["name"] for entry in entries} == {"proof.md", "artifacts"}
    entries = next(entry for entry in entries if entry["name"] == "artifacts")["children"]
    by_name = {entry["name"]: entry for entry in entries}
    assert by_name["site"]["opens"] == "artifacts/site/index.html"
    assert by_name["site"]["children"] == []
    assert by_name["notes"]["opens"] is None
    assert by_name["notes"]["children"][0]["opens"] == "artifacts/notes/unlinked.md"


@pytest.mark.parametrize("ancestor", ["files", "tickets", "entity", "artifacts"])
def test_detail_does_not_follow_artifact_ancestor_symlinks(
    tmp_db: Connection, tmp_path: Path, ancestor: str,
) -> None:
    ticket_id = _ticket(tmp_db)
    base = Path(database_path(tmp_db)).parent
    components = ["files", "tickets", ticket_id, "artifacts"]
    index = ["files", "tickets", "entity", "artifacts"].index(ancestor)
    link = base.joinpath(*components[:index + 1])
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    remaining = outside.joinpath(*components[index + 1:])
    remaining.mkdir(parents=True, exist_ok=True)
    (remaining / "private.html").write_text("private")
    link.symlink_to(outside, target_is_directory=True)
    assert views.ticket_detail(tmp_db, ticket_id, 2)["artifacts"] == []


def test_detail_ignores_descendant_links_and_missing_artifacts(
    tmp_db: Connection, tmp_path: Path,
) -> None:
    ticket_id = _ticket(tmp_db)
    assert views.ticket_detail(tmp_db, ticket_id, 2)["artifacts"] == []
    root = ticket_files_root(database_path(tmp_db)) / ticket_id / "artifacts"
    root.mkdir(parents=True)
    outside = tmp_path / "private.html"
    outside.write_text("private")
    (root / "linked.html").symlink_to(outside)
    (root / "normal.html").write_text("normal")
    entries = views.ticket_detail(tmp_db, ticket_id, 2)["artifacts"]
    assert entries[0]["children"][0]["name"] == "normal.html"
    assert len(entries[0]["children"]) == 1
