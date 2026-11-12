/** The read-only readiness decision. Preserve first-refusal order. */
import type { ReadSql } from '../core/database/contracts.ts';
import type { Ticket } from '../tickets/contracts.ts';
import type { WorkflowDefinition } from '../worker_types/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { stage_definition, stage_ownership_mode, gating_field } from '../tickets/logic/machine.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import { now_ms } from '../executor/clock.ts';
import * as locale_configuration from '../tickets/locale_configuration.ts';
export function worker_step_blocker(
  sql: ReadSql,
  ticket: Ticket,
  options: {
    worker_type_definition: WorkflowDefinition;
  },
): string | null {
  if (ticket.is_backlogged) return 'the Ticket is backlogged';
  if (!locale_configuration.can_execute_locale(sql, ticket.locale)) {
    return locale_configuration.execution_unavailable_reason(sql, ticket.locale);
  }
  if (ticket.locale === 'macos' && !locale_configuration.mac_is_present(sql, now_ms())) {
    return locale_configuration.WAITING_FOR_THE_MAC;
  }
  if (stage_definition(options.worker_type_definition, ticket.stage).is_terminal) {
    return `the Stage ${ticket.stage} is terminal`;
  }
  const ownership = stage_ownership_mode(ticket.stage, options);
  if (ticket.ticket_status !== 'empty') {
    return `the Ticket is at ${ticket.ticket_status}, so no worker step is due`;
  }
  if (
    ownership === 'user' &&
    sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?', [
      ticket.id,
      ticket.stage,
    ])
  )
    return 'the user-owned Stage opener already ran for this Stage entry';
  if (gating_field(options.worker_type_definition, ticket.stage) === null) {
    return `the Stage ${ticket.stage} has no field for a worker to fill`;
  }
  let model = ticket.employee_launch_model;
  let backend = ticket.employee_backend;
  if (model === null && ticket.worker_type !== null) {
    const defaults = configured_worker_type_registry().require(ticket.worker_type).worker_profile;
    model = defaults.default_model;
    backend = defaults.default_backend;
  }
  if (model !== null) {
    const row = sql.get<{
      enabled: number;
    }>('SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
      require_conversation_backend_key(backend),
      model,
    ]);
    if (row && !row.enabled) return `the model ${model} is disabled`;
  }
  return null;
}
export function is_ready_for_worker_step(
  sql: ReadSql,
  ticket: Ticket,
  options: {
    worker_type_definition: WorkflowDefinition;
  },
): boolean {
  return worker_step_blocker(sql, ticket, options) === null;
}
