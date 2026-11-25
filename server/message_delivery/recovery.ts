import { own_recipient_delivery } from './lifetime.ts';
/** Startup owns one reconciliation of each durable recipient obligation. */
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { RecipientDeliveryStale } from './contracts.ts';
import {
  decode_recipient_message,
  require_recipient_target,
  finish_recipient_delivery,
} from './data/recipient_deliveries.ts';
import { prompt_row, refused_row, uncertain_row } from '../conversation/runtime/messages.ts';
import { PromptQueueReason, PromptDeliveryRefusalReason } from '../conversation/contracts.ts';

export function resume_recipient_deliveries(host: SqliteProcessConversationSystem): void {
  const database = host.options.store.start_admission_database;
  const rows = database.read((sql) =>
    sql.all<{
      submission_id: string;
      attempt_id: string;
      native_transport: 'mailbox' | 'local' | null;
      conversation_id: string;
      payload: string;
      state: string;
    }>(
      "SELECT submission_id,attempt_id,native_transport,conversation_id,payload,state FROM recipient_deliveries WHERE state IN ('pending','dispatching') ORDER BY delivery_order",
    ),
  );
  for (const row of rows) {
    const message = decode_recipient_message(row.payload);
    if (
      host.options.store.sender_message_outcome(row.conversation_id, message.sender_message_id!) !==
      null
    )
      continue;
    if (row.state === 'dispatching') {
      const evidence = database.read((sql) =>
        sql.all<{ outcome_json: string | null; state: string; operation: string }>(
          "SELECT c.outcome_json,c.state,c.operation FROM executor_commands c JOIN recipient_delivery_commands d ON d.command_id=c.command_id WHERE d.submission_id=? AND c.operation IN ('write_prompt','steer')",
          [row.submission_id],
        ),
      );
      if (row.native_transport === 'mailbox' && evidence.length === 0) {
        // A machine cannot receive input without its durable command row.
        database.write((tx) =>
          tx.run(
            "UPDATE recipient_deliveries SET state='pending' WHERE submission_id=? AND attempt_id=?",
            [row.submission_id, row.attempt_id],
          ),
        );
      } else {
        // The retained native admission owns this exact command until its result arrives.
        if (evidence.some((row) => row.state === 'waiting' || row.state === 'delivered')) continue;
        const outcomes = evidence.flatMap((row) =>
          row.outcome_json === null ? [] : [JSON.parse(row.outcome_json)],
        );
        const accepted = outcomes.some(
          (outcome) =>
            outcome.child_outcome?.disposition === 'succeeded' &&
            ['prompt_accepted', 'steer_accepted'].includes(outcome.child_outcome?.result?.kind),
        );
        const steer_refused =
          outcomes.length > 0 &&
          evidence.every((row) => row.operation === 'steer') &&
          outcomes.every(
            (outcome) =>
              outcome.child_outcome?.disposition === 'refused' ||
              (outcome.child_outcome?.disposition === 'succeeded' &&
                outcome.child_outcome?.result?.kind === 'steer_refused'),
          );
        if (steer_refused) {
          const task = own_recipient_delivery(host, async () => {
            const state = host.conversation_state(row.conversation_id);
            if (!state) return;
            try {
              database.read((sql) => require_recipient_target(sql, row.submission_id));
            } catch (error) {
              if (!(error instanceof RecipientDeliveryStale)) throw error;
              const written = host.options.store.append_events(row.conversation_id, [
                refused_row(
                  message,
                  message.requested_mode ?? 'queue',
                  PromptDeliveryRefusalReason.no_such_conversation,
                  error.message,
                ),
              ]);
              host.note_written(state, written);
              return;
            }
            await state.lock.run(() => {
              const held = host.hold_prompt(state, {
                ...message,
                queue_reason: PromptQueueReason.steer_refused,
              });
              finish_recipient_delivery(database, row, held);
            });
            host.schedule_drain(state);
          });
          void task.catch((error) => console.error('refused steer recovery failed', error));
          continue;
        }
        const refused =
          outcomes.length > 0 &&
          outcomes.every((outcome) => outcome.child_outcome?.disposition === 'refused');
        host.options.store.append_events(row.conversation_id, [
          accepted
            ? prompt_row(message, message.requested_mode ?? 'queue')
            : refused
              ? refused_row(
                  message,
                  message.requested_mode ?? 'queue',
                  PromptDeliveryRefusalReason.write_to_backend_failed,
                )
              : uncertain_row(message, message.requested_mode ?? 'queue', 'delivery uncertain'),
        ]);
        continue;
      }
    }
    try {
      database.read((sql) => require_recipient_target(sql, row.submission_id));
    } catch (error) {
      if (!(error instanceof RecipientDeliveryStale)) throw error;
      const payload = refused_row(
        message,
        message.requested_mode ?? 'queue',
        PromptDeliveryRefusalReason.no_such_conversation,
        error.message,
      );
      if (host.options.store.read_conversation(row.conversation_id))
        host.options.store.append_events(row.conversation_id, [payload]);
      else
        database.write((tx) =>
          tx.run(
            "UPDATE recipient_deliveries SET state='refused',outcome=? WHERE submission_id=? AND attempt_id=?",
            [
              JSON.stringify({ fate: 'refused', refusal_reason: 'no_such_conversation' }),
              row.submission_id,
              row.attempt_id,
            ],
          ),
        );
      continue;
    }
    const task = own_recipient_delivery(host, () =>
      host.workspace_admission.operation(
        async (admitted) => {
          if (!admitted) return;
          const fate = await host.send(row.conversation_id, message.content, {
            ...message,
            mode: message.requested_mode,
            record_refusal: true,
          });
          finish_recipient_delivery(database, row, fate);
        },
        { starts: true },
      ),
    );
    void task.catch((error) => console.error('recipient delivery recovery failed', error));
  }
  const conversations = database.read((sql) =>
    sql.all<{ conversation_id: string }>(
      'SELECT DISTINCT conversation_id FROM conversation_held_prompts',
    ),
  );
  for (const row of conversations) {
    const state = host.conversation_state(row.conversation_id);
    if (state) host.schedule_drain(state);
  }
}
