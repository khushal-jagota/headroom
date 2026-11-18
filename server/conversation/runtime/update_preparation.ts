/** What an owner update interrupts, and the reopening of work after its trial. */
import type { ConversationState } from './state.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

function state_has_update_work(state: ConversationState): boolean {
  return !!(
    state.lock.locked ||
    state.phase !== 'idle' ||
    state.reserved_turn ||
    state.running_turn ||
    state.held_prompts.length ||
    state.admitted_sender_messages.size ||
    state.sender_messages_being_delivered.size ||
    state.steer_settlements.size ||
    state.late_steers.size ||
    state.backend_event_pump ||
    state.backend_events.length ||
    state.child_is_quarantined ||
    state.settlement_tasks.size
  );
}

/** Named on the update card for information. It never refuses or delays an update. */
export function update_interrupted_work(host: SqliteProcessConversationSystem): readonly string[] {
  const background = host.options.store.read_live_background_task_ids();
  const metadata = host.options.store.update_work_metadata();
  return [
    ...new Set(
      [...host.conversations]
        .filter(([identity, state]) => state_has_update_work(state) || background.has(identity))
        .map(
          ([identity, state]) =>
            metadata.get(identity)?.[0] ?? `${state.record.backend_key} conversation`,
        ),
    ),
  ];
}

/** A trial app starts with admission closed. This opens it when the installer ends. */
export function reopen_after_update(host: SqliteProcessConversationSystem): void {
  host.workspace_admission.reopen();
}

export async function run_with_workspace_admission(
  host: SqliteProcessConversationSystem,
  operation: () => void,
  options: { require_idle: boolean },
): Promise<boolean> {
  return host.workspace_admission.claim(operation, {
    idle: () => host.workspace_activity() === 'idle',
    require_idle: options.require_idle,
  });
}
