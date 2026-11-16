/** Clear the spent recap through its canonical writer, then request its replacement. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { Principal, RequestContext } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { requireAbove } from '../core/authority/service.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { edit_ticket } from '../tickets/data/index.ts';
import { read_manager, update_manager } from '../managers/data.ts';
import { update_chief_recap_in_transaction } from '../worker_settings/service.ts';
import type { MessageDeliveryResult } from './contracts.ts';
import { send_message } from './service.ts';

export const RECAP_REQUEST_MESSAGE =
  'File a short, top-level recap with your recap tool. ' +
  'Do not send the user a message. ' +
  'Workers: `panels worker recap`. Managers: `panels manager set <manager-id> recap`. ' +
  'Chief: `PUT /api/workers/chief-of-staff/recap`.';

export async function request_recap(
  conversations: ConversationSystem,
  database: Database,
  clock: Clock,
  context: RequestContext,
  recipient: Principal,
): Promise<MessageDeliveryResult> {
  if (!['ticket', 'manager', 'chief'].includes(recipient.kind)) {
    throw new PlannerError(
      'validation',
      'a recap can be asked of a ticket, a Manager, or the Chief',
      {
        kind: recipient.kind,
      },
    );
  }
  const message = database.write((tx) => {
    if (recipient.kind === 'chief') requireOwner(context);
    else
      requireAbove(tx, context.principal, {
        kind: recipient.kind as 'ticket' | 'manager',
        id: recipient.id,
      });
    const requester =
      context.principal.kind === 'owner'
        ? 'the owner'
        : context.principal.kind === 'chief'
          ? 'Chief'
          : `Manager ${read_manager(tx, context.principal.id).item.title}`;
    if (recipient.kind === 'ticket') {
      edit_ticket(tx, recipient.id, {
        edit: { recap: '' },
        title_max_chars: 10_000,
        principal: context.principal,
        now: clock.nowUnix(),
      });
    } else if (recipient.kind === 'manager') {
      update_manager(tx, recipient.id, { edits: { recap: '' }, clock });
    } else {
      update_chief_recap_in_transaction(tx, { recap: '' });
    }
    return `${RECAP_REQUEST_MESSAGE}\nRequested by ${requester}.`;
  });
  return send_message(conversations, database, clock, context, recipient, message, 'steer', {
    system_message: true,
  });
}
