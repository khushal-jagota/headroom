/** Reconcile decision feedback already offered to a conversation. */
import type { Database } from '../core/database/contracts.ts';
import type { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { automatic_ticket_admitted } from './automatic_work.ts';
import {
  known_conversation_backend_key,
  type PromptDeliveryFate,
  type ConversationSystem,
} from '../conversation/contracts.ts';
import { now_ms } from '../executor/clock.ts';
import type { WorkerTypeRegistry } from '../worker_types/registry.ts';
import * as tickets from '../tickets/data/index.ts';
import * as decisions from '../tickets/decision_messages.ts';
import * as locale from '../tickets/locale_configuration.ts';
import { model_is_enabled } from '../conversation/backend_state.ts';
import * as start from './conversation_start.ts';
import { observe_conversation_operation } from './abort.ts';

export async function deliver_ticket_decision_messages(
  ticket_id: string,
  options: {
    connect_database: () => Database;
    conversation_system: ConversationSystem;
    worker_type_registry: WorkerTypeRegistry;
    now: () => number;
    signal?: AbortSignal;
    automatic_tickets_created_after?: number;
    upload_service?: VideoUploadService;
  },
): Promise<PromptDeliveryFate | null> {
  const database = options.connect_database();
  if (
    !database.read((sql) =>
      automatic_ticket_admitted(sql, ticket_id, options.automatic_tickets_created_after),
    )
  )
    return null;
  return start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    const ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
    // A backend this build lacks is not admitted here. The readiness check then names it.
    const backend = known_conversation_backend_key(ticket.employee_backend);
    const admitted = database.read(
      (sql) =>
        !ticket.is_backlogged &&
        locale.can_execute_machine(ticket.machine_id, sql) &&
        locale.mac_is_present(sql, now_ms(), ticket.machine_id) &&
        (ticket.employee_launch_model === null ||
          (backend !== undefined && model_is_enabled(sql, backend, ticket.employee_launch_model))),
    );
    if (!admitted) return null;
    const rows = database.read((sql) =>
      sql.all<{
        decision_id: string;
        proposal_revision: number | null;
        sender_message_id: string;
        conversation_id: string | null;
        delivery_status: string;
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
          if (awaiting.has(decisions.receipt_sender_message_id(row.sender_message_id))) continue;
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
      // Pending decisions travel with the next step, never in a separate turn.
      break;
    }
    return null;
  }, options.signal);
}
