import { conversation_link_lock } from '../runtime/conversation_start.ts';
import { release_admission_leases } from '../runtime/admission_locks.ts';
/** Recipient recovery uses durable command identity, never elapsed time or matching text. */
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { Message } from '../conversation/runtime/contracts.ts';
import type { ConversationEventPayload } from '../conversation/events.ts';
import type { WireValue } from '../executor/contracts.ts';
import { decode_json } from '../executor/protocol.ts';
import { own_recipient_delivery } from './lifetime.ts';
import { RecipientDeliveryStale } from './contracts.ts';
import {
  decode_recipient_message,
  require_recipient_target,
  finish_recipient_delivery,
} from './data/recipient_deliveries.ts';
import { prompt_row, refused_row, uncertain_row } from '../conversation/runtime/messages.ts';
import { late_steer_owns_message } from '../conversation/runtime/controls.ts';
import {
  PromptQueueReason,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason as R,
} from '../conversation/contracts.ts';
import type { PromptDeliveryMode } from '../conversation/contracts.ts';

interface RecoveryRow {
  submission_id: string;
  attempt_id: string;
  recipient_kind: string;
  recipient_id: string;
  native_transport: 'mailbox' | 'local' | null;
  conversation_id: string;
  payload: string;
  state: string;
}
const columns =
  'submission_id,attempt_id,recipient_kind,recipient_id,native_transport,conversation_id,payload,state';

async function receipt(
  host: SqliteProcessConversationSystem,
  row: RecoveryRow,
  message: Message,
  payload: ConversationEventPayload,
): Promise<void> {
  const state = host.conversation_state(row.conversation_id);
  if (!state) {
    finish_recipient_delivery(
      host.options.store.start_admission_database,
      row,
      new PromptDeliveryRefused({ refusal_reason: R.no_such_conversation }),
    );
    return;
  }
  await state.lock.run(async () => {
    const previous = host.options.store.sender_message_outcome(
      row.conversation_id,
      message.sender_message_id!,
    );
    if (previous?.payload.kind === 'prompt' || previous?.payload.kind === payload.kind) return;
    if (
      payload.kind !== 'prompt' &&
      previous &&
      previous.payload.kind !== 'prompt_delivery_uncertain'
    )
      return;
    const written = await host.persist(state, () =>
      host.options.store.append_events(row.conversation_id, [payload], {
        owner_read_through_sequence:
          message.sender?.kind === 'owner' &&
          (payload.kind === 'prompt' || message.acknowledge_owner_on_failure)
            ? message.owner_read_through_sequence
            : null,
      }),
    );
    host.note_written(state, written);
  });
}

async function queue_refused_steer(
  host: SqliteProcessConversationSystem,
  row: RecoveryRow,
  message: Message,
): Promise<void> {
  const state = host.conversation_state(row.conversation_id);
  if (!state)
    return receipt(host, row, message, refused_row(message, 'steer', R.no_such_conversation));
  // The live late-result observer owns its own re-hold. A restarted runtime has no such owner.
  if (late_steer_owns_message(state, message.sender_message_id)) return;
  try {
    await state.lock.run(() => {
      host.options.store.start_admission_database.read((sql) =>
        require_recipient_target(sql, row.submission_id),
      );
      const already = state.held_prompts.findIndex(
        (held) => held.sender_message_id === message.sender_message_id,
      );
      const fate =
        already < 0
          ? host.hold_prompt(state, { ...message, queue_reason: PromptQueueReason.steer_refused })
          : new PromptDeliveryQueued({ queue_position: already + 1 });
      finish_recipient_delivery(host.options.store.start_admission_database, row, fate);
    });
    release_admission_leases();
    host.schedule_drain(state);
  } catch (error) {
    if (!(error instanceof RecipientDeliveryStale)) throw error;
    await receipt(
      host,
      row,
      message,
      refused_row(message, 'steer', R.no_such_conversation, error.message),
    );
  }
}

