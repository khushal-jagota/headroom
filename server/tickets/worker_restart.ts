/** Explicit recovery of a Ticket worker. Runtime busy state never decides admission. */
import { PlannerError, type Principal } from '../core/contracts.ts';
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { WorkerStepStartResult } from '../runtime/worker_step_readiness_loop.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import * as worker_step_readiness from '../runtime/worker_step_readiness.ts';
import * as tickets_data from './data/index.ts';
import * as revision_feedback from './revision_feedback.ts';
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
  if (ticket.worker_step_claim === 'out' || ticket.worker_step_claim === 'errored') return ticket;
  if (ticket.conversation_id === null) return ticket;
  if (ticket.ticket_status === 'empty' && revision_feedback.snapshot(sql, ticket_id) !== null)
    return ticket;
  throw new PlannerError('validation', 'the Ticket has no worker step out to restart', {
    ticket_id,
    ticket_status: ticket.ticket_status,
  });
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
  const [killed_conversation_id, killed_conversation_looked_running] =
    await _prepare_worker_restart(conversations, database, principal, ticket_id, options);
  const start_result = await options.start_worker_step();
  return database.read((sql) =>
    _restart_result(
      sql,
      ticket_id,
      start_result,
      killed_conversation_id,
      killed_conversation_looked_running,
    ),
  );
}
export async function _prepare_worker_restart(
  conversations: ConversationSystem,
  database: Database,
  principal: Principal,
  ticket_id: string,
  options: Pick<RestartOptions, 'now' | 'write_employee_configuration'>,
): Promise<[string | null, boolean]> {
  let ticket = database.read((sql) => require_restartable(sql, principal, ticket_id));
  const rearming_idle_revision =
    ticket.worker_step_claim === 'none' && ticket.conversation_id !== null;
  if (rearming_idle_revision && options.write_employee_configuration != null) {
    throw new PlannerError(
      'already_running',
      'Employee configuration is frozen while a conversation or a worker step holds it',
      { ticket_id },
    );
  }
  let killed_conversation_id = ticket.conversation_id;
  let killed_conversation_looked_running = false;
  await conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    ticket = database.read((sql) => require_restartable(sql, principal, ticket_id));
    killed_conversation_id = ticket.conversation_id;
    if (killed_conversation_id !== null) {
      killed_conversation_looked_running = await conversations.is_running(killed_conversation_id);
      await conversations.kill(killed_conversation_id);
    }
    let source_retirement_proven = false;
    if (killed_conversation_id !== null) {
      source_retirement_proven =
        await conversations.retirement_is_confirmed(killed_conversation_id);
      if (!source_retirement_proven) {
        throw new PlannerError(
          'validation',
          'source retirement is unresolved; executor or operator settlement is required',
          {
            retirement_started: true,
            source_retired: null,
            reason: 'context_changed',
            corrective_action: 'verify_launch_context',
          },
        );
      }
    }
    try {
      database.write((sql) =>
        _give_the_claim_back_and_write_configuration(sql, principal, ticket_id, {
          write_employee_configuration: options.write_employee_configuration,
          now: options.now(),
        }),
      );
    } catch (error) {
      if (!(error instanceof PlannerError)) throw error;
      throw new PlannerError(error.code, error.message, {
        ...error.detail,
        retirement_started: killed_conversation_id !== null,
        source_retired: source_retirement_proven ? true : null,
      });
    }
  });
  return [killed_conversation_id, killed_conversation_looked_running];
}
export function _give_the_claim_back_and_write_configuration(
  sql: TransactionSql,
  principal: Principal,
  ticket_id: string,
  options: {
    write_employee_configuration?: ((sql: TransactionSql, now: number) => void) | null;
    now: number;
  },
): void {
  const ticket = require_restartable(sql, principal, ticket_id);
  if (ticket.worker_step_claim === 'out' || ticket.worker_step_claim === 'errored') {
    tickets_data.release_worker_step_claim(sql, ticket_id, {
      expected_claim: ticket.worker_step_claim,
      expected_claim_revision: ticket.worker_step_claim_revision,
      now: options.now,
    });
    if (ticket.conversation_id !== null) {
      tickets_data.clear_ticket_conversation_link(sql, ticket_id, {
        expected_conversation_id: ticket.conversation_id,
        now: options.now,
      });
    }
  }
  options.write_employee_configuration?.(sql, options.now);
}
export function _restart_result(
  sql: ReadSql,
  ticket_id: string,
  start_result: WorkerStepStartResult,
  killed_conversation_id: string | null,
  killed_conversation_looked_running: boolean,
): Record<string, unknown> {
  const restarted = tickets_data.read_ticket(sql, ticket_id);
  return {
    ticket_id,
    killed_conversation_id,
    killed_conversation_looked_running,
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
