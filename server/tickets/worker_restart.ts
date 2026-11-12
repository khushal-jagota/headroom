/** Kill a Ticket's conversation, unlink it, and start a fresh worker step. */
import { PlannerError, type Principal } from '../core/contracts.ts';
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { now_ms } from '../executor/clock.ts';
import type { WorkerStepStartResult } from '../runtime/worker_step_readiness_loop.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import * as worker_step_readiness from '../runtime/worker_step_readiness.ts';
import * as tickets_data from './data/index.ts';
import * as revision_feedback from './revision_feedback.ts';
import * as locale_configuration from './locale_configuration.ts';
import { stage_ownership_mode } from './logic/machine.ts';
import type { Ticket } from './contracts.ts';
export function require_restartable(
  sql: ReadSql,
  _principal: Principal,
  ticket_id: string,
): Ticket {
  const ticket = tickets_data.read_ticket(sql, ticket_id);
  if (
    stage_ownership_mode(ticket.stage, { worker_type_definition: ticket.workflow }) !== 'worker'
  ) {
    throw new PlannerError('validation', 'only a Worker-owned Stage has a worker step to restart', {
      ticket_id,
      stage: ticket.stage,
    });
  }
  if (ticket.locale === 'macos' && !locale_configuration.mac_is_present(sql, now_ms())) {
    throw new PlannerError(
      'validation',
      `${locale_configuration.WAITING_FOR_THE_MAC}: restart this Ticket once it is back`,
      { ticket_id },
    );
  }
  return ticket;
}
interface RestartOptions {
  start_worker_step: () => Promise<WorkerStepStartResult>;
  now: () => number;
  write_employee_configuration?: ((sql: TransactionSql, now: number) => void) | null;
}
export async function restart_worker(
  conversations: ConversationSystem,
  database: Database,
  principal: Principal,
  ticket_id: string,
  options: RestartOptions,
): Promise<Record<string, unknown>> {
  let killed_conversation_id: string | null = null;
  let killed_conversation_looked_running = false;
  let revision_restarted_in_fresh_conversation = false;
  await conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    const ticket = database.read((sql) => require_restartable(sql, principal, ticket_id));
    killed_conversation_id = ticket.conversation_id;
    if (killed_conversation_id !== null) {
      revision_restarted_in_fresh_conversation = database.read(
        (sql) => revision_feedback.snapshot(sql, ticket_id) !== null,
      );
      killed_conversation_looked_running = await conversations.is_running(killed_conversation_id);
      await conversations.kill(killed_conversation_id);
    }
    const now = options.now();
    database.write((sql) =>
      _unlink_give_back_the_claim_and_write_configuration(sql, ticket_id, {
        killed_conversation_id,
        write_employee_configuration: options.write_employee_configuration,
        now,
      }),
    );
  });
  // Starting obtains fresh ownership after the link mutation is complete.
  const start_result = await options.start_worker_step();
  return database.read((sql) =>
    _restart_result(
      sql,
      ticket_id,
      start_result,
      killed_conversation_id,
      killed_conversation_looked_running,
      revision_restarted_in_fresh_conversation,
    ),
  );
}
export function _unlink_give_back_the_claim_and_write_configuration(
  sql: TransactionSql,
  ticket_id: string,
  options: {
    killed_conversation_id: string | null;
    write_employee_configuration?: ((sql: TransactionSql, now: number) => void) | null;
    now: number;
  },
): void {
  const ticket = tickets_data.read_ticket(sql, ticket_id);
  if (ticket.worker_step_claim === 'out' || ticket.worker_step_claim === 'errored') {
    tickets_data.release_worker_step_claim(sql, ticket_id, {
      expected_claim: ticket.worker_step_claim,
      expected_claim_revision: ticket.worker_step_claim_revision,
      now: options.now,
    });
  }
  if (options.killed_conversation_id !== null) {
    tickets_data.clear_ticket_conversation_link(sql, ticket_id, {
      expected_conversation_id: options.killed_conversation_id,
      now: options.now,
    });
  }
  options.write_employee_configuration?.(sql, options.now);
}
export function _restart_result(
  sql: ReadSql,
  ticket_id: string,
  start_result: WorkerStepStartResult,
  killed_conversation_id: string | null,
  killed_conversation_looked_running: boolean,
  revision_restarted_in_fresh_conversation: boolean,
): Record<string, unknown> {
  const restarted = tickets_data.read_ticket(sql, ticket_id);
  return {
    ticket_id,
    killed_conversation_id,
    killed_conversation_looked_running,
    revision_restarted_in_fresh_conversation,
    employee_configuration: {
      employee_backend: restarted.employee_backend,
      employee_launch_model: restarted.employee_launch_model,
      employee_launch_reasoning_effort: restarted.employee_launch_reasoning_effort,
    },
    ticket_status: restarted.ticket_status,
    conversation_id: restarted.conversation_id,
    started: start_result.started,
    delivery_fate: start_result.delivery_fate,
    not_started_because:
      start_result.started || start_result.delivery_fate !== null
        ? null
        : worker_step_readiness.worker_step_blocker(sql, restarted, {
            worker_type_definition: restarted.workflow,
          }),
  };
}
