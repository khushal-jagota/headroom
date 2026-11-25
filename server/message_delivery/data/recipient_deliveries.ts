import { recipient_lifecycle, require_recipient_available } from './lifecycles.ts';
import type { Principal } from '../../core/contracts.ts';
import { DeploymentRecoveryReference } from '../../conversation/contracts.ts';
import { outcome_fate } from '../../conversation/runtime/messages.ts';
import type { ConversationEventPayload } from '../../conversation/events.ts';
import { RecipientDeliveryStale } from '../contracts.ts';
/** A normalized recipient prompt and its exact native attempts. */
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import type {
  Database as FullDatabase,
  ReadSql,
  TransactionSql,
} from '../../core/database/contracts.ts';
import type { Message } from '../../conversation/runtime/contracts.ts';
import {
  message_content_json_entries,
  message_content_from_stored,
} from '../../conversation/message_content.ts';
import type { PromptDeliveryFate } from '../../conversation/contracts.ts';
import { delivery_fate_json } from '../../conversation/http.ts';

import type { RecipientAttempt } from '../contracts.ts';
const native_attempts = new AsyncLocalStorage<readonly RecipientAttempt[]>();
export function with_recipient_attempts<T>(
  attempts: readonly RecipientAttempt[],
  operation: () => Promise<T>,
): Promise<T> {
  return native_attempts.run(attempts, operation);
}
export function link_recipient_command(
  tx: TransactionSql,
  command_id: string,
  operation: string,
): void {
  if (!['start', 'write_prompt', 'steer'].includes(operation)) return;
  for (const attempt of native_attempts.getStore() ?? []) {
    const current = tx.get<{ attempt_id: string; state: string }>(
      'SELECT attempt_id,state FROM recipient_deliveries WHERE submission_id=?',
      [attempt.submission_id],
    );
    if (
      current?.attempt_id !== attempt.attempt_id ||
      !['pending', 'held', 'dispatching'].includes(current.state)
    )
      throw new RecipientDeliveryStale('Recipient attempt changed before dispatch');
    require_recipient_target(tx, attempt.submission_id);
    tx.run(
      'INSERT OR IGNORE INTO recipient_delivery_commands(command_id,submission_id,attempt_id) SELECT ?,submission_id,attempt_id FROM recipient_deliveries WHERE submission_id=? AND attempt_id=?',
      [command_id, attempt.submission_id, attempt.attempt_id],
    );
  }
}
export function admit_recipient_delivery(
  database: Database,
  conversation_id: string,
  message: Message,
): RecipientAttempt {
  const submission_id =
    message.sender_message_id === null
      ? randomUUID()
      : `${conversation_id}:${message.sender_message_id}`;
  const { recording_receipt: _receipt, content: _content, ...values } = message;
  const result = database.write((tx) => {
    const previous = tx.get<RecipientAttempt>(
      'SELECT submission_id,attempt_id FROM recipient_deliveries WHERE submission_id=?',
      [submission_id],
    );
    if (previous) return previous;
    const ticket = tx.get<{ id: string }>('SELECT id FROM tickets WHERE conversation_id=?', [
      conversation_id,
    ]);
    const agent = tx.get<{ agent_key: string }>(
      'SELECT agent_key FROM agents WHERE conversation_id=? ORDER BY agent_key LIMIT 1',
      [conversation_id],
    );
    const recipient =
      message.recipient ??
      (ticket
        ? { kind: 'ticket', id: ticket.id }
        : agent?.agent_key === 'chief_of_staff'
          ? { kind: 'chief', id: 'chief' }
          : agent?.agent_key.startsWith('manager_')
            ? { kind: 'manager', id: agent.agent_key.slice('manager_'.length) }
            : { kind: 'conversation', id: conversation_id });
    if (recipient.kind !== 'conversation') require_recipient_available(tx, recipient as Principal);
    const lifecycle_generation = recipient_lifecycle(tx, recipient as Principal).generation;
    const attempt_id = randomUUID();
    tx.run(
      "INSERT INTO recipient_deliveries(submission_id,recipient_kind,recipient_id,conversation_id,generation,payload,state,attempt_id) VALUES(?,?,?,?,?,?,'pending',?)",
      [
        submission_id,
        recipient.kind,
        recipient.id,
        conversation_id,
        JSON.stringify({
          lifecycle_generation,
          conversation_id,
          machine_id: tx.get<{ machine_id: string }>(
            'SELECT machine_id FROM conversations WHERE conversation_id=?',
            [conversation_id],
          )!.machine_id,
        }),
        JSON.stringify({ ...values, ...message_content_json_entries(message.content) }),
        attempt_id,
      ],
    );
    message.recording_receipt?.in_admission?.(tx, conversation_id);
    return { submission_id, attempt_id };
  });
  message.recording_receipt?.admitted?.();
  return result;
}
export function mark_recipient_attempt(
  database: Database,
  conversation_id: string,
  messages: readonly Message[],
  turn_number: number,
): readonly RecipientAttempt[] {
  return database.write((tx) =>
    messages.flatMap((message) => {
      if (message.sender_message_id === null) return [];
      const submission_id = `${conversation_id}:${message.sender_message_id}`;
      const row = tx.get<RecipientAttempt>(
        'SELECT submission_id,attempt_id FROM recipient_deliveries WHERE submission_id=?',
        [submission_id],
      );
      if (!row) return [];
      require_recipient_target(tx, submission_id);
      tx.run(
        "UPDATE recipient_deliveries SET state='dispatching' WHERE submission_id=? AND attempt_id=?",
        [submission_id, row.attempt_id],
      );
      if (message.held_prompt_id)
        tx.run(
          'UPDATE conversation_held_prompts SET dispatching=1,dispatch_turn=? WHERE held_prompt_id=?',
          [turn_number, message.held_prompt_id],
        );
      return [row];
    }),
  );
}
export function finish_recipient_delivery(
  database: Database,
  attempt: RecipientAttempt,
  fate: PromptDeliveryFate,
): void {
  const outcome = delivery_fate_json(fate);
  const state =
    outcome.fate === 'queued'
      ? 'held'
      : outcome.fate === 'uncertain'
        ? 'uncertain'
        : outcome.fate === 'refused'
          ? 'refused'
          : outcome.fate === 'suppressed'
            ? 'discarded'
            : 'delivered';
  database.write((tx) =>
    tx.run(
      "UPDATE recipient_deliveries SET state=?,outcome=? WHERE submission_id=? AND attempt_id=? AND state NOT IN ('delivered','refused','discarded')",
      [state, JSON.stringify(outcome), attempt.submission_id, attempt.attempt_id],
    ),
  );
}
export function decode_recipient_message(payload: string): Message {
  const stored = JSON.parse(payload);
  return {
    ...stored,
    content: message_content_from_stored(stored),
    recovery_references: stored.recovery_references.map(
      (values: ConstructorParameters<typeof DeploymentRecoveryReference>[0]) =>
        new DeploymentRecoveryReference(values),
    ),
    recording_receipt: null,
  };
}

