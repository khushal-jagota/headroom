/** Clear the spent recap through its canonical writer, then request its replacement. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { Principal, RequestContext } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { edit_ticket } from '../tickets/data/index.ts';
import { update_manager } from '../managers/data.ts';
import { update_chief_recap } from '../worker_settings/service.ts';
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
  if (recipient.kind === 'ticket') {
    database.write((tx) =>
      edit_ticket(tx, recipient.id, {
        edit: { recap: '' },
        title_max_chars: 10_000,
        principal: context.principal,
        now: clock.nowUnix(),
      }),
    );
  } else if (recipient.kind === 'manager') {
    database.write((tx) => update_manager(tx, recipient.id, { edits: { recap: '' }, clock }));
  } else {
    update_chief_recap(database, { recap: '' });
  }
  return send_message(
    conversations,
    database,
    clock,
    context,
    recipient,
    RECAP_REQUEST_MESSAGE,
    'steer',
    { system_message: true },
  );
}
