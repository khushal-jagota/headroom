/** Canonical presence admission and the temporary local-only composition policy. */
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Ticket } from './contracts.ts';
import { read_ticket_by_conversation_id } from './data/index.ts';
import {
  can_execute_locale,
  CURRENT_EXECUTION_LOCALE,
  execution_unavailable_reason,
} from './locale_configuration.ts';

export type TicketExecutionRefusal = (sql: ReadSql, ticket: Ticket) => PlannerError | null;
function refusal(sql: ReadSql, ticket: Ticket): PlannerError {
  return new PlannerError('validation', execution_unavailable_reason(sql, ticket.locale), {
    ticket_id: ticket.id,
    locale: ticket.locale,
    machine_locale: CURRENT_EXECUTION_LOCALE,
  });
}
export const canonical_ticket_execution_refusal: TicketExecutionRefusal = (sql, ticket) =>
  can_execute_locale(sql, ticket.locale) ? null : refusal(sql, ticket);
/** Remove/replace this policy when the real executor router is composed. */
export const local_ticket_execution_refusal: TicketExecutionRefusal = (sql, ticket) =>
  ticket.locale === CURRENT_EXECUTION_LOCALE ? null : refusal(sql, ticket);

export function conversation_execution_refusal(
  sql: ReadSql,
  conversation_id: string,
  policy: TicketExecutionRefusal,
): PlannerError | null {
  let ticket: Ticket;
  try {
    ticket = read_ticket_by_conversation_id(sql, conversation_id);
  } catch (error) {
    if (error instanceof PlannerError && error.code === 'not_found') return null;
    throw error;
  }
  return policy(sql, ticket);
}
