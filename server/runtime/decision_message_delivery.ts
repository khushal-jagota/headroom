/** Deliver immutable Ticket decisions through the ordinary conversation door. */
import type { Database } from '../core/database/contracts.ts';
import {
  require_conversation_backend_key,
  PromptDeliveryStarted,
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  type PromptDeliveryFate,
  type ConversationSystem,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { now_ms } from '../executor/clock.ts';
import type { WorkerTypeRegistry } from '../worker_types/registry.ts';
import * as tickets from '../tickets/data/index.ts';
import * as decisions from '../tickets/decision_messages.ts';
import * as locale from '../tickets/locale_configuration.ts';
import { model_is_enabled } from '../conversation/backend_state.ts';
import * as start from './conversation_start.ts';
import { ConversationStartAborted, observe_conversation_operation } from './abort.ts';

export async function deliver_ticket_decision_messages(
  ticket_id: string,
  options: {
    connect_database: () => Database;
    conversation_system: ConversationSystem;
    worker_type_registry: WorkerTypeRegistry;
    now: () => number;
    signal?: AbortSignal;
  },
): Promise<PromptDeliveryFate | null> {
  let last_fate: PromptDeliveryFate | null = null;
  const database = options.connect_database();
  return start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    let ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
    const admitted = database.read(
      (sql) =>
        !ticket.is_backlogged &&
        locale.can_execute_locale(sql, ticket.locale) &&
        (ticket.locale !== 'macos' || locale.mac_is_present(sql, now_ms())) &&
        (ticket.employee_launch_model === null ||
          model_is_enabled(
            sql,
            require_conversation_backend_key(ticket.employee_backend),
            ticket.employee_launch_model,
          )),
    );
    if (!admitted) return null;
    const rows = database.read((sql) =>
      sql.all<{
        decision_id: string;
        sender_message_id: string;
        conversation_id: string | null;
        delivery_status: string;
        model_text: string;
      }>(
        "SELECT * FROM ticket_decision_messages WHERE ticket_id=? AND delivery_status != 'delivered' ORDER BY created_at,rowid",
        [ticket_id],
      ),
    );
    const set_status = (identity: string, status: string, conversation_id: string | null) =>
      database.write((sql) => {
        sql.run(
          'UPDATE ticket_decision_messages SET delivery_status=?,conversation_id=? WHERE decision_id=?',
          [status, conversation_id, identity],
        );
      });
    const mark_error = (status: string) =>
      database.write((sql) =>
        tickets.mark_worker_step_claim_errored(sql, ticket_id, {
          expected_claim: ticket.worker_step_claim,
          expected_claim_revision: ticket.worker_step_claim_revision,
          now: options.now(),
          error_reason: `decision delivery ${status}`,
        }),
      );
    for (const row of rows) {
      const identity = row.decision_id,
        target = row.conversation_id;
      let status = database.read((sql) =>
        decisions.recorded_outcome(sql, target, row.sender_message_id),
      );
      if (status !== null) {
        if (status === row.delivery_status && status !== 'delivered') break;
        set_status(identity, status, target);
        if (status !== 'delivered') {
          mark_error(status);
          break;
        }
        continue;
      }
      if (['uncertain', 'refused', 'discarded'].includes(row.delivery_status)) break;
      if (row.delivery_status !== 'pending') {
        if (target !== null) {
          const held = await observe_conversation_operation(
            () => options.conversation_system.held_prompts(target),
            options.signal,
          );
          if (held.some((item) => item.sender_message_id === row.sender_message_id)) continue;
        }
        set_status(identity, 'uncertain', target);
        mark_error('uncertain');
        console.error(`decision delivery receipt is uncertain (ticket=${ticket_id})`);
        break;
      }
      try {
        ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
        const delivered = await start.send_to_ticket_conversation(
          options.conversation_system,
          database,
          ticket_id,
          async (sending_into: string) => {
            set_status(identity, 'sending', sending_into);
            return text_message_content(row.model_text);
          },
          {
            conversation_id: ticket.conversation_id,
            created_conversation_id: start.new_conversation_id(),
            sender_label: 'system',
            sender_message_id: row.sender_message_id,
            worker_type_registry: options.worker_type_registry,
            retain_backend_start_failure: true,
            now: options.now(),
            signal: options.signal,
          },
        );
        last_fate = delivered.fate;
        status =
          delivered.fate instanceof PromptDeliveryStarted ||
          delivered.fate instanceof PromptDeliveryInjected
            ? 'delivered'
            : delivered.fate instanceof PromptDeliveryQueued
              ? 'queued'
              : delivered.fate instanceof PromptDeliveryRefused
                ? 'refused'
                : delivered.fate instanceof PromptDeliverySuppressed
                  ? 'discarded'
                  : 'uncertain';
        set_status(identity, status, delivered.conversation_id);
        if (!['delivered', 'queued'].includes(status)) {
          mark_error(status);
          break;
        }
      } catch (error) {
        // Cancellation preserves the durable sending marker for reconciliation.
        if (error instanceof ConversationStartAborted) throw error;
        console.error(`decision message could not be sent (ticket=${ticket_id})`, error);
        const attempt = database.read((sql) =>
          sql.get<{ delivery_status: string; conversation_id: string | null }>(
            'SELECT delivery_status,conversation_id FROM ticket_decision_messages WHERE decision_id=?',
            [identity],
          ),
        );
        if (attempt?.delivery_status === 'sending') {
          set_status(identity, 'uncertain', attempt.conversation_id);
          mark_error('uncertain');
        }
        break;
      }
    }
    return last_fate;
  }, options.signal);
}
