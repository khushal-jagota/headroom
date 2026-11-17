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
  AgentMessageDelta,
  ModelThinkingHappened,
  ToolCallProgress,
} from '../backends/contracts.ts';
import type { BackendEventSink, BackendEventValue, TurnToken } from '../backends/contracts.ts';
import { ConversationRecord } from '../record/contracts.ts';
import { LifecycleKind as K } from '../record/lifecycle.ts';
import { interpret_backend_event } from '../logic/backend_events.ts';
import type { InterpretedBackendEvent } from '../logic/backend_events.ts';
import type { AcceptedRecordEvent, RecordCommitParticipant } from '../record/contracts.ts';
import type { JsonObject } from '../../executor/wire.generated.ts';
import type { RuntimeMutation, CoreBackendEventSink } from './contracts.ts';
import { field_mutation, RemoteEventPrediction } from './save_attempt.ts';
import { acquire_after_steers } from './synchronization.ts';
import { turn_is_maintenance, same_turn, set_phase, own_settlement } from './state.ts';
import type { ConversationState } from './state.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import {
  BACKGROUND_TASK_ENDINGS_OF_THEIR_OWN,
  STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS,
  MODEL_THINKING_PULSE_INTERVAL_SECONDS,
} from './configuration.ts';
import { tell_agent_about_lost_background_tasks } from './maintenance.ts';
import { randomUUID } from 'node:crypto';
import type { ConversationLiveTailFrame } from '../events.ts';
import { AgentMessageDeltaFrame, ToolCallProgressFrame, ModelThinkingFrame } from '../events.ts';

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
    if (agent_acted) {
      await host.accept_automatic_start(state, token!);
      state.last_touched_monotonic = host.monotonic_now();
    }
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
    const compaction_confirmed =
      interpreted.compacted ||
      (event instanceof TurnEnded && running?.compaction_confirmed === true);
    const working_turn_compaction_confirmed =
      compaction_confirmed && running !== null && !running.automatic_compaction;
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
                automatic_compaction_confirmed: compaction_confirmed,
                working_turn_compaction_confirmed,
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
      if (working_turn_compaction_confirmed) {
        state.record = new ConversationRecord({
          ...state.record,
          working_turn_compacted_through_sequence:
            result.written.findLast((row) => row.payload.kind === 'context_compacted')?.sequence ??
            state.record.working_turn_compacted_through_sequence,
        });
      }
      if (interpreted.compacted) {
        state.record = new ConversationRecord({
          ...state.record,
          automatically_compacted_through_sequence:
            result.written.findLast((row) => row.payload.kind === 'context_compacted')?.sequence ??
            state.record.automatically_compacted_through_sequence,
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

export function make_sink(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): CoreBackendEventSink {
  const generation = randomUUID().replaceAll('-', '');
  const sink: CoreBackendEventSink = {
    generation,
    record_local_child_supervision: (path) =>
      host.options.store.record_child_supervision(state.record.conversation_id, generation, path),
    record_local_child_custody: (identity) =>
      host.options.store.record_child_custody(state.record.conversation_id, generation, identity),
    report: (token, event) =>
      own_settlement(
        state,
        (async () => {
          if (host.shutting_down || state.child_event_sink !== sink) return;
          if (
            token !== null &&
            same_turn(state.running_turn?.token ?? null, token) &&
            !state.running_turn?.ended
          )
            await host.accept_automatic_start(state, token);
          if (
            event instanceof AgentMessageDelta ||
            event instanceof ModelThinkingHappened ||
            event instanceof ToolCallProgress
          ) {
            if (token === null) return;
            const show = async () => {
              await host.wait_for_reservation(state, token);
              if (
                state.child_event_sink === sink &&
                same_turn(state.running_turn?.token ?? null, token) &&
                !state.running_turn?.ended
              ) {
                await host.accept_automatic_start(state, token);
                publish_live(host, state, token, event);
              }
            };
            if (state.reserved_turn) enqueue_backend_event(host, state, show);
            else publish_live(host, state, token, event);
            return;
          }
          enqueue_backend_event(host, state, async () => {
            await host.take_local_backend_event(state, token, event, sink);
          });
        })().catch((error: unknown) => {
          state.backend_event_failures += 1;
          throw error;
        }),
      ),
  };
  return sink;
}

function publish_live(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken,
  event: AgentMessageDelta | ModelThinkingHappened | ToolCallProgress,
): void {
  if (host.shutting_down || !same_turn(state.running_turn?.token ?? null, token)) return;
  host.options.require_runtime_ownership?.();
  let frame: ConversationLiveTailFrame;
  if (event instanceof AgentMessageDelta) frame = new AgentMessageDeltaFrame(event);
  else if (event instanceof ToolCallProgress) frame = new ToolCallProgressFrame(event);
  else {
    const now = host.monotonic_now();
    if (now - state.model_thinking_shown_at_monotonic < MODEL_THINKING_PULSE_INTERVAL_SECONDS)
      return;
    state.model_thinking_shown_at_monotonic = now;
    frame = new ModelThinkingFrame();
  }
  host.options.publish_frame?.(state.record.conversation_id, frame);
}

function enqueue_backend_event(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  event: () => Promise<void>,
): void {
  state.backend_events.push(event);
  if (state.backend_event_pump !== null || host.shutting_down) return;
  const pump = async () => {
    try {
      while (state.backend_events.length > 0 && !host.shutting_down) {
        const take = state.backend_events.shift()!;
        try {
          await take();
        } catch (error) {
          state.backend_event_failures += 1;
          console.error(
            `conversation ${state.record.conversation_id} could not take in a backend event`,
            error,
          );
        }
      }
    } finally {
      state.backend_event_pump = null;
    }
  };
  // Start after assignment, so a synchronous event cannot clear then overwrite the pump.
  state.backend_event_pump = Promise.resolve().then(pump);
}
