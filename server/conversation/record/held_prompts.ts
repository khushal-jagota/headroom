/** Durable custody for the conversation's held line. */
import type { Database as FullDatabase, TransactionSql } from '../../core/database/contracts.ts';
import type { Message } from '../runtime/contracts.ts';
import { message_content_json_entries, message_content_from_stored } from '../message_content.ts';
import { uncertain_row } from '../runtime/messages.ts';
import {
  conversation_event_payload_from_canonical_json,
  conversation_event_payload_to_canonical_json,
} from './codec.ts';

export function save_held_prompt(
  database: Database,
  conversation_id: string,
  message: Message,
  position: number,
): void {
  const {
    recording_receipt: _receipt,
    content: _content,
    recovery_references: _references,
    ...values
  } = message;
  const payload = JSON.stringify({
    ...values,
    ...message_content_json_entries(message.content),
    canonical: conversation_event_payload_to_canonical_json(
      uncertain_row(message, message.requested_mode ?? 'queue', 'delivery uncertain'),
    ),
  });
  database.write(
    (tx) => {
      tx.run('DELETE FROM conversation_held_prompts WHERE held_prompt_id=? AND conversation_id=?', [
        message.held_prompt_id,
        conversation_id,
      ]);
      const rows = tx.all<{ held_prompt_id: string; dispatching: number }>(
        'SELECT held_prompt_id,dispatching FROM conversation_held_prompts WHERE conversation_id=? ORDER BY dispatching DESC,position',
        [conversation_id],
      );
      const at = position + rows.filter((row) => row.dispatching).length;
      rows.forEach((row, index) =>
        tx.run('UPDATE conversation_held_prompts SET position=? WHERE held_prompt_id=?', [
          index < at ? index : index + 1,
          row.held_prompt_id,
        ]),
      );
      tx.run(
        'INSERT INTO conversation_held_prompts(held_prompt_id,conversation_id,position,payload) VALUES(?,?,?,?)',
        [message.held_prompt_id, conversation_id, at, payload],
      );
    },
    { changeSignal: 'silent' },
  );
}

export function read_held_prompts(database: Database, conversation_id: string): readonly Message[] {
  return database.read((sql) =>
    sql
      .all<{ payload: string; dispatching: number }>(
        'SELECT payload,dispatching FROM conversation_held_prompts WHERE conversation_id=? ORDER BY dispatching DESC,position',
        [conversation_id],
      )
      .map((row) => {
        const stored = JSON.parse(row.payload);
        const canonical = conversation_event_payload_from_canonical_json(
          'prompt_delivery_uncertain',
          stored.canonical,
        );
        return {
          ...stored,
          content: message_content_from_stored(stored),
          recovery_references:
            'recovery_references' in canonical ? canonical.recovery_references : [],
          recording_receipt: null,
          delivery_uncertain: stored.delivery_uncertain || Boolean(row.dispatching),
        } as Message;
      }),
  );
}

export function settle_held_prompt(
  tx: TransactionSql,
  conversation_id: string,
  payload: { kind: string; sender_message_id?: string | null },
): void {
  if (
    !['prompt', 'prompt_delivery_refused', 'prompt_discarded'].includes(payload.kind) ||
    payload.sender_message_id == null
  )
    return;
  tx.run(
    "DELETE FROM conversation_held_prompts WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=?",
    [conversation_id, payload.sender_message_id],
  );
}

type Database = Pick<FullDatabase, 'read' | 'write'>;
