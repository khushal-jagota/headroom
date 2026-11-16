import type { Principal } from '../../core/contracts.ts';
import type { Ticket } from '../contracts.ts';
export function review_transferred(before: Ticket, after: Ticket): boolean {
  return (
    before.pending_proposal !== null &&
    after.pending_proposal !== null &&
    before.pending_proposal.field === after.pending_proposal.field &&
    before.pending_proposal.body === after.pending_proposal.body &&
    before.pending_proposal.proposed_by === after.pending_proposal.proposed_by &&
    before.pending_proposal.created_at === after.pending_proposal.created_at &&
    !same_principal(before.ceiling_holder, after.ceiling_holder)
  );
}
export function same_principal(a: Principal, b: Principal): boolean {
  return a.kind === b.kind && a.id === b.id;
}
export function notify_new_review_holder(holder: Principal): boolean {
  return holder.kind !== 'owner';
}
