import type { Database } from './core/database/contracts.ts';
import type { ConversationSystem } from './conversation/contracts.ts';
import { readDurableFactsFor } from './conversation/publication/durable.ts';
import { pendingCredentialConversations } from './conversation/publication/coordinator.ts';
import { ticket_need, ticketIsTerminal } from './navigation/views/logic.ts';
import { TICKET_NEEDS, type TicketNeed } from './tickets/contracts.ts';
async function signals(
  durableRows: ReturnType<typeof readDurableFactsFor>,
  system: ConversationSystem | undefined,
  ids: Set<string>,
  pending_keys: ReadonlySet<string> = new Set(),
) {
  const durable = new Map(durableRows.map((r) => [r.conversation_id, r.durable]));
  const result = new Map<
    string,
    {
      signal: { working: boolean; message_for_owner: boolean };
      awaiting_answer: boolean;
      last_turn_failed: boolean;
    }
  >();
  for (const id of ids) {
    const d = durable.get(id);
    result.set(id, {
      signal: {
        working: !!(await system?.is_running(id)) || !!d?.has_live_background_task,
        message_for_owner: !!d?.message_for_owner,
      },
      awaiting_answer:
        pending_keys.has(id) ||
        !!(await system?.has_pending_permission_ask(id)) ||
        !!(await system?.has_pending_user_input(id)),
      last_turn_failed: !!d?.last_turn_failed,
    });
  }
  return result;
}
interface WorkAttention {
  awaiting_reply: boolean;
  awaiting_answer: boolean;
  awaiting_approval: boolean;
  awaiting_agent_approval: boolean;
  assigned: boolean;
  agent_state: 'idle' | 'setting_up' | 'working' | 'errored';
}
function empty_attention(): WorkAttention {
  return {
    awaiting_reply: false,
    awaiting_answer: false,
    awaiting_approval: false,
    awaiting_agent_approval: false,
    assigned: false,
    agent_state: 'idle',
  };
}
/** Done and backlogged Tickets wait on no one, so their need never reads a conversation. */
function settled(record: Record<string, unknown>): boolean {
  return !('integrity_error' in record) && (!!record.is_backlogged || ticketIsTerminal(record));
}
function count_needs(needs: TicketNeed[]): Partial<Record<TicketNeed, number>> {
  const counts: Partial<Record<TicketNeed, number>> = {};
  for (const need of TICKET_NEEDS) {
    const count = needs.filter((value) => value === need).length;
    if (count) counts[need] = count;
  }
  return counts;
}
export async function add_work_attention(
  database: Database,
  system: ConversationSystem | undefined,
  tickets: Record<string, unknown>[] = [],
  managers: Record<string, unknown>[] = [],
): Promise<void> {
  const manager_ids = new Set(managers.map((row) => String(row.id)));
  const requested = new Set(tickets.map((row) => String(row.id)));
  const ticket_ids = new Set(requested);
  const { records, children, links, conversation_ids, durable, pending_keys } = database.read(
    (sql) => {
      const children = new Map<string, string[]>([...manager_ids].map((id) => [id, []]));
      const links = new Map<string, string>();
      if (manager_ids.size) {
        const ids = [...manager_ids].sort();
        const placeholders = ids.map(() => '?').join(',');
        for (const row of sql.all<{ id: string; manager_id: string }>(
          `SELECT id, manager_id FROM tickets WHERE manager_id IN (${placeholders})`,
          ids,
        )) {
          children.get(row.manager_id)!.push(row.id);
          ticket_ids.add(row.id);
        }
        for (const row of sql.all<{ id: string; conversation_id: string | null }>(
          'SELECT m.id, a.conversation_id FROM managers m LEFT JOIN agents a ' +
            "ON a.agent_key='manager_'||m.id " +
            `WHERE m.id IN (${placeholders})`,
          ids,
        )) {
          if (row.conversation_id !== null) links.set(row.id, row.conversation_id);
        }
      }
      const records = new Map<string, Record<string, unknown>>();
      if (ticket_ids.size) {
        const ids = [...ticket_ids].sort();
        for (const row of sql.all<{ id: string; value: string }>(
          "SELECT id,value FROM navigation_records WHERE kind='ticket' AND id IN (" +
            ids.map(() => '?').join(',') +
            ')',
          ids,
        ))
          records.set(row.id, JSON.parse(row.value) as Record<string, unknown>);
      }
      const conversation_ids = new Set(links.values());
      // A requested row still shows its conversation's signal. A child counted only for its
      // Manager's needs reads no conversation when it is done or backlogged.
      for (const [id, row] of records) {
        if (typeof row.conversation_id === 'string' && (requested.has(id) || !settled(row)))
          conversation_ids.add(row.conversation_id);
      }
      return {
        records,
        children,
        links,
        conversation_ids,
        durable: readDurableFactsFor(sql, conversation_ids),
        pending_keys: pendingCredentialConversations(sql, conversation_ids),
      };
    },
  );
  const conversations = await signals(durable, system, conversation_ids, pending_keys);
  const attention = new Map<string, WorkAttention>();
  const needs = new Map<string, TicketNeed>();
  for (const [id, stored] of records) {
    const c = conversations.get(String(stored.conversation_id));
    const signal = c?.signal ?? { working: false, message_for_owner: false };
    const holder = stored.ceiling_holder as { kind: string; id: string } | undefined;
    const broken = stored.integrity_error != null;
    const waiting = !settled(stored);
    const parked = stored.ticket_status === 'awaiting_approval';
    const owner = holder?.kind === 'owner' && holder.id === 'owner';
    needs.set(id, ticket_need(stored, waiting ? (c ?? null) : null));
    attention.set(id, {
      awaiting_reply: waiting && signal.message_for_owner,
      awaiting_answer: waiting && (c?.awaiting_answer ?? false),
      awaiting_approval: waiting && !broken && parked && owner,
      awaiting_agent_approval: waiting && !broken && parked && !owner,
      assigned: waiting && !broken && stored.stage_ownership === 'user',
      agent_state: broken
        ? 'errored'
        : signal.working
          ? 'working'
          : stored.worker_setup
            ? 'setting_up'
            : stored.ticket_status === 'errored' || (c?.last_turn_failed && stored.stage !== 'done')
              ? 'errored'
              : 'idle',
    });
  }
  for (const row of tickets) {
    const id = String(row.id);
    const stored = records.get(id);
    const c = conversations.get(String(stored?.conversation_id));
    Object.assign(row, attention.get(id) ?? empty_attention(), {
      needs: needs.get(id) ?? ticket_need(row, null),
      conversation_signal: c?.signal ?? { working: false, message_for_owner: false },
      worker_setup: stored?.worker_setup ?? null,
    });
    if (!('ceiling_holder' in row)) row.ceiling_holder = stored?.ceiling_holder ?? null;
    if (!('blocked' in row)) row.blocked = !!stored?.blocked;
  }
  for (const row of managers) {
    const id = String(row.id);
    const own = conversations.get(links.get(id) ?? '');
    const signal = own?.signal ?? { working: false, message_for_owner: false };
    Object.assign(row, {
      conversation_signal: signal,
      ...empty_attention(),
      awaiting_reply: signal.message_for_owner,
      awaiting_answer: own?.awaiting_answer ?? false,
      agent_state: signal.working ? 'working' : own?.last_turn_failed ? 'errored' : 'idle',
      ticket_needs: count_needs(
        (children.get(id) ?? []).flatMap((child) => {
          const need = needs.get(child);
          return need ? [need] : [];
        }),
      ),
    });
  }
}
/** One Ticket body gains its need, and nothing else from the attention read. */
export async function add_ticket_need(
  database: Database,
  system: ConversationSystem | undefined,
  ticket: Record<string, unknown>,
): Promise<void> {
  const probe = [{ ...ticket }];
  await add_work_attention(database, system, probe);
  ticket.needs = probe[0]!.needs;
}
