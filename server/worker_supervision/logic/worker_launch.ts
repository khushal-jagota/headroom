/** A Worker launch is named by its role identity, never by an inherited environment. */
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';

/** The Ticket of a Worker launch, or null for every other conversation. */
export function worker_ticket_id(resolved_start: ResolvedConversationStart): string | null {
  const identity = new Map(resolved_start.role_materials?.identity_environment_variables ?? []);
  const ticket_id = identity.get('PLAN_TICKET_ID');
  return identity.get('PLAN_ACTOR') === 'worker' && ticket_id ? ticket_id : null;
}
