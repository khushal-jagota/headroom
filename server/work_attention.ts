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
export async function add_work_attention(
  database: Database,
  system: ConversationSystem | undefined,
  tickets: Record<string, unknown>[],
): Promise<void> {
  const records = new Map(
    database.read((sql) =>
      sql
        .all<{ id: string; value: string }>(
          "SELECT id,value FROM navigation_records WHERE kind='ticket'",
        )
        .map((r) => [r.id, JSON.parse(r.value) as Record<string, unknown>]),
    ),
  );
  const ids = new Set(
    tickets
      .map((t) => records.get(String(t.id))?.conversation_id)
      .filter((v): v is string => typeof v === 'string'),
  );
  const conversations = await signals(
    database.read((sql) => readAllDurableFacts(sql)),
    system,
    ids,
  );
  for (const row of tickets) {
    const stored = records.get(String(row.id));
    const c = conversations.get(String(stored?.conversation_id));
    const signal = c?.signal ?? { working: false, message_for_owner: false };
    const holder = stored?.ceiling_holder as { kind: string; id: string } | undefined;
    const broken = stored?.integrity_error != null;
    const parked = stored?.ticket_status === 'awaiting_approval';
    const owner = holder?.kind === 'owner' && holder.id === 'owner';
    Object.assign(row, {
      awaiting_reply: signal.message_for_owner,
      awaiting_answer: c?.awaiting_answer ?? false,
      awaiting_approval: !broken && parked && owner,
      awaiting_agent_approval: !broken && parked && !owner,
      assigned: !broken && stored?.stage_ownership === 'user',
      agent_state: broken
        ? 'errored'
        : signal.working
          ? 'working'
          : stored?.ticket_status === 'errored' || (c?.last_turn_failed && stored?.stage !== 'done')
            ? 'errored'
            : 'idle',
      conversation_signal: signal,
    });
  }
}
