"""Canonical durable attention on the caller's transaction, without runtime guesses."""

import sqlite3

from planner.conversation.contracts import SignalDurableFacts


def read_durable_facts(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> SignalDurableFacts | None:
    row = connection.execute(
        "SELECT c.owner_read_through_sequence,"
        "(SELECT MAX(sequence) FROM conversation_events e WHERE "
        "e.conversation_id=c.conversation_id AND e.kind='message_to_owner'),"
        "(SELECT CASE WHEN e.kind='turn_ended' THEN json_extract(e.payload,'$.ending') "
        "ELSE NULL END FROM conversation_events e WHERE e.conversation_id=c.conversation_id "
        "AND e.kind IN ('prompt','turn_ended') AND e.sequence>COALESCE("
        "(SELECT through_sequence FROM conversation_error_acknowledgements a WHERE "
        "a.conversation_id=c.conversation_id),0) ORDER BY e.sequence DESC LIMIT 1) "
        "FROM conversations c WHERE c.conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return SignalDurableFacts(row[1] is not None and int(row[1]) > int(row[0]), row[2] == "failed")
