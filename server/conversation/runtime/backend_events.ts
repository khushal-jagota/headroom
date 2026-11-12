/** Local backend news, serialized behind accepted prompt and steer receipts. */
import {
  BackendStreamEnded,
  BackgroundTaskEnded,
  BackgroundTaskStarted,
  ComposerCatalogReported,
  PermissionAskRaised,
  TurnEnded,
  UserInputRequested,
  VendorSessionCursorRebound,
} from '../backends/contracts.ts';
import type { BackendEventSink, BackendEventValue, TurnToken } from '../backends/contracts.ts';
import { ConversationRecord } from '../record/contracts.ts';
import { LifecycleKind as K } from '../record/lifecycle.ts';
import { interpret_backend_event } from '../logic/backend_events.ts';
import type { RuntimeMutation } from './contracts.ts';
import { field_mutation } from './save_attempt.ts';
import { same_turn, set_phase } from './state.ts';
import type { ConversationState } from './state.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import {
  BACKGROUND_TASK_ENDINGS_OF_THEIR_OWN,
  STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS,
} from './configuration.ts';
import { tell_agent_about_lost_background_tasks } from './maintenance.ts';

export function standard_error_tail(value: string | null): string | null {
  return value === null
    ? null
    : Array.from(value).slice(-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS).join('');
}

