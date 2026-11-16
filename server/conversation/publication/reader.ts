/** Durable attention for one dirty conversation on the source transaction. */
import type { ReadSql } from '../../core/database/contracts.ts';
import type { SignalDurableFacts } from '../contracts.ts';

export function read_durable_facts(
  sql: ReadSql,
  conversation_id: string,
): SignalDurableFacts | null {
  const row = sql.get<{
    owner_read_through_sequence: number;
    latest_owner_message: number | null;
    latest_turn_ending: string | null;
    has_live_background_task: number;
  }>(
    `
    SELECT c.owner_read_through_sequence,
      (SELECT MAX(sequence) FROM conversation_events e
        WHERE e.conversation_id=c.conversation_id AND e.kind='message_to_owner')
        AS latest_owner_message,
      (SELECT CASE WHEN e.kind='turn_ended' THEN json_extract(e.payload,'$.ending')
        ELSE NULL END FROM conversation_events e
        WHERE e.conversation_id=c.conversation_id AND e.kind IN ('prompt','turn_ended')
          AND e.sequence>COALESCE((SELECT through_sequence
            FROM conversation_error_acknowledgements a
            WHERE a.conversation_id=c.conversation_id),0)
        ORDER BY e.sequence DESC LIMIT 1) AS latest_turn_ending,
      EXISTS(SELECT 1 FROM conversation_background_tasks b
        WHERE b.conversation_id=c.conversation_id) AS has_live_background_task
    FROM conversations c WHERE c.conversation_id=?
  `,
    [conversation_id],
  );
  if (!row) return null;
  return {
    message_for_owner:
      row.latest_owner_message !== null &&
      row.latest_owner_message > row.owner_read_through_sequence,
    last_turn_failed: row.latest_turn_ending === 'failed',
    has_live_background_task: !!row.has_live_background_task,
  };
}