async function recover_row(host: SqliteProcessConversationSystem, row: RecoveryRow): Promise<void> {
  if (host.shutting_down) return;
  const database = host.options.store.start_admission_database;
  const message = decode_recipient_message(row.payload);
  const previous = host.options.store.sender_message_outcome(
    row.conversation_id,
    message.sender_message_id!,
  );
  if (previous && previous.payload.kind !== 'prompt_delivery_uncertain') return;
  let unattempted_mailbox = false;
  if (row.state === 'dispatching' || row.state === 'uncertain') {
    const evidence = database.read((sql) =>
      sql.all<{
        operation: string;
        state: string;
        command_json: string;
        outcome_json: string | null;
      }>(
        "SELECT c.operation,c.state,c.command_json,c.outcome_json FROM executor_commands c JOIN recipient_delivery_commands d ON d.command_id=c.command_id WHERE d.submission_id=? AND d.attempt_id=? AND c.operation IN ('write_prompt','steer') ORDER BY c.command_sequence",
        [row.submission_id, row.attempt_id],
      ),
    );
    if (
      row.state === 'dispatching' &&
      row.native_transport === 'mailbox' &&
      evidence.length === 0
    ) {
      unattempted_mailbox = true;
    } else {
      // Retained native admission or the exact command-result callback owns unfinished commands.
      if (evidence.some((item) => item.state === 'waiting' || item.state === 'delivered')) return;
      const outcomes = evidence.flatMap((item) =>
        item.outcome_json === null
          ? []
          : [{ command: item, result: JSON.parse(item.outcome_json).child_outcome }],
      );
      const accepted = outcomes.find(
        (item) =>
          item.result?.disposition === 'succeeded' &&
          ['prompt_accepted', 'steer_accepted'].includes(item.result.result?.kind),
      );
      if (accepted) {
        let mode: PromptDeliveryMode = 'steer';
        if (accepted.command.operation === 'write_prompt') {
          const command = decode_json(
            accepted.command.command_json,
            'MailboxCommand',
          ).child_command;
          if (command?.arguments.kind !== 'write_prompt')
            throw new Error('Recipient proof has no prompt command');
          mode = command.arguments.mode;
        }
        return receipt(host, row, message, prompt_row(message, mode));
      }
      const never_arrived =
        outcomes.length > 0 &&
        outcomes.every(
          (item) =>
            item.result?.disposition === 'refused' ||
            (item.result?.disposition === 'succeeded' &&
              item.result.result?.kind === 'steer_refused'),
        );
      if (never_arrived && evidence.every((item) => item.operation === 'steer'))
        return queue_refused_steer(host, row, message);
      return receipt(
        host,
        row,
        message,
        never_arrived
          ? refused_row(message, message.requested_mode ?? 'queue', R.write_to_backend_failed)
          : uncertain_row(message, message.requested_mode ?? 'queue', 'delivery uncertain'),
      );
    }
  }
  try {
    database.read((sql) => require_recipient_target(sql, row.submission_id));
    if (unattempted_mailbox) {
      const state = host.conversation_state(row.conversation_id);
      if (state)
        await state.lock.run(() => {
          database.write((tx) => {
            require_recipient_target(tx, row.submission_id);
            tx.run(
              "UPDATE recipient_deliveries SET state='pending' WHERE submission_id=? AND attempt_id=?",
              [row.submission_id, row.attempt_id],
            );
            tx.run(
              "UPDATE conversation_held_prompts SET dispatching=0,dispatch_turn=NULL WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=?",
              [row.conversation_id, message.sender_message_id],
            );
          });
          for (let index = 0; index < state.held_prompts.length; index++) {
            if (state.held_prompts[index].sender_message_id === message.sender_message_id)
              state.held_prompts[index] = {
                ...state.held_prompts[index],
                delivery_uncertain: false,
              };
          }
        });
    }
    const fate = await host.send(row.conversation_id, message.content, {
      ...message,
      mode: message.requested_mode,
      record_refusal: true,
    });
    finish_recipient_delivery(database, row, fate);
    if (unattempted_mailbox) {
      const state = host.conversation_state(row.conversation_id);
      if (state) host.schedule_drain(state);
    }
  } catch (error) {
    if (!(error instanceof RecipientDeliveryStale)) throw error;
    await receipt(
      host,
      row,
      message,
      refused_row(
        message,
        message.requested_mode ?? 'queue',
        R.no_such_conversation,
        error.message,
      ),
    );
  }
}

export function resume_recipient_deliveries(host: SqliteProcessConversationSystem): void {
  const database = host.options.store.start_admission_database;
  const rows = database.read((sql) =>
    sql.all<RecoveryRow>(
      `SELECT ${columns} FROM recipient_deliveries WHERE state IN ('pending','dispatching','uncertain') ORDER BY delivery_order`,
    ),
  );
  for (const row of rows) {
    const key =
      row.recipient_kind === 'ticket'
        ? `ticket:${row.recipient_id}`
        : row.recipient_kind === 'chief'
          ? 'agent:chief_of_staff'
          : row.recipient_kind === 'manager'
            ? `agent:manager_${row.recipient_id}`
            : `conversation:${row.conversation_id}`;
    void own_recipient_delivery(host, () =>
      conversation_link_lock(key).run(() => recover_row(host, row)),
    ).catch((error) => console.error('Recipient delivery recovery failed', error));
  }
  for (const row of database.read((sql) =>
    sql.all<{ conversation_id: string }>(
      'SELECT DISTINCT conversation_id FROM conversation_held_prompts',
    ),
  )) {
    const state = host.conversation_state(row.conversation_id);
    if (state) host.schedule_drain(state);
  }
}

export async function reconcile_recipient_message(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  sender_message_id: string | null,
): Promise<void> {
  if (host.shutting_down || sender_message_id === null) return;
  const row = host.options.store.start_admission_database.read((sql) =>
    sql.get<RecoveryRow>(
      `SELECT ${columns} FROM recipient_deliveries WHERE submission_id=? AND state IN ('dispatching','uncertain')`,
      [`${conversation_id}:${sender_message_id}`],
    ),
  );
  if (row) await own_recipient_delivery(host, () => recover_row(host, row));
}

export async function reconcile_recipient_native_result(
  host: SqliteProcessConversationSystem,
  command: WireValue<'ChildCommand'>,
): Promise<void> {
  if (host.shutting_down) return;
  const messages = host.options.store.start_admission_database.read((sql) =>
    sql.all<{ payload: string }>(
      'SELECT r.payload FROM recipient_deliveries r JOIN recipient_delivery_commands c ON c.submission_id=r.submission_id AND c.attempt_id=r.attempt_id WHERE c.command_id=?',
      [command.command_id],
    ),
  );
  for (const row of messages) {
    const message = decode_recipient_message(row.payload);
    const state = host.conversation_state(command.child.conversation_id);
    if (
      state &&
      (state.admitted_sender_messages.has(message.sender_message_id!) ||
        state.sender_messages_being_delivered.has(message.sender_message_id!))
    )
      continue;
    await reconcile_recipient_message(
      host,
      command.child.conversation_id,
      message.sender_message_id,
    );
  }
}
