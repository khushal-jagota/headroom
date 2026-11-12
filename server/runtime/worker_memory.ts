/** Route-required memory reads. Automatic scans and notice dispatch belong to Unit 9. */
import { randomUUID } from 'node:crypto';
import type { ReadSql } from '../core/database/contracts.ts';
export const WORKER_STEP_MESSAGE_PREFIX = 'worker_step_message_';
export const ANSWERING_SENDER_MESSAGE_PREFIX = 'worker_step_';
export function new_worker_step_message_id(): string {
  return WORKER_STEP_MESSAGE_PREFIX + randomUUID().replaceAll('-', '');
}
export function memory_was_lost_unanswered(options: {
  automatically_compacted_through_sequence: number;
  latest_answering_prompt_sequence: number | null;
}): boolean {
  return (
    options.latest_answering_prompt_sequence !== null &&
    options.automatically_compacted_through_sequence > options.latest_answering_prompt_sequence
  );
}
export function conversation_holds_an_unanswered_memory_loss(
  sql: ReadSql,
  conversation_id: string | null,
): boolean {
  if (conversation_id === null) return false;
  const row = sql.get<{
    automatically_compacted_through_sequence: number;
  }>('SELECT automatically_compacted_through_sequence FROM conversations WHERE conversation_id=?', [
    conversation_id,
  ]);
  if (!row) return false;
  return memory_was_lost_unanswered({
    automatically_compacted_through_sequence: row.automatically_compacted_through_sequence,
    latest_answering_prompt_sequence: _latest_answering_prompt_sequence(sql, conversation_id),
  });
}
function _latest_answering_prompt_sequence(sql: ReadSql, conversation_id: string): number | null {
  const row = sql.get<{
    sequence: number | null;
  }>(
    "SELECT MAX(sequence) sequence FROM conversation_events WHERE conversation_id=? AND kind='prompt' " +
      "AND substr(json_extract(payload,'$.sender_message_id'),1,?)=?",
    [conversation_id, ANSWERING_SENDER_MESSAGE_PREFIX.length, ANSWERING_SENDER_MESSAGE_PREFIX],
  );
  return row?.sequence ?? null;
}
