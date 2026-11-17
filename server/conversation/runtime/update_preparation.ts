/** Owner update preparation: admission, local child retirement, and cancel. */
import { ConversationState, set_phase } from './state.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { observe_promise } from '../../core/promise_observer.ts';
import type { BackendChild } from '../backends/contracts.ts';
import * as controls from './controls.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

function state_has_update_work(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): boolean {
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

export function update_waiting_work(host: SqliteProcessConversationSystem): readonly string[] {
  const background = host.options.store.read_live_background_task_ids();
  const metadata = host.update_metadata ?? host.options.store.update_work_metadata();
  return [
    ...new Set(
      [...host.conversations]
        .filter(
          ([identity, state]) => state_has_update_work(host, state) || background.has(identity),
        )
        .map(
          ([identity, state]) =>
            metadata.get(identity)?.[0] ?? `${state.record.backend_key} conversation`,
        ),
    ),
  ];
}

export async function prepare_update(
  host: SqliteProcessConversationSystem,
  mode: 'idle' | 'now',
  signal?: AbortSignal,
): Promise<boolean> {
  host.require_runtime_ownership();
  if (!host.update_admission_owned) {
    if (!host.workspace_admission.close_starts())
      throw new Error('Another operation owns workspace admission.');
    host.update_admission_owned = true;
    host.update_generation = Symbol('update');
    host.update_metadata = host.options.store.update_work_metadata();
    host.update_stop_failed = false;
  }
  const generation = host.update_generation!;
  if (host.update_retirement !== null && host.update_retirement_generation !== generation) {
    if (host.update_retirement_pending) return false;
    host.update_retirement = null;
  }
  if (host.update_stop_failed) {
    host.update_retirement = null;
    host.update_stop_failed = false;
  }
  await Promise.all(
    [...host.conversations.values()].map((state) => host.ensure_local_custody(state)),
  );
  const background = host.options.store.read_live_background_task_ids();
  const local = [...host.conversations]
    .filter(
      ([identity, state]) =>
        (host.update_metadata?.get(identity)?.[1] ?? HOME_MACHINE_ID) === HOME_MACHINE_ID &&
        state.retained_child_reference === null &&
        !state.child?.stop_outside_lock,
    )
    .map(([, state]) => state);
  if (host.update_retirement === null) {
    if (host.workspace_admission.active) return false;
    if (
      mode === 'idle' &&
      (host.options.store.blocked_local_conversations.size > 0 ||
        local.some(
          (state) =>
            state_has_update_work(host, state) || background.has(state.record.conversation_id),
        ))
    )
      return false;
    host.update_retirement_generation = generation;
    host.update_retirement_pending = true;
    host.update_retirement = (async () => {
      try {
        for (const state of local) {
          if (host.update_generation !== generation) return;
          if (
            state.child === null &&
            state.local_child_retirement_proven &&
            !state_has_update_work(host, state) &&
            !background.has(state.record.conversation_id)
          )
            continue;
          await retire_update_child(host, state, generation);
        }
        for (const state of local) {
          while (state.backend_event_pump) await state.backend_event_pump;
          await Promise.all([...state.settlement_tasks]);
        }
        await host.options.store.finish_writes();
      } catch (error) {
        if (host.update_generation === generation) host.update_stop_failed = true;
        throw error;
      } finally {
        host.update_retirement_pending = false;
      }
    })();
    void host.update_retirement.catch(() => {});
  }
  if (signal) {
    if (!(await observe_promise(host.update_retirement, signal)).completed) return false;
  } else await host.update_retirement;
  if (signal?.aborted || host.update_generation !== generation) return false;
  if (host.workspace_admission.closed) return true;
  return host.workspace_admission.claim(() => {}, {
    idle: () => local.every((state) => !state_has_update_work(host, state)),
    require_idle: true,
  });
}

function update_event_failed(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): boolean {
  return (
    state.backend_event_failures >
    (host.acknowledged_event_failures.get(state.record.conversation_id) ?? 0)
  );
}

async function retire_update_child(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  generation: symbol,
): Promise<void> {
  state.being_killed = true;
  try {
    const lease = await host.acquire_settled(state);
    let child: BackendChild | null;
    try {
      if (host.update_generation !== generation) return;
      await controls.discard_all(host, state);
      child = state.child;
      if (child) host.child_stop_ownership.set(child, state);
    } finally {
      lease.release();
    }
    if (child !== null) {
      await host.native_operation(() => child.stop());
      await child.settled?.();
      await Promise.all([...state.settlement_tasks]);
      while (state.backend_event_pump) await state.backend_event_pump;
      const event_failed = update_event_failed(host, state);
      await state.lock.run(async () => {
        await host._confirm_local_child_stopped(state, child);
        state.child_is_quarantined = false;
        if (state.child === child) {
          state.child = null;
          state.child_event_sink = null;
        }
        if (state.running_turn) await host.end_turn(state, state.running_turn, 'interrupted', null);
        set_phase(state, state.running_turn ? 'running' : 'idle');
      });
      if (event_failed) throw new Error('A backend result did not settle before the update.');
    }
    // A canceled attempt cannot discard messages admitted after Cancel.
    if (host.update_generation === generation) await host.kill(state.record.conversation_id);
    if (host.update_generation === generation && update_event_failed(host, state))
      throw new Error('A backend result did not settle before the update.');
  } catch (error) {
    if (state.child) state.child_is_quarantined = true;
    throw error;
  } finally {
    state.being_killed = false;
    if (host.update_generation !== generation) await host.drain_held_prompts(state);
  }
}

export function cancel_update(host: SqliteProcessConversationSystem): boolean {
  host.workspace_admission.reopen();
  host.update_admission_owned = false;
  host.update_generation = null;
  host.update_stop_failed = false;
  host.update_metadata = null;
  host.acknowledged_event_failures = new Map(
    [...host.conversations].map(([identity, state]) => [identity, state.backend_event_failures]),
  );
  return true;
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
