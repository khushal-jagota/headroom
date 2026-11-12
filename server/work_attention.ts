import type { Database } from './core/database/contracts.ts';
import type { ConversationSystem } from './conversation/contracts.ts';
import { readAllDurableFacts } from './conversation/publication/durable.ts';
async function signals(
  durableRows: ReturnType<typeof readAllDurableFacts>,
  system: ConversationSystem | undefined,
  ids: Set<string>,
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
        !!(await system?.has_pending_permission_ask(id)) ||
        !!(await system?.has_pending_user_input(id)),
      last_turn_failed: !!d?.last_turn_failed,
    });
  }
  return result;
}
export async function read_running_conversation_count(
  database: Database,
  system: ConversationSystem | undefined,
): Promise<number> {
  const ids = new Set(
    database.read((sql) =>
      sql
        .all<{ conversation_id: string }>(
          "SELECT conversation_id FROM tickets WHERE conversation_id IS NOT NULL UNION SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff' AND conversation_id IS NOT NULL UNION SELECT a.conversation_id FROM managers si JOIN agents a ON a.agent_key='manager_'||si.id WHERE a.conversation_id IS NOT NULL",
        )
        .map((r) => r.conversation_id),
    ),
  );
  return [
    ...(
      await signals(
        database.read((sql) => readAllDurableFacts(sql)),
        system,
        ids,
      )
    ).values(),
  ].filter((v) => v.signal.working).length;
}
interface WorkAttention {
  awaiting_reply: boolean;
  awaiting_answer: boolean;
  awaiting_approval: boolean;
  awaiting_agent_approval: boolean;
  assigned: boolean;
  agent_state: 'idle' | 'working' | 'errored';
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
function roll_up(rows: WorkAttention[]): WorkAttention {
  return {
    awaiting_reply: rows.some((row) => row.awaiting_reply),
    awaiting_answer: rows.some((row) => row.awaiting_answer),
    awaiting_approval: rows.some((row) => row.awaiting_approval),
    awaiting_agent_approval: rows.some((row) => row.awaiting_agent_approval),
    assigned: rows.some((row) => row.assigned),
    agent_state: rows.some((row) => row.agent_state === 'working')
      ? 'working'
      : rows.some((row) => row.agent_state === 'errored')
        ? 'errored'
        : 'idle',
  };
}
export async function add_work_attention(
  database: Database,
  system: ConversationSystem | undefined,
  tickets: Record<string, unknown>[] = [],
  managers: Record<string, unknown>[] = [],
): Promise<void> {
  const manager_ids = new Set(managers.map((row) => String(row.id)));
  const ticket_ids = new Set(tickets.map((row) => String(row.id)));
  const { records, children, links, durable } = database.read((sql) => {
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
    return { records, children, links, durable: readAllDurableFacts(sql) };
  });
  const conversation_ids = new Set(links.values());
  for (const row of records.values()) {
    if (typeof row.conversation_id === 'string') conversation_ids.add(row.conversation_id);
  }
  const conversations = await signals(durable, system, conversation_ids);
  const attention = new Map<string, WorkAttention>();
  for (const [id, stored] of records) {
    const c = conversations.get(String(stored.conversation_id));
    const signal = c?.signal ?? { working: false, message_for_owner: false };
    const holder = stored.ceiling_holder as { kind: string; id: string } | undefined;
    const broken = stored.integrity_error != null;
    const parked = stored.ticket_status === 'awaiting_approval';
    const owner = holder?.kind === 'owner' && holder.id === 'owner';
    attention.set(id, {
      awaiting_reply: signal.message_for_owner,
      awaiting_answer: c?.awaiting_answer ?? false,
      awaiting_approval: !broken && parked && owner,
      awaiting_agent_approval: !broken && parked && !owner,
      assigned: !broken && stored.stage_ownership === 'user',
      agent_state: broken
        ? 'errored'
        : signal.working
          ? 'working'
          : stored.ticket_status === 'errored' || (c?.last_turn_failed && stored.stage !== 'done')
            ? 'errored'
            : 'idle',
    });
  }
  for (const row of tickets) {
    const id = String(row.id);
    const c = conversations.get(String(records.get(id)?.conversation_id));
    Object.assign(row, attention.get(id) ?? empty_attention(), {
      conversation_signal: c?.signal ?? { working: false, message_for_owner: false },
    });
  }
  for (const row of managers) {
    const id = String(row.id);
    const own = conversations.get(links.get(id) ?? '');
    const signal = own?.signal ?? { working: false, message_for_owner: false };
    Object.assign(row, empty_attention(), {
      conversation_signal: signal,
      awaiting_reply: signal.message_for_owner,
      awaiting_answer: own?.awaiting_answer ?? false,
      agent_state: signal.working ? 'working' : own?.last_turn_failed ? 'errored' : 'idle',
      ticket_rollup: roll_up((children.get(id) ?? []).map((child) => attention.get(child)!)),
    });
  }
}
