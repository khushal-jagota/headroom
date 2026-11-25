/** The display form of the need word. This is the only place the CLI words a need. */
import { WORKER_STATUS_LABEL } from '../tickets/logic/status_presentation.ts';
import { numeric_value } from '../core/http/numeric_json.ts';
import { TICKET_NEEDS, type TicketNeed } from '../tickets/contracts.ts';

const OWNER_NEEDS: Partial<Record<TicketNeed, string>> = {
  owner_approval: 'Owner: approve',
  owner_answer: 'Owner: answer',
  owner_stage: 'Owner: stage',
  owner_reply: 'Owner: reply',
};
/** `holder_kind` is the Ticket's `ceiling_holder.kind`; without it an agent approval names no role. */
export function need_display(need: unknown, holder_kind?: unknown): string {
  const word = String(need);
  if (word === 'agent_approval')
    return holder_kind === 'chief'
      ? 'Chief: approve'
      : holder_kind === 'manager'
        ? 'Manager: approve'
        : 'agent: approve';
  // The wire need 'working' includes an idle Worker whose step is still out.
  if (word === 'working') return WORKER_STATUS_LABEL;
  return OWNER_NEEDS[word as TicketNeed] ?? word;
}
const SETTLED: readonly string[] = ['backlog', 'done'];
/** `2 working, 1 Owner: approve; 9 backlog, 104 done`, or `no Tickets`. */
export function need_counts_display(counts: unknown): string {
  const table = (counts ?? {}) as Record<string, unknown>;
  const entry = (need: string) => {
    const count = numeric_value(table[need] as never) ?? 0;
    return count ? `${count} ${need_display(need)}` : null;
  };
  const live = TICKET_NEEDS.filter((need) => !SETTLED.includes(need))
    .map(entry)
    .filter(Boolean);
  const settled = SETTLED.map(entry).filter(Boolean);
  const groups = [live.join(', '), settled.join(', ')].filter(Boolean);
  return groups.length ? groups.join('; ') : 'no Tickets';
}
