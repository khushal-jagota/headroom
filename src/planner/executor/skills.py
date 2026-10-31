"""Exact canonical skill snapshots and serialized native account bootstrap."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import PlannerError
from planner.environments.hermes_home import resolve_planner_home
from planner.executor.configuration import ExecutorPaths
from planner.executor.protocol import CanonicalSkillSnapshot, CanonicalSkillSource
from planner.managed_skills import read_all_skill_sources, skill_description
from planner.skill_sources import RETIRED_PANELS_SKILL_NAMES, provision_native_backend_skills
from planner.worker_types.store import read_definitions


def native_skill_home(backend: ConversationBackendKey) -> Path:
    """Use the same native account home as the selected provider."""
    if backend is ConversationBackendKey.codex:
        return codex_home(os.environ)
    if backend is ConversationBackendKey.hermes:
        return resolve_planner_home()
    configured_home = os.environ.get("CLAUDE_CONFIG_DIR", "")
    return Path(configured_home).expanduser() if configured_home else Path.home() / ".claude"


def validate_skill_snapshot(snapshot: CanonicalSkillSnapshot) -> None:
    """Refuse incomplete or unsafe input without changing its source text."""
    try:
        names: set[str] = set()
        folded_names: set[str] = set()
        for source in snapshot.sources:
            name = source.skill_name
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
                raise ValueError("unsafe skill name")
            if name.casefold() in folded_names:
                raise ValueError("duplicate skill name")
            names.add(name)
            folded_names.add(name.casefold())
            source.source_text.encode("utf-8", errors="strict")
            skill_description(source.source_text, name)
        required = snapshot.required_skill_names
        if len(set(required)) != len(required):
            raise ValueError("duplicate required skill name")
        if not {"panels", "panels-worker"}.issubset(required):
            raise ValueError("base Worker skills are not required")
        if set(required) & RETIRED_PANELS_SKILL_NAMES:
            raise ValueError("required canonical skill is retired")
        if not set(required).issubset(names):
            raise ValueError("required canonical skill is absent")
    except (ValueError, TypeError, AttributeError, PlannerError) as exc:
        raise BackendSpawnFailed(f"invalid canonical skill snapshot: {exc}") from exc


def read_canonical_skill_snapshot(
    conn: sqlite3.Connection, ticket_id: str
) -> CanonicalSkillSnapshot:
    """Read authority in the caller's transaction, without seeding or committing."""
    try:
        row = conn.execute("SELECT worker_type FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise ValueError("assigned Ticket does not exist")
        definition = next(
            (item for item in read_definitions(conn) if item.worker_type == row["worker_type"]),
            None,
        )
        if definition is None:
            raise ValueError("assigned Ticket Worker type does not exist")
        required = tuple(
            dict.fromkeys(("panels", "panels-worker", definition.worker_profile.specialist_skill))
        )
        snapshot = CanonicalSkillSnapshot(
            sources=tuple(
                CanonicalSkillSource(name, text)
                for name, text in read_all_skill_sources(conn).items()
            ),
            required_skill_names=required,
        )
        validate_skill_snapshot(snapshot)
        return snapshot
    except (sqlite3.Error, ValueError, PlannerError) as exc:
        raise BackendSpawnFailed(f"cannot read canonical skill snapshot: {exc}") from exc


def _owned_directory(path: Path) -> None:
    """Create one executor-owned directory without following a symlink."""
    if path.is_symlink():
        raise ValueError(f"executor skill directory is a symlink: {path}")
    path.mkdir(exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"executor skill path is not a directory: {path}")


def _generation_digest(snapshot: CanonicalSkillSnapshot) -> str:
    digest = hashlib.sha256()
    for source in sorted(snapshot.sources, key=lambda item: item.skill_name):
        for value in (source.skill_name, source.source_text):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _check_generation(generation: Path, snapshot: CanonicalSkillSnapshot) -> None:
    root = generation / "skills"
    if generation.is_symlink() or root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe canonical skill generation")
    expected = {source.skill_name for source in snapshot.sources}
    if {item.name for item in root.iterdir()} != expected:
        raise ValueError("canonical skill generation content differs")
    for source in snapshot.sources:
        folder = root / source.skill_name
        source_file = folder / "SKILL.md"
        if folder.is_symlink() or source_file.is_symlink():
            raise ValueError("canonical skill generation contains symlinks")
        if {item.name for item in folder.iterdir()} != {"SKILL.md"}:
            raise ValueError("canonical skill directory content differs")
        if not source_file.is_file() or source_file.read_bytes() != source.source_text.encode(
            "utf-8"
        ):
            raise ValueError("canonical skill generation bytes differ")


def _install_account_links(
    native_home: Path, generation: Path, generations: Path, snapshot: CanonicalSkillSnapshot
) -> None:
    # The canonical provisioner intentionally excludes historical Panels names.
    # All rows remain in the immutable snapshot without reviving retired paths.
    target = native_home / "skills"
    if not target.exists() and not target.is_symlink():
        target.mkdir()
    provision_native_backend_skills(native_home, generation)
    if target.is_symlink():
        return  # A whole-root link now names exactly the current generation.
    names = {source.skill_name for source in snapshot.sources}
    for entry in target.iterdir():
        if entry.name not in names and entry.is_symlink():
            destination = entry.resolve(strict=False)
            if destination.is_relative_to(generations):
                entry.unlink()


def install_native_skill_snapshot(
    snapshot: CanonicalSkillSnapshot, *, paths: ExecutorPaths, native_home: Path
) -> Path:
    """Publish one complete generation, then expose it before provider creation."""
    validate_skill_snapshot(snapshot)
    try:
        # Ancestors outside the owned root follow the platform's normal paths.
        paths.root.parent.mkdir(parents=True, exist_ok=True)
        _owned_directory(paths.root)
        _owned_directory(paths.skills)
        lock_path = paths.skills / ".install.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, "rb") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError("canonical skill lock is not a regular file")
            fcntl.flock(lock, fcntl.LOCK_EX)
            generation = paths.skills / _generation_digest(snapshot)
            if generation.exists() or generation.is_symlink():
                _check_generation(generation, snapshot)
            else:
                stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=paths.skills))
                try:
                    (stage / "skills").mkdir()
                    for source in snapshot.sources:
                        folder = stage / "skills" / source.skill_name
                        folder.mkdir()
                        (folder / "SKILL.md").write_bytes(source.source_text.encode("utf-8"))
                    stage.rename(generation)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
            native_home.mkdir(parents=True, exist_ok=True)
            _install_account_links(native_home, generation, paths.skills.resolve(), snapshot)
            return generation
    except (OSError, ValueError, RuntimeError) as exc:
        raise BackendSpawnFailed(f"cannot install canonical skill snapshot: {exc}") from exc
