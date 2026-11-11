"""Legacy scratch roots must belong to the canonical database."""

from pathlib import Path

from planner.environments import ticket_scratch


def test_scratch_belongs_only_to_its_own_database(tmp_path: Path) -> None:
    live = str(tmp_path / "live.db")

    assert ticket_scratch.layout_for(tmp_path, live, live) is not None
    assert ticket_scratch.layout_for(tmp_path, live, str(tmp_path / "ticket.db")) is None
    assert ticket_scratch.layout_for(tmp_path, None, live) is None
