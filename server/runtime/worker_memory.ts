/** Route-required memory reads. Automatic scans and notice dispatch belong to Unit 9. */
import { randomUUID } from 'node:crypto';
import { can_execute_locale } from '../tickets/locale_configuration.ts';
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

export const MEMORY_NOTICE_MESSAGE_PREFIX = 'worker_step_memory_notice_';
export function new_memory_notice_message_id(): string {
  return MEMORY_NOTICE_MESSAGE_PREFIX + randomUUID().replaceAll('-', '');
}
export function an_answer_is_already_waiting(
  held_prompts: Iterable<import('../conversation/contracts.ts').HeldPrompt>,
): boolean {
  return [...held_prompts].some((prompt) =>
    (prompt.sender_message_id ?? '').startsWith(ANSWERING_SENDER_MESSAGE_PREFIX),
  );
}
export function ticket_ids_holding_an_unanswered_memory_loss(sql: ReadSql): string[] {
  return sql.all<{id: string}>(`SELECT id FROM tickets
    WHERE worker_step_claim='out' AND conversation_id IS NOT NULL`)
    .filter((row) => ticket_needs_memory_notice(sql, row.id)).map((row) => row.id);
}
export function ticket_needs_memory_notice(sql: ReadSql, ticket_id: string): boolean {
  const row = sql.get<{conversation_id: string | null; locale: import('../tickets/contracts.ts').TicketLocale}>(
    "SELECT conversation_id, locale FROM tickets WHERE id=? AND worker_step_claim='out'", [ticket_id]);
  if (!row || !can_execute_locale(sql, row.locale)) return false;
  if (!conversation_holds_an_unanswered_memory_loss(sql, row.conversation_id)) return false;
  return sql.get(`SELECT 1 FROM conversation_events e JOIN conversations c USING(conversation_id)
    WHERE e.conversation_id=? AND e.kind='prompt_delivery_uncertain'
      AND e.sequence>c.automatically_compacted_through_sequence
      AND substr(json_extract(e.payload,'$.sender_message_id'),1,?)=? LIMIT 1`,
    [row.conversation_id, MEMORY_NOTICE_MESSAGE_PREFIX.length, MEMORY_NOTICE_MESSAGE_PREFIX]) === undefined;
}