/** Terminal conversation evidence settles the same delivery, including late native results. */
export function settle_recipient_event(
  tx: TransactionSql,
  conversation_id: string,
  payload: ConversationEventPayload,
): void {
  if (!('sender_message_id' in payload) || !payload.sender_message_id) return;
  const state =
    payload.kind === 'prompt'
      ? 'delivered'
      : payload.kind === 'prompt_delivery_refused'
        ? 'refused'
        : payload.kind === 'prompt_delivery_uncertain'
          ? 'uncertain'
          : payload.kind === 'prompt_discarded'
            ? 'discarded'
            : null;
  if (state === null) return;
  tx.run(
    "UPDATE agent_message_acceptances SET pending=0,record_json=json_set(record_json,'$.pending',json('false')) WHERE submission_id=?",
    [payload.sender_message_id],
  );
  tx.run(
    'UPDATE recipient_deliveries SET state=?,outcome=? WHERE submission_id=? AND conversation_id=?',
    [
      state,
      JSON.stringify(delivery_fate_json(outcome_fate(payload))),
      `${conversation_id}:${payload.sender_message_id}`,
      conversation_id,
    ],
  );
}

export function recipient_attempts_for(
  database: Database,
  conversation_id: string,
  messages: readonly Message[],
): readonly RecipientAttempt[] {
  return database.read((sql) =>
    messages.flatMap((message) => {
      const row = sql.get<RecipientAttempt>(
        'SELECT submission_id,attempt_id FROM recipient_deliveries WHERE submission_id=?',
        [`${conversation_id}:${message.sender_message_id}`],
      );
      return row ? [row] : [];
    }),
  );
}

type Database = Pick<FullDatabase, 'read' | 'write'>;

export function require_recipient_target(tx: ReadSql, submission_id: string): void {
  const row = tx.get<{
    recipient_kind: string;
    recipient_id: string;
    conversation_id: string;
    generation: string;
  }>(
    'SELECT recipient_kind,recipient_id,conversation_id,generation FROM recipient_deliveries WHERE submission_id=?',
    [submission_id],
  );
  if (!row) return;
  const lifecycle = recipient_lifecycle(tx, {
    kind: row.recipient_kind,
    id: row.recipient_id,
  } as Principal);
  if (lifecycle.retiring)
    throw new RecipientDeliveryStale('Recipient lifecycle changed before dispatch');
  const machine = tx.get<{ machine_id: string }>(
    'SELECT machine_id FROM conversations WHERE conversation_id=?',
    [row.conversation_id],
  );
  if (
    JSON.stringify({
      lifecycle_generation: lifecycle.generation,
      conversation_id: row.conversation_id,
      machine_id: machine?.machine_id,
    }) !== row.generation
  )
    throw new RecipientDeliveryStale('Recipient generation changed before dispatch');
  if (row.recipient_kind === 'conversation') return;
  const current =
    row.recipient_kind === 'ticket'
      ? tx.get<{ conversation_id: string | null }>(
          'SELECT conversation_id FROM tickets WHERE id=?',
          [row.recipient_id],
        )
      : tx.get<{ conversation_id: string | null }>(
          'SELECT conversation_id FROM agents WHERE agent_key=?',
          [row.recipient_kind === 'chief' ? 'chief_of_staff' : `manager_${row.recipient_id}`],
        );
  if (current?.conversation_id !== row.conversation_id)
    throw new RecipientDeliveryStale('Recipient conversation changed before dispatch');
}

export function recipient_messages_for_command(
  database: Database,
  command_id: string,
): readonly Message[] {
  return database.read((sql) =>
    sql
      .all<{ payload: string }>(
        'SELECT r.payload FROM recipient_deliveries r JOIN recipient_delivery_commands c ON c.submission_id=r.submission_id AND c.attempt_id=r.attempt_id WHERE c.command_id=? ORDER BY r.delivery_order',
        [command_id],
      )
      .map((row) => decode_recipient_message(row.payload)),
  );
}
