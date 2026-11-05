"""The iOS merge upgrades the Mac-status database without losing contact evidence."""

from contextlib import closing
from pathlib import Path

from tests.unit.test_memory_notice_migration import _database_at_revision

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
