/** Deliver immutable Ticket decisions through the ordinary conversation door. */
import type { Database } from '../core/database/contracts.ts';
import { automatic_ticket_admitted } from './automatic_work.ts';
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
import { HOME_MACHINE_ID } from '../execution_identity.ts';
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
    automatic_tickets_created_after?: number;
  },
): Promise<PromptDeliveryFate | null> {
  let last_fate: PromptDeliveryFate | null = null;
  const database = options.connect_database();
  if (
    !database.read((sql) =>
      automatic_ticket_admitted(sql, ticket_id, options.automatic_tickets_created_after),
    )
  )
    return null;
  return start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    let ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
    const admitted = database.read(
      (sql) =>
        !ticket.is_backlogged &&
        locale.can_execute_machine(ticket.machine_id, sql) &&
        (ticket.machine_id === HOME_MACHINE_ID ||
          locale.mac_is_present(sql, now_ms(), ticket.machine_id)) &&
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
        proposal_revision: number | null;
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
    const mark_error = (status: string, proposal_revision: number | null) =>
      database.write((sql) =>
        tickets.mark_worker_step_claim_errored(sql, ticket_id, {
          expected_claim: ticket.worker_step_claim,
          expected_claim_revision: ticket.worker_step_claim_revision,
          now: options.now(),
          error_reason: `decision delivery ${status}`,
          decision_delivery: true,
          decision_proposal_revision: proposal_revision,
        }),
      );
    for (const row of rows) {
      const identity = row.decision_id,
        target = row.conversation_id;
      let status = database.read((sql) =>
        decisions.recorded_outcome(sql, target, row.sender_message_id),
      );
      if (status === null && ['sending', 'queued'].includes(row.delivery_status)) {
        if (target !== null) {
          const awaiting = await observe_conversation_operation(
            () => options.conversation_system.sender_message_ids_awaiting_outcome(target),
            options.signal,
          );
          if (awaiting.has(row.sender_message_id)) continue;
        }
        status = database.read((sql) =>
          decisions.recorded_outcome(sql, target, row.sender_message_id),
        );
      }
      if (status !== null) {
        if (status === row.delivery_status && status !== 'delivered') break;
        set_status(identity, status, target);
        if (status !== 'delivered') {
          mark_error(status, row.proposal_revision);
          break;
        }
        continue;
      }
      if (['uncertain', 'refused', 'discarded'].includes(row.delivery_status)) break;
      if (row.delivery_status !== 'pending') {
        set_status(identity, 'uncertain', target);
        mark_error('uncertain', row.proposal_revision);
        console.error(`decision delivery receipt is uncertain (ticket=${ticket_id})`);
        break;
      }
      let delivered: Awaited<ReturnType<typeof start.send_to_ticket_conversation>>;
      try {
        ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
        delivered = await start.send_to_ticket_conversation(
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
            retain_definite_start_refusal: true,
            now: options.now(),
            signal: options.signal,
          },
        );
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
          mark_error('uncertain', row.proposal_revision);
        }
        break;
      }
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
        mark_error(status, row.proposal_revision);
        break;
      }
    }
    return last_fate;
  }, options.signal);
}