export async function take_backend_event(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken | null,
  event: BackendEventValue,
  expected_sink?: BackendEventSink,
): Promise<boolean> {
  await host.wait_for_reservation(state, token);
  const lease = await host.acquire_after_steers(state);
  let drain = false;
  let applied = false;
  let lost_background_tasks: readonly string[] = [];
  const background_task_news =
    event instanceof BackgroundTaskStarted ||
    event instanceof BackgroundTaskEnded ||
    event instanceof BackendStreamEnded;
  try {
    if (host.shutting_down || (expected_sink && state.child_event_sink !== expected_sink)) {
      return false;
    }
    const running = state.running_turn;
    const session =
      token === null &&
      (background_task_news ||
        event instanceof VendorSessionCursorRebound ||
        event instanceof ComposerCatalogReported);
    const live =
      session ||
      (running !== null &&
        same_turn(running.token, token) &&
        !running.ended &&
        !running.ending_is_the_cores);
    if (
      !live ||
      (running?.callbacks_revoked &&
        (event instanceof PermissionAskRaised || event instanceof UserInputRequested))
    ) {
      return false;
    }
    const agent_acted = token !== null;
    if (agent_acted) state.last_touched_monotonic = host.monotonic_now();
    const interpreted = interpret_backend_event(
      token,
      event,
      running,
      event instanceof TurnEnded
        ? host.options.store.lifecycle_facts(state.record.conversation_id)
        : [],
    );
    if (
      interpreted.lifecycle.length > 0 &&
      (event instanceof PermissionAskRaised || event instanceof UserInputRequested)
    ) {
      const identity = interpreted.lifecycle[0].identity;
      if (
        host.options.store
          .lifecycle_facts(state.record.conversation_id)
          .some((fact) => fact.kind === K.request && fact.identity === identity)
      ) {
        return false;
      }
    }
    const predicted = interpreted.predicted;
    const mutations: RuntimeMutation[] = [];
    if (predicted) {
      mutations.push(field_mutation(state, 'running_turn', predicted.ended ? null : predicted));
      if (predicted.ended) {
        mutations.push(field_mutation(state, 'last_ended_turn_token', predicted.token));
        mutations.push(
          field_mutation(
            state,
            'last_ended_turn_was_automatic_compaction',
            predicted.automatic_compaction,
          ),
        );
      }
    }
    let ended_task_description: string | null = null;
    const result = await host.persist(
      state,
      () =>
        host.options.store.commit_backend_event(
          (tx) => {
            const conversation_id = state.record.conversation_id;
            const written = host.options.store.append_in_transaction(
              tx,
              conversation_id,
              interpreted.payloads,
              {
                lifecycle: interpreted.lifecycle,
                agent_activity:
                  interpreted.payloads.length > 0 &&
                  !interpreted.compacted &&
                  running !== null &&
                  !running.automatic_compaction,
                automatic_compaction_confirmed:
                  interpreted.compacted ||
                  (event instanceof TurnEnded && running?.compaction_confirmed === true),
                automatic_compaction_result: interpreted.compaction_result,
                create_failure_notice: interpreted.failure_notice,
              },
            );
            if (event instanceof VendorSessionCursorRebound) {
              host.options.store.update_session_in_transaction(tx, conversation_id, {
                vendor_session_cursor: event.vendor_session_cursor,
              });
            } else if (event instanceof ComposerCatalogReported) {
              host.options.store.update_session_in_transaction(tx, conversation_id, {
                composer_catalog: event.composer_catalog,
              });
            } else if (event instanceof BackgroundTaskStarted) {
              host.options.store.record_background_task_started_in_transaction(
                tx,
                conversation_id,
                event.task_id,
                event.task_description,
              );
            } else if (event instanceof BackgroundTaskEnded) {
              ended_task_description =
                host.options.store.record_background_task_ended_in_transaction(
                  tx,
                  conversation_id,
                  event.task_id,
                );
            } else if (event instanceof BackendStreamEnded) {
              lost_background_tasks = [
                ...(agent_acted
                  ? []
                  : state.background_tasks_stopped_since_the_agent_last_acted.values()),
                ...host.options.store.take_background_tasks_in_transaction(tx, conversation_id),
              ];
            }
            return { result: true, written };
          },
          null,
          { always_announce: background_task_news },
        ),
      mutations,
    );
    if (!result.taken) return false;
    applied = true;
    if (agent_acted) state.background_tasks_stopped_since_the_agent_last_acted.clear();
    host.note_written(state, result.written);
    if (event instanceof VendorSessionCursorRebound) {
      state.record = new ConversationRecord({
        ...state.record,
        vendor_session_cursor: event.vendor_session_cursor,
      });
    } else if (event instanceof ComposerCatalogReported) {
      state.record = new ConversationRecord({
        ...state.record,
        composer_catalog: event.composer_catalog,
      });
    } else if (background_task_news) {
      if (event instanceof BackgroundTaskEnded) {
        state.last_touched_monotonic = host.monotonic_now();
        if (
          ended_task_description !== null &&
          !BACKGROUND_TASK_ENDINGS_OF_THEIR_OWN.has(event.task_status)
        ) {
          state.background_tasks_stopped_since_the_agent_last_acted.set(
            event.task_id,
            ended_task_description,
          );
        }
      } else if (event instanceof BackendStreamEnded) {
        state.background_tasks_stopped_since_the_agent_last_acted.clear();
      }
      host.options.publish_status_changed?.(state.record.conversation_id);
    }
    if (result.written.length > 0) {
      const last = result.written[result.written.length - 1];
      if (running !== null && !running.automatic_compaction && !interpreted.compacted) {
        state.record = new ConversationRecord({
          ...state.record,
          latest_agent_activity_at: last.created_at,
          latest_agent_activity_sequence: last.sequence,
        });
      }
      if (interpreted.compacted) {
        state.record = new ConversationRecord({
          ...state.record,
          automatically_compacted_through_sequence: state.record.latest_agent_activity_sequence,
          automatic_compaction_attempted_through_sequence:
            state.record.latest_agent_activity_sequence,
        });
      } else if (interpreted.compaction_result !== null) {
        state.record = new ConversationRecord({
          ...state.record,
          automatic_compaction_attempted_through_sequence:
            state.record.latest_agent_activity_sequence,
        });
      }
    }
    if (predicted?.ended) {
      set_phase(state, 'idle');
      host.options.emit_change?.();
      drain = true;
    }
    if (event instanceof TurnEnded && event.ending === 'failed') {
      const sequence = result.written.at(-1)?.sequence ?? null;
      console.error(
        `conversation turn failed conversation_id=${state.record.conversation_id} ` +
          `backend=${state.record.backend_key} sequence=${sequence} ` +
          `error=${JSON.stringify(event.error_summary)} ` +
          `standard_error_tail=${JSON.stringify(standard_error_tail(event.standard_error_tail))}`,
      );
    }
  } finally {
    lease.release();
  }
  if (applied) tell_agent_about_lost_background_tasks(host, state, lost_background_tasks);
  if (drain) host.schedule_drain(state);
  return applied;
}
