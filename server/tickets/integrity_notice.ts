/** Startup faults use the ordinary owner-to-Chief system-message door. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { CHIEF, OWNER } from '../core/contracts.ts';
import type { PlannerError } from '../core/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { send_message } from '../message_delivery/service.ts';

export async function notify_chief(
  database: Database,
  conversations: ConversationSystem,
  clock: Clock,
  issues: readonly PlannerError[],
): Promise<void> {
  for (const issue of issues) {
    const ticket_id = String(issue.detail.ticket_id);
    const row = database.read((sql) =>
      sql.get<{ title: string }>('SELECT title FROM tickets WHERE id=?', [ticket_id]),
    );
    if (!row) continue;
    try {
      await send_message(
        conversations,
        database,
        clock,
        { principal: OWNER, conversationId: null },
        CHIEF,
        `The Ticket '${row.title}' is unusable because its saved record is invalid. ` +
          `Reason: ${String(issue.detail.reason)}. Panels remains available. ` +
          'Inspect this Ticket before any repair.',
        'queue',
        { system_message: true },
      );
    } catch (error) {
      if (is_shutdown_business_cancellation(error)) throw error;
      console.error('Could not tell Chief about a broken Ticket', error);
    }
  }
}
