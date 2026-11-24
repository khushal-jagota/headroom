/** Who a child of machine code works for, read from its start's role identity alone. */
import type { StartRun } from '../../../wire.generated.ts';

/**
 * The same owners that the home registers for the child. A Ticket's Worker, the Chief, one
 * Manager, or any other conversation, which owns only itself.
 */
export type MacChildOwner =
  | { readonly kind: 'ticket'; readonly id: string }
  | { readonly kind: 'chief' }
  | { readonly kind: 'manager'; readonly id: string }
  | { readonly kind: 'conversation'; readonly id: string };

/** A Ticket identity names a Ticket's child, as it always has. Only other starts read the actor. */
export function child_owner(start: StartRun['resolved_start']): MacChildOwner {
  const identity = new Map(start.role_materials?.identity_environment_variables ?? []);
  const ticket_id = identity.get('PLAN_TICKET_ID');
  if (ticket_id) return { kind: 'ticket', id: ticket_id };
  const actor = identity.get('PLAN_ACTOR');
  if (actor === 'chief') return { kind: 'chief' };
  const manager_id = identity.get('PLAN_MANAGER_ID');
  if (actor === 'manager' && manager_id) return { kind: 'manager', id: manager_id };
  return { kind: 'conversation', id: start.conversation_id };
}

/** One start at a time per owner. A Ticket keeps its id as its key. */
export function child_owner_start_key(owner: MacChildOwner): string {
  switch (owner.kind) {
    case 'ticket':
      return owner.id;
    case 'chief':
      return 'chief';
    case 'manager':
      return `manager:${owner.id}`;
    case 'conversation':
      return `conversation:${owner.id}`;
  }
}

export function child_ticket_id(owner: MacChildOwner): string | null {
  return owner.kind === 'ticket' ? owner.id : null;
}
