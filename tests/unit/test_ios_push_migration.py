"""The sweep baseline retains Mac presence and native push registration state."""

from contextlib import closing
from pathlib import Path

from planner.core.db import connect, create_schema


def test_baseline_reopen_preserves_existing_mac_presence(tmp_path: Path) -> None:
    path = str(tmp_path / "baseline.db")
    with closing(connect(path)) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,last_contact_at_ms,refusal_json) "
            "VALUES (1, 12345, ?)",
            ('{"reason":"retained"}',),
        )
        conn.commit()
    for _ in range(2):
        with closing(connect(path)) as conn:
            create_schema(conn)
            assert tuple(
                conn.execute(
                    "SELECT singleton,last_contact_at_ms,refusal_json FROM mac_executor_presence"
                ).fetchone()
            ) == (1, 12345, '{"reason":"retained"}')
            assert conn.execute("SELECT COUNT(*) FROM notification_apns_devices").fetchone()[0] == 0
            assert (
                conn.execute("SELECT COUNT(*) FROM notification_apns_deliveries").fetchone()[0] == 0
            )
