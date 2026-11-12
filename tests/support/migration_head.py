"""The single head of the shipped migration scripts, for upgrade assertions."""

from alembic.script import ScriptDirectory

from planner.core import db


def shipped_migration_head() -> str:
    heads = ScriptDirectory(str(db.MIGRATIONS_DIRECTORY)).get_heads()
    assert len(heads) == 1, f"shipped migrations must have one head, found {heads}"
    return heads[0]
