import type { Database, ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { PromptDeliveryRefusalReason } from '../contracts.ts';
import type { PromptDeliveryMode } from '../contracts.ts';
import type { ConversationRecordStore } from './contracts.ts';
import type { Message } from '../runtime/contracts.ts';
import { refused_row, uncertain_row } from '../runtime/messages.ts';
import {
  conversation_event_payload_from_canonical_json,
  conversation_event_payload_to_canonical_json,
} from './codec.ts';

type PendingKind = 'prompt_delivery_refused' | 'prompt_delivery_uncertain';

export function owner_send_epoch(sql: ReadSql): string | null {
  return (
    sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1')
      ?.epoch ?? null
  );
}

export function note_pending_owner_sends(
  database: Pick<Database, 'write'>,
  conversation_id: string,
  messages: readonly Message[],
  mode: PromptDeliveryMode,
  dispatching: boolean,
): void {
  const owner_messages = messages.flatMap((message) => {
    const { sender, recipient, sender_message_id } = message;
    return sender?.kind === 'owner' && recipient && sender_message_id
      ? [{ message, recipient, sender_message_id }]
      : [];
  });
  if (owner_messages.length === 0) return;
  const kind: PendingKind = dispatching ? 'prompt_delivery_uncertain' : 'prompt_delivery_refused';
  database.write(
    (tx) => {
      for (const { message, recipient, sender_message_id } of owner_messages) {
        const payload = dispatching
          ? uncertain_row(
              message,
              mode,
              'The agent did not confirm receipt before Panels restarted.',
            )
          : refused_row(
              message,
              mode,
              PromptDeliveryRefusalReason.write_to_backend_failed,
              'Panels restarted before this queued message reached the agent.',
            );
        tx.run(
          'UPDATE owner_send_receipts SET pending_kind=?,pending_payload=? ' +
            'WHERE owner_kind=? AND owner_id=? AND message_id=? AND conversation_id=?',
          [
            kind,
            conversation_event_payload_to_canonical_json(payload),
            recipient.kind,
            recipient.id,
            sender_message_id,
            conversation_id,
          ],
        );
      }
    },
    { changeSignal: 'silent' },
  );
}

export function clear_owner_send_payload(
  tx: TransactionSql,
  conversation_id: string,
  payload: Parameters<ConversationRecordStore['append_events']>[1][number],
): void {
  if (
    payload.kind !== 'prompt' &&
    payload.kind !== 'prompt_delivery_refused' &&
    payload.kind !== 'prompt_delivery_uncertain' &&
    payload.kind !== 'prompt_discarded'
  )
    return;
  if (payload.sender?.kind !== 'owner' || !payload.recipient || !payload.sender_message_id) return;
  tx.run(
    'UPDATE owner_send_receipts SET pending_kind=NULL,pending_payload=NULL ' +
      'WHERE owner_kind=? AND owner_id=? AND message_id=? AND conversation_id=?',
    [payload.recipient.kind, payload.recipient.id, payload.sender_message_id, conversation_id],
  );
}

/** Recover the outbound message as an ordinary conversation row, without dispatching it. */
export function recover_owner_sends(database: Database, store: ConversationRecordStore): void {
  const pending = database.read((sql) =>
    sql.all<{
      owner_kind: Principal['kind'];
      owner_id: string;
      message_id: string;
      conversation_id: string;
      pending_kind: PendingKind;
      pending_payload: string;
    }>(
      'SELECT owner_kind,owner_id,message_id,conversation_id,pending_kind,pending_payload ' +
        'FROM owner_send_receipts WHERE pending_kind IS NOT NULL AND epoch IS NOT ?',
      [owner_send_epoch(sql)],
    ),
  );
  for (const row of pending) {
    if (
      store.read_conversation(row.conversation_id) &&
      !store.sender_message_outcome(row.conversation_id, row.message_id)
    ) {
      store.append_events(row.conversation_id, [
        conversation_event_payload_from_canonical_json(row.pending_kind, row.pending_payload),
      ]);
    }
    database.write(
      (tx) => {
        tx.run(
          'UPDATE owner_send_receipts SET pending_kind=NULL,pending_payload=NULL ' +
            'WHERE owner_kind=? AND owner_id=? AND message_id=?',
          [row.owner_kind, row.owner_id, row.message_id],
        );
      },
      { changeSignal: 'silent' },
    );
  }
}
