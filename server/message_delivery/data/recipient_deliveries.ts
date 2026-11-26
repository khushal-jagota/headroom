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
    prune_finished_recipient_deliveries(tx);
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
  native_transport: 'local' | 'mailbox',
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
      const attempt_id = randomUUID();
      tx.run(
        "UPDATE recipient_deliveries SET state='dispatching',native_transport=?,attempt_id=? WHERE submission_id=? AND attempt_id=?",
        [native_transport, attempt_id, submission_id, row.attempt_id],
      );
      // Remote start acknowledges mailbox placement before the machine fetches it.
      // That preparation belongs to this input attempt; an older re-held attempt does not.
      tx.run(
        "UPDATE recipient_delivery_commands SET attempt_id=? WHERE submission_id=? AND attempt_id=? AND EXISTS (SELECT 1 FROM executor_commands c WHERE c.command_id=recipient_delivery_commands.command_id AND c.operation='start')",
        [attempt_id, submission_id, row.attempt_id],
      );
      if (message.held_prompt_id)
        tx.run(
          'UPDATE conversation_held_prompts SET dispatching=1,dispatch_turn=? WHERE held_prompt_id=?',
          [turn_number, message.held_prompt_id],
        );
      return [{ submission_id, attempt_id }];
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
      "UPDATE recipient_deliveries SET state=?,outcome=?,settled_at=? WHERE submission_id=? AND attempt_id=? AND state NOT IN ('delivered','refused','discarded') AND (?!='held' OR state!='dispatching')",
      [
        state,
        JSON.stringify(outcome),
        ['delivered', 'refused', 'discarded'].includes(state) ? Date.now() : null,
        attempt.submission_id,
        attempt.attempt_id,
        state,
      ],
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
    'UPDATE recipient_deliveries SET state=?,outcome=?,settled_at=? WHERE submission_id=? AND conversation_id=?',
    [
      state,
      JSON.stringify(delivery_fate_json(outcome_fate(payload))),
      ['delivered', 'refused', 'discarded'].includes(state) ? Date.now() : null,
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

/** A queued native command must still belong to its recorded target at machine fetch. */
export function recipient_command_is_current(sql: ReadSql, command_id: string): boolean {
  const links = sql.all<{
    submission_id: string;
    attempt_id: string;
    expected_attempt: string;
    state: string;
    operation: string;
  }>(
    'SELECT c.submission_id,c.attempt_id,r.attempt_id AS expected_attempt,r.state,e.operation FROM recipient_delivery_commands c JOIN recipient_deliveries r USING(submission_id) JOIN executor_commands e ON e.command_id=c.command_id WHERE c.command_id=?',
    [command_id],
  );
  const shared_start = links[0]?.operation === 'start';
  const required = shared_start ? links.filter((link) => link.state !== 'discarded') : links;
  for (const link of required) {
    if (
      link.attempt_id !== link.expected_attempt ||
      (link.operation === 'start'
        ? !['pending', 'held', 'dispatching'].includes(link.state)
        : link.state !== 'dispatching')
    )
      return false;
    try {
      require_recipient_target(sql, link.submission_id);
    } catch (error) {
      if (error instanceof RecipientDeliveryStale) return false;
      throw error;
    }
  }
  // A discarded sibling does not cancel preparation needed by a surviving input.
  return !shared_start || required.length > 0 || links.length === 0;
}

/** Receipts and conversation events retain terminal outcomes independently of delivery payloads. */
export function prune_finished_recipient_deliveries(tx: TransactionSql): void {
  tx.run(
    "DELETE FROM recipient_deliveries WHERE state IN ('delivered','refused','discarded') AND settled_at<? AND NOT EXISTS (SELECT 1 FROM recipient_delivery_commands d JOIN executor_commands c ON c.command_id=d.command_id WHERE d.submission_id=recipient_deliveries.submission_id AND c.state IN ('waiting','delivered'))",
    [Date.now() - 7 * 24 * 60 * 60 * 1000],
  );
}

/** Re-holding a proven unissued input closes its old native attempt atomically with custody. */
export function hold_recipient_delivery(
  tx: TransactionSql,
  conversation_id: string,
  sender_message_id: string | null,
): void {
  if (sender_message_id === null) return;
  tx.run(
    "UPDATE recipient_deliveries SET state='held',attempt_id=CASE WHEN state IN ('dispatching','uncertain') THEN ? ELSE attempt_id END,native_transport=NULL,settled_at=NULL WHERE submission_id=? AND state IN ('pending','held','dispatching','uncertain')",
    [randomUUID(), `${conversation_id}:${sender_message_id}`],
  );
}
