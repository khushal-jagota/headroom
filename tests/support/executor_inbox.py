"""Read the durable receipt when a test interrupts acceptance before it returns."""

from planner.core.db import connect
from planner.executor.protocol import AcceptanceReceipt, type_adapter
from planner.executor.vps_inbox import InboxStore


def lookup_receipt(
    store: InboxStore, sender_id: str, submission_id: str
) -> AcceptanceReceipt | None:
    connection = connect(store.db_path)
    try:
        row = connection.execute(
            "SELECT result_json FROM mac_inbox_positions WHERE sender_id=? AND submission_id=?",
            (sender_id, submission_id),
        ).fetchone()
        return type_adapter(AcceptanceReceipt).validate_json(row[0]) if row else None
    finally:
        connection.close()
