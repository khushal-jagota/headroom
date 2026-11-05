"""The iOS merge upgrades the Mac-status database without losing contact evidence."""

from contextlib import closing
from pathlib import Path
from shutil import copytree
from unittest.mock import patch

from tests.unit.test_memory_notice_migration import _database_at_revision

from planner.core import db
from planner.core.db import connect, create_schema


def test_native_push_upgrade_preserves_existing_mac_contact(tmp_path: Path) -> None:
    path = _database_at_revision(tmp_path, "mac_contact_status")
    with closing(connect(path)) as conn:
        conn.execute(
            "INSERT INTO mac_contact_status VALUES (1, 12345, ?)", ('{"reason":"retained"}',)
        )
        conn.commit()
    for _ in range(2):
        with closing(connect(path)) as conn:
            create_schema(conn)
            assert tuple(conn.execute("SELECT * FROM mac_contact_status").fetchone()) == (
                1,
                12345,
                '{"reason":"retained"}',
            )
            assert conn.execute("SELECT COUNT(*) FROM notification_apns_devices").fetchone()[0] == 0
            assert (
                conn.execute("SELECT COUNT(*) FROM notification_apns_deliveries").fetchone()[0] == 0
            )


def test_native_goal_holder_history_joins_and_reopens(tmp_path: Path) -> None:
    path = _database_at_revision(tmp_path, "reconcile_native_staging")
    with closing(connect(path)) as conn:
        # Both histories already contain native push tables; upgrade must not recreate them.
        conn.execute(
            "INSERT INTO notification_apns_devices "
            "(device_id, owner_id, device_token, bundle_id, environment, "
            "registration_version, created_at, updated_at) "
            "VALUES ('retained', 'owner', 'token', 'topic', 'development', 1, 1, 1)"
        )
        conn.commit()
    for _ in range(2):
        with closing(connect(path)) as conn:
            create_schema(conn)
            assert (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "reconcile_native_goal_holders"
            )
            assert (
                conn.execute(
                    "SELECT registration_version FROM notification_apns_devices "
                    "WHERE device_id='retained'"
                ).fetchone()[0]
                == 1
            )
            assert {row[1] for row in conn.execute("PRAGMA table_info(goals)")} >= {
                "holder_kind",
                "holder_id",
            }
            assert conn.execute("SELECT COUNT(*) FROM chief_page").fetchone()[0] == 1


def test_original_main_goal_holder_history_keeps_contact(tmp_path: Path) -> None:
    historical = tmp_path / "main-history"
    copytree(db.MIGRATIONS_DIRECTORY, historical)
    native = historical / "versions/native_push_deliveries.py"
    native.write_text(
        native.read_text().replace(
            'down_revision = "notice_suppression_audit"', 'down_revision = "mac_contact_status"'
        )
    )
    with patch.object(db, "MIGRATIONS_DIRECTORY", historical):
        path = _database_at_revision(tmp_path, "goal_holders")
    with closing(connect(path)) as conn:
        conn.execute(
            "INSERT INTO mac_contact_status VALUES (1, 12345, ?)", ('{"reason":"retained"}',)
        )
        conn.commit()
    for _ in range(2):
        with closing(connect(path)) as conn:
            create_schema(conn)
            assert tuple(conn.execute("SELECT * FROM mac_contact_status").fetchone()) == (
                1,
                12345,
                '{"reason":"retained"}',
            )
            assert (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "reconcile_native_goal_holders"
            )
