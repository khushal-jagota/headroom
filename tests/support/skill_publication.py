"""Spawn-safe operations for real managed-home publication races."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.synchronize import Event
from pathlib import Path

from pytest import MonkeyPatch

from planner import managed_skills
from planner.core.db import connect
from planner.worker_settings import service


def publication_process(
    database_path: str,
    operation: str,
    patch: dict[str, object],
    attempted: Event,
    acquired: Event,
    paused: Event,
    release: Event,
    pause: bool,
) -> None:
    original_lock = managed_skills.skill_publication_lock

    @contextmanager
    def observed_lock(parent: Path | str) -> Iterator[Path]:
        attempted.set()
        with original_lock(parent) as root:
            acquired.set()
            yield root

    patches = MonkeyPatch()
    patches.setattr(service, "skill_publication_lock", observed_lock)
    patches.setattr(managed_skills, "skill_publication_lock", observed_lock)
    if pause and operation == "edit":
        original_publish = service._publish_skill

        def publish(*args: object, **kwargs: object) -> None:
            paused.set()
            if not release.wait(15):
                raise RuntimeError("parent did not release the editor")
            original_publish(*args, **kwargs)  # type: ignore[arg-type]

        service._publish_skill = publish
    if pause and operation == "startup":
        original_read = managed_skills.read_all_skill_sources

        def read(conn: object) -> dict[str, str]:
            snapshot = original_read(conn)  # type: ignore[arg-type]
            paused.set()
            if not release.wait(15):
                raise RuntimeError("parent did not release startup")
            return snapshot

        managed_skills.read_all_skill_sources = read
    conn = connect(database_path)
    try:
        parent = Path(database_path).parent
        if operation == "startup":
            managed_skills.write_skill_home(conn, parent)
        else:
            service.save_skill(conn, "panels", patch, now=1, database_parent=parent)
    finally:
        conn.close()
