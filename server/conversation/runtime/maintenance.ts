/** Local compaction, idle cleanup, and live-session task loss. Restart recovery stays separate. */
import type { SqliteProcessConversationSystem } from '../system.ts';
import type { Message, ReservedTurn } from './contracts.ts';
import type { ConversationState } from './state.ts';
import { own_settlement } from './state.ts';
import { PromptDeliveryMode, PromptQueueReason } from '../contracts.ts';
import { text_message_content } from '../message_content.ts';
import {
  AUTOMATIC_COMPACTION_AFTER_SECONDS,
  AUTOMATIC_COMPACTION_BEFORE_SECONDS,
  AUTOMATIC_COMPACTION_PROMPT,
  AUTOMATIC_COMPACTION_SENDER_LABEL,
  IDLE_CHILD_STOP_AFTER_SECONDS,
  IDLE_CHILD_SWEEP_INTERVAL_SECONDS,
  LOST_BACKGROUND_TASK_SENDER_LABEL,
} from './configuration.ts';

export function automatic_compaction_is_due(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): boolean {
  const activity_at = state.record.latest_agent_activity_at;
  if (activity_at === null || state.child_is_quarantined) return false;
  const age = Math.floor(host.unix_time_now()) - activity_at;
  return (
    age >= AUTOMATIC_COMPACTION_AFTER_SECONDS &&
    age < AUTOMATIC_COMPACTION_BEFORE_SECONDS &&
    state.record.latest_agent_activity_sequence >
      state.record.automatic_compaction_attempted_through_sequence
  );
}

export async function deliver_automatic_compaction(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reservation: ReservedTurn,
): Promise<void> {
  const message: Message = {
    held_prompt_id: '',
    system_generated: true,
    snapshot_sent_at_unix_milliseconds: 0,
    queue_reason: PromptQueueReason.requested,
    model_change: null,
    reasoning_effort_change: null,
    reasoning_effort_is_set: false,
    sender_message_id: null,
    sent_at_unix_milliseconds: null,
    sender: null,
    recipient: null,
    recovery_references: [],
    owner_read_through_sequence: null,
    content: text_message_content(AUTOMATIC_COMPACTION_PROMPT),
    sender_label: AUTOMATIC_COMPACTION_SENDER_LABEL,
    acknowledge_owner_on_failure: true,
    recording_receipt: null,
    delivery_uncertain: false,
  };
  try {
    await host.deliver_and_finalize(state, reservation, [message], false, false);
  } catch (error) {
    if (host.shutting_down) throw error;
    console.error(
      `conversation ${state.record.conversation_id} could not start automatic compaction`,
      error,
    );
    const termination_confirmed = await state.lock.run(async () => {
      if (state.child === null) return true;
      return await host.discard_child(state, state.child);
    });
    if (termination_confirmed) {
      await host.drain_held_prompts(state);
    } else {
      console.error(
        `conversation ${state.record.conversation_id} kept held traffic quarantined after automatic compaction child termination failed`,
      );
    }
    return;
  }
  // Refusal already releases the line. A running or uncertain turn keeps this a no-op.
  await host.drain_held_prompts(state);
}

export async function sweep_idle_children(host: SqliteProcessConversationSystem): Promise<void> {
  await host.workspace_admission.operation(async (admitted) => {
    if (!admitted || host.shutting_down) return;
    const due = host.options.store.conversations_due_for_automatic_compaction({
      due_at_or_before: Math.floor(host.unix_time_now()) - AUTOMATIC_COMPACTION_AFTER_SECONDS,
      activity_after: Math.floor(host.unix_time_now()) - AUTOMATIC_COMPACTION_BEFORE_SECONDS,
    });
    for (const conversation_id of due) {
      const state = host.conversation_state(conversation_id);
      if (state === null || state.lock.locked) continue;
      const reservation = await state.lock.run(async () => {
        if (
          state.phase === 'idle' &&
          state.held_prompts.length === 0 &&
          automatic_compaction_is_due(host, state)
        ) {
          return await host.reserve_turn(state, true);
        }
        return null;
      });
      if (reservation !== null) {
        void own_settlement(state, deliver_automatic_compaction(host, state, reservation)).catch(
          (error: unknown) => console.error('the idle conversation compaction failed', error),
        );
      }
    }
    const with_background_tasks = host.options.store.conversation_ids_with_live_background_tasks();
    const recovered: ConversationState[] = [];
    for (const state of host.conversations.values()) {
      if (state.child === null || state.lock.locked) continue;
      await state.lock.run(async () => {
        const child = state.child;
        if (child === null || state.phase !== 'idle') return;
        const was_quarantined = state.child_is_quarantined;
        if (!was_quarantined) {
          if (with_background_tasks.has(state.record.conversation_id)) return;
          const idle_for = host.monotonic_now() - state.last_touched_monotonic;
          if (
            idle_for < (host.options.idle_child_stop_after_seconds ?? IDLE_CHILD_STOP_AFTER_SECONDS)
          )
            return;
        }
        if (!(await host.discard_child(state, child))) return;
        if (was_quarantined && state.held_prompts.length > 0) recovered.push(state);
      });
    }
    for (const state of recovered) {
      await host.drain_held_prompts(state);
    }
  });
}

