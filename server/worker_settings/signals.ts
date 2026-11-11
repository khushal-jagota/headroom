import type { Database } from '../core/database/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { ManagedChiefSettings } from './contracts.ts';
/** The roster owns identity. Conversation tables and the live system own these signals. */
export async function add_chief_conversation_signals(database: Database, conversations: ConversationSystem | undefined, chief: ManagedChiefSettings) {
  const state = database.read(sql => {
    const link = sql.get<{ conversation_id: string }>('SELECT conversation_id FROM agents WHERE agent_key = ? AND conversation_id IS NOT NULL', [chief.employee_id]);
    const id = link?.conversation_id ?? null;
    if (id === null) return { conversation_id: id, latest_turn_ended_sequence: 0, owner_read_through_sequence: 0, background: false, unread: false };
    const facts = sql.get<{ owner_read_through_sequence: number; latest_owner_message: number | null; background: number }>("SELECT c.owner_read_through_sequence, (SELECT MAX(sequence) FROM conversation_events WHERE conversation_id=c.conversation_id AND kind='message_to_owner') AS latest_owner_message, EXISTS (SELECT 1 FROM conversation_background_tasks b WHERE b.conversation_id=c.conversation_id) AS background FROM conversations c WHERE c.conversation_id=?", [id]);
    const latest = sql.get<{ sequence: number | null }>("SELECT MAX(sequence) AS sequence FROM conversation_events WHERE conversation_id=? AND kind='turn_ended'", [id]);
    return { conversation_id: id, latest_turn_ended_sequence: latest?.sequence ?? 0, owner_read_through_sequence: facts?.owner_read_through_sequence ?? 0, background: !!facts?.background, unread: facts?.latest_owner_message != null && facts.latest_owner_message > facts.owner_read_through_sequence };
  });
  const id = state.conversation_id;
  const running = id !== null && conversations ? await conversations.is_running(id) : false;
  const permission = id !== null && conversations ? await conversations.has_pending_permission_ask(id) : false;
  const question = id !== null && conversations ? await conversations.has_pending_user_input(id) : false;
  const signal = { working: running || state.background, message_for_owner: state.unread };
  return { ...chief, conversation_id: id, conversation_signal: signal, awaiting_reply: signal.message_for_owner, agent_working: signal.working, needs_me: permission || question, latest_turn_ended_sequence: state.latest_turn_ended_sequence, owner_read_through_sequence: state.owner_read_through_sequence };
}
