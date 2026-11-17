/** Canonical durable attention, read in the caller's source transaction. */
import type { ReadSql } from '../../core/database/contracts.ts';
import type { SignalDurableFacts } from '../contracts.ts';

interface DurableRow {
  conversation_id: string;
  owner_read_through_sequence: number | null;
  latest_owner_message: number | null;
  latest_turn_ending: string | null;
  has_live_background_task: number;
}

/**
 * Each conversation row carries large text columns, so its read-through mark is read
 * only when the conversation has an owner message to compare it with.
 */
const durable_facts_sql = (conversations: string) =>
  'SELECT f.conversation_id,f.latest_owner_message,f.latest_turn_ending,f.has_live_background_task,' +
  'CASE WHEN f.latest_owner_message IS NULL THEN NULL ELSE (SELECT o.owner_read_through_sequence ' +
  'FROM conversations o WHERE o.conversation_id=f.conversation_id) END AS owner_read_through_sequence ' +
  'FROM (SELECT c.conversation_id,' +
  '(SELECT MAX(sequence) FROM conversation_events e WHERE ' +
  "e.conversation_id=c.conversation_id AND e.kind='message_to_owner') AS latest_owner_message," +
  "(SELECT CASE WHEN e.kind='turn_ended' THEN json_extract(e.payload,'$.ending') " +
  'ELSE NULL END FROM conversation_events e WHERE e.conversation_id=c.conversation_id ' +
  "AND e.kind IN ('prompt','turn_ended') AND e.sequence>COALESCE(" +
  '(SELECT through_sequence FROM conversation_error_acknowledgements a WHERE ' +
  'a.conversation_id=c.conversation_id),0) ORDER BY e.sequence DESC LIMIT 1) AS latest_turn_ending,' +
  'EXISTS(SELECT 1 FROM conversation_background_tasks b ' +
  'WHERE b.conversation_id=c.conversation_id) AS has_live_background_task ' +
  `FROM conversations c${conversations}) f ORDER BY f.conversation_id`;

function durable_facts(
  rows: DurableRow[],
): { conversation_id: string; durable: SignalDurableFacts }[] {
  return rows.map((row) => ({
    conversation_id: row.conversation_id,
    durable: {
      message_for_owner:
        row.latest_owner_message !== null &&
        row.latest_owner_message > row.owner_read_through_sequence!,
      last_turn_failed: row.latest_turn_ending === 'failed',
      has_live_background_task: !!row.has_live_background_task,
    },
  }));
}

/** Read durable facts in batches over the complete source. */
export function readAllDurableFacts(
  sql: ReadSql,
): { conversation_id: string; durable: SignalDurableFacts }[] {
  return durable_facts(sql.all<DurableRow>(durable_facts_sql('')));
}

/** Read durable facts for only the named conversations; unknown ids are omitted. */
export function readDurableFactsFor(
  sql: ReadSql,
  conversation_ids: Iterable<string>,
): { conversation_id: string; durable: SignalDurableFacts }[] {
  return durable_facts(
    sql.all<DurableRow>(
      durable_facts_sql(' WHERE c.conversation_id IN (SELECT value FROM json_each(?))'),
      [JSON.stringify([...conversation_ids])],
    ),
  );
}