/** Own only the timer. The core owns settlement cancellation and shutdown ordering. */
export class IdleChildJanitor {
  readonly #host: SqliteProcessConversationSystem;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #sweep: Promise<void> | null = null;
  #stopped = false;
  constructor(host: SqliteProcessConversationSystem) {
    this.#host = host;
  }
  start(): void {
    if (this.#timer !== null || this.#sweep !== null) return;
    this.#stopped = false;
    this.#schedule();
  }
  #schedule(): void {
    if (this.#stopped || this.#host.shutting_down) return;
    const delay =
      this.#host.options.idle_child_sweep_interval_seconds ?? IDLE_CHILD_SWEEP_INTERVAL_SECONDS;
    this.#timer = setTimeout(() => {
      this.#timer = null;
      const sweep = sweep_idle_children(this.#host);
      this.#sweep = sweep;
      void sweep
        .catch((error: unknown) => console.error('the idle conversation child sweep failed', error))
        .finally(() => {
          this.#sweep = null;
          this.#schedule();
        });
    }, delay * 1000);
  }
  async stop(): Promise<void> {
    this.#stopped = true;
    if (this.#timer !== null) {
      clearTimeout(this.#timer);
      this.#timer = null;
    }
    await this.#sweep?.catch(() => {});
  }
}

/** Detach preserves durable task rows for the later restart-recovery owner. */
export async function detach_children(states: readonly ConversationState[]): Promise<void> {
  for (const state of states) {
    if (state.running_turn !== null) state.running_turn.callbacks_revoked = true;
    const child = state.child;
    state.child = null;
    state.child_event_sink = null;
    if (child === null) continue;
    try {
      await child.detach();
      if (!child.stop_outside_lock) state.local_child_retirement_proven = true;
    } catch (error) {
      state.child = child;
      state.child_is_quarantined = true;
      console.error(
        `conversation ${state.record.conversation_id} could not detach its backend child cleanly`,
        error,
      );
    }
  }
}

export function tell_agent_about_lost_background_tasks(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  descriptions: readonly string[],
): void {
  if (descriptions.length === 0 || host.shutting_down) return;
  const operation = async () => {
    for (const description of descriptions) {
      try {
        await host.send(
          state.record.conversation_id,
          text_message_content(
            `Your background task "${description}" stopped because your session ended. ` +
              'It did not finish. Read its output, then start it again or report what happened.',
          ),
          { sender_label: LOST_BACKGROUND_TASK_SENDER_LABEL, mode: PromptDeliveryMode.queue },
        );
      } catch (error) {
        if (host.shutting_down) throw error;
        console.error(
          `conversation ${state.record.conversation_id} could not report its lost background task`,
          error,
        );
      }
    }
  };
  void own_settlement(state, operation()).catch((error: unknown) =>
    console.error(
      `conversation ${state.record.conversation_id} could not report lost tasks`,
      error,
    ),
  );
}

export async function background_tasks_ended_with_child(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  if (host.shutting_down) return;
  const stopped = [...state.background_tasks_stopped_since_the_agent_last_acted.values()];
  state.background_tasks_stopped_since_the_agent_last_acted.clear();
  const lost = await host.persist(state, () =>
    host.options.store.take_background_tasks(state.record.conversation_id),
  );
  if (!state.being_killed) {
    tell_agent_about_lost_background_tasks(host, state, [...stopped, ...lost]);
  }
}
