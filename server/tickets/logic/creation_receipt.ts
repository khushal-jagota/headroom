import type { Ticket } from '../contracts.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
import { route_proposal } from './proposal_routing.ts';
import { stage_index } from './machine.ts';
export function supervision_receipt(
  ticket: Ticket,
  o: {
    worker_type_definition: WorkflowDefinition;
    review_recipient: string;
  },
): {
  supervision_receipt: string;
} {
  const d = o.worker_type_definition,
    stages = d.stages.filter((s) => !s.is_terminal),
    auto = stages.filter(
      (s) =>
        route_proposal({
          stage: s.id,
          ceiling: ticket.ceiling,
          holder: ticket.ceiling_holder,
          worker_type_definition: d,
        }).kind === 'auto_accept',
    ),
    remaining = stages.filter((s) => stage_index(d, s.id) >= stage_index(d, ticket.stage)),
    autonomous = remaining.filter((s) => auto.includes(s) && s.ownership_mode === 'worker'),
    review = remaining.find((s) => !auto.includes(s)),
    parts: string[] = [];
  if (!review) parts.push('Nothing in this grant ever parks for review.');
  parts.push(
    autonomous.length
      ? 'Runs unsupervised: ' + autonomous.map((s) => s.label).join(', ') + '.'
      : 'Nothing runs unsupervised.',
  );
  const users = remaining.filter((s) => auto.includes(s) && s.ownership_mode === 'user');
  if (users.length)
    parts.push(
      'User involvement is required at ' +
        users.map((s) => s.label).join(', ') +
        ', even though its proposals auto-accept.',
    );
  if (review) {
    parts.push(`First parks for review at ${review.label}, with ${o.review_recipient}.`);
    if (review.ownership_mode === 'user')
      parts.push(`${review.label} requires user involvement before its proposal review.`);
  }
  return { supervision_receipt: parts.join(' ') };
}
