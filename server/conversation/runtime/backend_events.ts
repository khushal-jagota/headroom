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
import type { InterpretedBackendEvent } from '../logic/backend_events.ts';
import type { AcceptedRecordEvent, RecordCommitParticipant } from '../record/contracts.ts';
import type { JsonObject } from '../../executor/wire.generated.ts';
import type { RuntimeMutation } from './contracts.ts';
import { field_mutation, RemoteEventPrediction } from './save_attempt.ts';
import { acquire_after_steers } from './synchronization.ts';
import { turn_is_maintenance, same_turn, set_phase } from './state.ts';
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
  const accepted = await take_backend_event_with_receipt(host, state, token, event, {
    expected_sink,
  });
  return accepted.taken && accepted.result.applied === true;
}
export async function take_backend_event_with_receipt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken | null,
  event: BackendEventValue,
  options: {
    expected_sink?: BackendEventSink;
    in_commit?: RecordCommitParticipant<JsonObject>;
    retry_later_at?: number;
  } = {},
): Promise<AcceptedRecordEvent<JsonObject>> {
  await host.wait_for_reservation(state, token, options.retry_later_at);
  const lease = await acquire_after_steers(state, options.retry_later_at);
  let accepted: AcceptedRecordEvent<JsonObject> = {
    result: { applied: false },
    taken: false,
    written: [],
  };
  let drain = false;
  let applied = false;
  let lost_background_tasks: readonly string[] = [];
  const background_task_news =
    event instanceof BackgroundTaskStarted ||
    event instanceof BackgroundTaskEnded ||
    event instanceof BackendStreamEnded;
  try {
    if (host.shutting_down) return accepted;
    const running = state.running_turn;
    const session =
      token === null &&
      (background_task_news ||
        event instanceof VendorSessionCursorRebound ||
        event instanceof ComposerCatalogReported);
    let live =
      session ||
      (running !== null &&
        same_turn(running.token, token) &&
        !running.ended &&
        !running.ending_is_the_cores);
    if (
      (options.expected_sink !== undefined && state.child_event_sink !== options.expected_sink) ||
      (running?.callbacks_revoked &&
        (event instanceof PermissionAskRaised || event instanceof UserInputRequested))
    ) {
      live = false;
    }
    const agent_acted = live && token !== null;
    if (agent_acted) state.last_touched_monotonic = host.monotonic_now();
    let interpreted: InterpretedBackendEvent = live
      ? interpret_backend_event(
          token,
          event,
          running,
          event instanceof TurnEnded
            ? host.options.store.lifecycle_facts(state.record.conversation_id)
            : [],
        )
      : {
          payloads: [],
          lifecycle: [],
          predicted: null,
          compacted: false,
          compaction_result: null,
          failure_notice: false,
        };
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
        live = false;
        interpreted = {
          payloads: [],
          lifecycle: [],
          predicted: null,
          compacted: false,
          compaction_result: null,
          failure_notice: false,
        };
      }
    }
    if (!live && options.in_commit === undefined) return accepted;
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
    const prediction = new RemoteEventPrediction(mutations);
    let ended_task_description: string | null = null;
    const result = await host.persist(
      state,
      () =>
        host.options.store.commit_backend_event<JsonObject>(
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
                  !turn_is_maintenance(running),
                automatic_compaction_confirmed:
                  interpreted.compacted ||
                  (event instanceof TurnEnded && running?.compaction_confirmed === true),
                automatic_compaction_result: interpreted.compaction_result,
                create_failure_notice: interpreted.failure_notice,
              },
            );
            if (live && event instanceof VendorSessionCursorRebound) {
              host.options.store.update_session_in_transaction(tx, conversation_id, {
                vendor_session_cursor: event.vendor_session_cursor,
              });
            } else if (live && event instanceof ComposerCatalogReported) {
              host.options.store.update_session_in_transaction(tx, conversation_id, {
                composer_catalog: event.composer_catalog,
              });
            } else if (live && event instanceof BackgroundTaskStarted) {
              host.options.store.record_background_task_started_in_transaction(
                tx,
                conversation_id,
                event.task_id,
                event.task_description,
              );
            } else if (live && event instanceof BackgroundTaskEnded) {
              ended_task_description =
                host.options.store.record_background_task_ended_in_transaction(
                  tx,
                  conversation_id,
                  event.task_id,
                );
            } else if (live && event instanceof BackendStreamEnded) {
              lost_background_tasks = [
                ...(agent_acted
                  ? []
                  : state.background_tasks_stopped_since_the_agent_last_acted.values()),
                ...host.options.store.take_background_tasks_in_transaction(tx, conversation_id),
              ];
            }
            return { result: { applied: live }, written };
          },
          options.in_commit === undefined
            ? null
            : (tx, take) => {
                const outcome = options.in_commit!(tx, take);
                if (!outcome.taken) prediction.rollback();
                return outcome;
              },
          { always_announce: live && background_task_news },
        ),
      mutations.length > 0 ? [prediction] : [],
      options.retry_later_at,
    );
    accepted = result;
    if (!result.taken) return result;
    applied = true;
    if (agent_acted) state.background_tasks_stopped_since_the_agent_last_acted.clear();
    host.note_written(state, result.written);
    if (live && event instanceof VendorSessionCursorRebound) {
      state.record = new ConversationRecord({
        ...state.record,
        vendor_session_cursor: event.vendor_session_cursor,
      });
    } else if (live && event instanceof ComposerCatalogReported) {
      state.record = new ConversationRecord({
        ...state.record,
        composer_catalog: event.composer_catalog,
      });
    } else if (live && background_task_news) {
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
      if (running !== null && !turn_is_maintenance(running) && !interpreted.compacted) {
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
    if (live && event instanceof TurnEnded && event.ending === 'failed') {
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
  return accepted;
}
