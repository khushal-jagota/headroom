/** Retained executor runs keep their exact child identity across VPS restarts. */
import { same_reference } from '../../executor/child_reference.ts';
import type {
  BackendChild,
  BackendChildFactory,
  ChildReference,
  RetainedBackendChildFactory,
  RetainedBackendRun,
  TurnToken,
  BackendEventValue,
  BackendEventSink,
} from '../backends/contracts.ts';
import {
  DeliveryCancelled,
  PromptWriteRefused,
  BackendPromptRejected,
  BackendSpawnFailed,
  SessionLoadFailed,
  NeedsRebind,
} from '../backends/contracts.ts';
import type { ReservedTurn } from './contracts.ts';
import { Deferred } from '../../core/deferred.ts';
import { field_mutation } from './save_attempt.ts';
import { is_shutdown_business_cancellation } from '../shutdown.ts';
import { AnswerFate, PromptDeliveryMode } from '../contracts.ts';
import {
  AnswerAppliedEventPayload,
  PermissionAnsweredEventPayload,
  UserInputAnsweredEventPayload,
  UserInputRequestedEventPayload,
} from '../events.ts';
import { MessageText, message_content_text } from '../message_content.ts';
import { adopted_running_turn } from '../logic/retained_turn.ts';
import { read_facts } from '../record/data/lifecycle.ts';
import type {
  AcceptedRecordEvent,
  OpenRunTurn,
  RecordCommitParticipant,
  RunningTurn,
  UndeliveredTurnPrompt,
} from '../record/contracts.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../record/lifecycle.ts';
import type { JsonObject } from '../../executor/wire.generated.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { RemoteTurnRejected, admit_remote_turn } from './remote_turn.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationState, own_settlement, same_turn, set_phase } from './state.ts';
import { background_tasks_ended_with_child } from './maintenance.ts';
import { acquire_after_steers, contention_only_write } from './synchronization.ts';
import { write_replayed_prompt } from './delivery.ts';
import { log_backend_failure } from '../backends/failure_summary.ts';
import { take_backend_event_with_receipt } from './backend_events.ts';

/** Undefined for a key with no live instance, such as a saved conversation on a removed driver. */
export function child_factory_for(
  host: SqliteProcessConversationSystem,
  key: string,
): BackendChildFactory | undefined {
  const entry = host.options.backend_registry.get(key);
  return entry?.kind === 'live' ? entry.instance.child_factory : undefined;
}
export function retained_factory(
  factory: BackendChildFactory | undefined,
): RetainedBackendChildFactory | null {
  if (factory === undefined) return null;
  const candidate = factory as Partial<RetainedBackendChildFactory>;
  return typeof candidate.retained_runs === 'function' && typeof candidate.recover === 'function'
    ? (factory as RetainedBackendChildFactory)
    : null;
}
type AdoptedRun = Omit<RetainedBackendRun, 'turn'> & { readonly turn: TurnToken };
function admitted_runs(factory: BackendChildFactory | undefined): readonly AdoptedRun[] {
  return (retained_factory(factory)?.retained_runs() ?? []).flatMap((run) => {
    let turn: TurnToken;
    try {
      turn = admit_remote_turn(run.turn);
    } catch (error) {
      if (!(error instanceof RemoteTurnRejected)) throw error;
      console.error(
        `Refusing retained run: conversation=${run.reference.conversation_id} child=${run.reference.child_id}`,
        error,
      );
      return [];
    }
    return [{ ...run, turn }];
  });
}
export function retained_open_runs(
  factories: readonly BackendChildFactory[],
): ReadonlyMap<string, OpenRunTurn> {
  const result = new Map<string, OpenRunTurn>();
  for (const factory of new Set(factories))
    for (const run of admitted_runs(factory))
      if (!run.stop_requested && !run.retirement_confirmed)
        result.set(run.reference.conversation_id, {
          turn_number: run.turn.turn_number,
          executor_process_current: run.incarnation_current,
          ...(run.pending_prompt !== null ? { prompt_admission_pending: true } : {}),
        });
  return result;
}
export function retained_run(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): AdoptedRun | null {
  return (
    admitted_runs(child_factory_for(host, state.record.backend_key)).find(
      (run) => run.reference.conversation_id === state.record.conversation_id,
    ) ?? null
  );
}
export async function confirm_remote_prompt_accepted(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
  turn: TurnToken,
): Promise<void> {
  const state = host.conversation_state_for_admitted_remote_acknowledgement(
    reference.conversation_id,
  );
  if (state === null || !same_reference(state.retained_child_reference, reference)) return;
  await own_settlement(
    state,
    (async () => {
      await host.wait_for_reservation(state, turn);
      host.options.require_runtime_ownership?.();
      const lease = await acquire_after_steers(state, undefined, { admitted_settlement: true });
      try {
        host.options.require_runtime_ownership?.();
        if (!same_turn(state.running_turn?.token ?? null, turn)) return;
        state.last_touched_monotonic = host.monotonic_now();
        if (same_reference(state.retained_child_reference, reference))
          await contention_only_write(() => {
            host.options.require_runtime_ownership?.();
            host.start_admission.accept(host.options.store.start_admission_database, turn);
          });
      } finally {
        lease.release();
      }
    })(),
  );
}
/** Native command facts settle a stale active turn without dependence on artifact delivery. */
export async function reconcile_remote_command(
  host: SqliteProcessConversationSystem,
  command: WireValue<'ChildCommand'>,
  outcome: WireValue<'CommandOutcome'>,
): Promise<void> {
  if (
    command.turn === null ||
    command.turn.conversation_id !== command.child.conversation_id ||
    command.command_id !== outcome.command_id ||
    !same_reference(command.child, outcome.child)
  )
    return;
  const failed_prompt =
    command.operation === 'write_prompt' &&
    outcome.disposition !== 'succeeded' &&
    outcome.result.reason !== 'executor_process_replaced' &&
    outcome.result.reason !== 'delivery_withdrawn';
  const no_turn =
    command.operation === 'steer' &&
    outcome.disposition === 'succeeded' &&
    outcome.result.kind === 'steer_refused' &&
    outcome.result.refusal_reason === 'no_running_turn_to_steer_into';
  if (!failed_prompt && !no_turn) return;
  const turn = admit_remote_turn(command.turn);
  const state = host.conversation_state_for_admitted_remote_acknowledgement(
    command.child.conversation_id,
  );
  if (state === null || !same_reference(state.retained_child_reference, command.child)) return;
  await own_settlement(
    state,
    (async () => {
      await host.wait_for_reservation(state, turn);
      const lease = await acquire_after_steers(state, undefined, { admitted_settlement: true });
      try {
        host.options.require_runtime_ownership?.();
        const running = state.running_turn;
        if (
          !same_reference(state.retained_child_reference, command.child) ||
          running === null ||
          !same_turn(running.token, turn) ||
          running.ended ||
          running.ending_is_the_cores ||
          // A replayed prompt's own delivery path settles its failure.
          (failed_prompt &&
            state.reserved_turn !== null &&
            same_turn(state.reserved_turn.token, turn))
        )
          return;
        const detail = no_turn
          ? 'The remote agent has no running turn to steer into.'
          : typeof outcome.result.error_message === 'string'
            ? outcome.result.error_message
            : 'The remote prompt failed.';
        await host.persist(state, () =>
          outcome.disposition === 'uncertain'
            ? host.start_admission.failed_attempt(host.options.store.start_admission_database, turn)
            : host.start_admission.reject(host.options.store.start_admission_database, turn),
        );
        if (outcome.disposition === 'uncertain') {
          state.delivery_uncertain = true;
          state.child_is_quarantined = true;
        }
        await host.end_turn(state, running, 'failed', detail);
        set_phase(state, 'idle');
      } finally {
        lease.release();
      }
      if (!host.shutting_down) host.schedule_drain(state);
    })(),
  );
}
export function restore_retained_run(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): void {
  const retained = retained_factory(child_factory_for(host, state.record.backend_key))
    ?.retained_runs()
    .find((run) => run.reference.conversation_id === state.record.conversation_id);
  if (retained === undefined) return;
  state.delivery_uncertain = retained.delivery_uncertain;
  state.retained_child_reference = retained.retirement_confirmed ? null : retained.reference;
  state.has_delivered_prompt = true;
  state.last_ended_turn_token = retained_run(host, state)?.turn ?? null;
  state.child_is_quarantined = !retained.retirement_confirmed;
}
export function initialize_retained_runs(
  host: SqliteProcessConversationSystem,
): ReadonlySet<string> {
  const retained = [
    ...new Set(
      host.options.backend_registry
        .list()
        .flatMap((entry) => (entry.kind === 'live' ? [entry.instance.child_factory] : [])),
    ),
  ].flatMap(admitted_runs);
  const open = new Map(
    retained
      .filter((run) => !run.stop_requested && !run.retirement_confirmed)
      .map((run) => [run.reference.conversation_id, run]),
  );
  const adopted = new Set<string>();
  for (const record of host.options.store.read_conversation_records(
    retained.map((run) => run.reference.conversation_id),
  )) {
    const state = new ConversationState(record),
      run = open.get(record.conversation_id);
    for (const retained_run of retained.filter(
      (entry) => entry.reference.conversation_id === record.conversation_id,
    ))
      host.options.store.start_admission_database.read((sql) =>
        host.start_admission.restore_bindings(sql, retained_run.turn),
      );
    restore_retained_run(host, state);
    if (run !== undefined) {
      const facts = host.options.store.lifecycle_facts(record.conversation_id);
      const has_user_input = facts.some(
        (fact) =>
          fact.kind === K.request &&
          fact.operation === O.user_input &&
          fact.turn_number === run.turn.turn_number,
      );
      const turn_start = has_user_input
        ? host.options.store.current_turn_start_sequence(record.conversation_id)
        : null;
      const questions = new Map(
        turn_start !== null
          ? host.options.store
              .read_events_after(record.conversation_id, turn_start)
              .flatMap((row) =>
                row.payload instanceof UserInputRequestedEventPayload
                  ? [[row.payload.request_id, row.payload.questions] as const]
                  : [],
              )
          : [],
      );
      const pending = run.incarnation_current && run.pending_prompt !== null;
      const running = pending
        ? null
        : adopted_running_turn(run.turn, facts, questions, {
            executor_process_current: run.incarnation_current,
          });
      if (running !== null)
        running.memory_notice_only = host.options.store.open_turn_holds_only_memory_notices(
          record.conversation_id,
        );
      if (run.incarnation_current && (pending || running !== null || !run.delivery_uncertain)) {
        const sink = host.make_sink(state);
        // A run on a backend this build lacks is not recovered: it settles as unrecovered.
        const child =
          retained_factory(child_factory_for(host, record.backend_key))?.recover(run.reference, {
            event_sink: sink,
          }) ?? null;
        if (child !== null) {
          state.child_event_sink = sink;
          state.child = child;
          state.retained_child_reference = run.reference;
          state.child_is_quarantined = false;
          state.delivery_uncertain = false;
          state.last_touched_monotonic = host.monotonic_now();
          adopted.add(record.conversation_id);
        }
      }
      state.running_turn = running;
      if (running !== null || pending) state.last_ended_turn_token = null;
      set_phase(state, running === null ? 'idle' : 'running');
      // Without a recovered child the prompt settles as uncertain and is never sent again.
      if (pending) resume_pending_admission(host, state, run, state.child);
    }
    host.conversations.set(record.conversation_id, state);
  }
  return adopted;
}
/** A crash lost the caller's metadata. Observe native truth without reconstructing a prompt row. */
function resume_pending_admission(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  run: AdoptedRun,
  child: BackendChild | null,
): void {
  const prompt = run.pending_prompt;
  if (prompt?.arguments.kind !== 'write_prompt') return;
  const reservation: ReservedTurn = {
    token: run.turn,
    resolved: new Deferred<void>(),
    automatic_compaction: prompt.arguments.automatic_compaction ?? false,
    automatic_notice: false,
    memory_notice_only: false,
    prompt_accepted: false,
    prompt_write_attempted: true,
    messages: [],
    mode: prompt.arguments.mode,
    shutdown_owned: false,
    shutdown_fate: null,
  };
  state.reserved_turn = reservation;
  set_phase(state, 'starting');
  const task = own_settlement(
    state,
    Promise.resolve().then(async () => {
      let accepted: Awaited<ReturnType<BackendChild['write_prompt']>> | null = null;
      let refused = false;
      try {
        const resume = child?.resume_prompt_admission?.bind(child);
        if (resume === undefined)
          throw new Error('The retained backend cannot observe its pending prompt');
        accepted = await host.native_operation(() => resume(run.turn));
      } catch (error) {
        if (host.shutting_down || reservation.shutdown_owned) return;
        refused =
          error instanceof PromptWriteRefused ||
          error instanceof BackendPromptRejected ||
          error instanceof BackendSpawnFailed ||
          error instanceof SessionLoadFailed ||
          error instanceof NeedsRebind;
      }
      await state.lock.run(async () => {
        if (
          host.shutting_down ||
          reservation.shutdown_owned ||
          state.reserved_turn !== reservation ||
          !same_reference(state.retained_child_reference, run.reference)
        )
          return;
        const facts = host.options.store.lifecycle_facts(state.record.conversation_id);
        const running =
          accepted === null
            ? null
            : adopted_running_turn(run.turn, facts, new Map(), {
                executor_process_current: true,
              });
        await host.persist(
          state,
          () =>
            host.options.store.transition_lifecycle(state.record.conversation_id, [
              {
                kind: K.turn,
                identity: String(run.turn.turn_number),
                turn_number: run.turn.turn_number,
                operation: O.turn,
                status: accepted !== null ? S.running : S.unavailable,
                provider_identity: null,
              },
              {
                kind: K.attempt,
                identity: `prompt:${run.turn.turn_number}`,
                turn_number: run.turn.turn_number,
                operation: O.prompt,
                status: accepted !== null ? S.started : refused ? S.refused : S.unknown,
                provider_identity: null,
              },
            ]),
          [field_mutation(state, 'running_turn', running)],
        );
        if (accepted?.agent_acceptance_proven) await host.accept_automatic_start(state, run.turn);
        else if (refused)
          await host.persist(state, () =>
            host.start_admission.reject(host.options.store.start_admission_database, run.turn),
          );
        else if (accepted === null)
          await host.persist(state, () =>
            host.start_admission.failed_attempt(
              host.options.store.start_admission_database,
              run.turn,
            ),
          );
        state.has_delivered_prompt ||= accepted !== null;
        state.delivery_uncertain = accepted === null && !refused;
        state.child_is_quarantined = state.delivery_uncertain;
        state.reserved_turn = null;
        set_phase(state, running === null ? 'idle' : 'running');
        reservation.resolved.resolve();
        host.options.emit_change?.();
      });
      if (!host.shutting_down) host.schedule_drain(state);
    }),
  );
  void task.catch((error) => console.error('A retained prompt admission could not settle.', error));
}
export async function take_answer_application(
  host: SqliteProcessConversationSystem,
  turn: TurnToken,
  answer_id: string,
  provider_id: string,
  kind: string,
  in_commit: RecordCommitParticipant<JsonObject>,
  retry_later_at?: number,
): Promise<AcceptedRecordEvent<JsonObject>> {
  const state = host.conversation_state(turn.conversation_id);
  if (state === null) throw new TypeError('answer application names unknown conversation');
  return own_settlement(
    state,
    (async () => {
      const release = await state.lock.acquire(retry_later_at);
      try {
        const result = await host.persist(
          state,
          () =>
            host.options.store.commit_backend_event<JsonObject>((tx) => {
              const facts = read_facts(tx, turn.conversation_id);
              const attempt = facts.find(
                (fact) =>
                  fact.kind === K.attempt &&
                  fact.identity === answer_id &&
                  fact.turn_number === turn.turn_number &&
                  [O.permission_answer, O.user_input_answer].includes(
                    fact.operation as typeof O.permission_answer,
                  ),
              );
              if (
                attempt === undefined ||
                attempt.status !== S.accepted ||
                attempt.provider_identity !== provider_id ||
                attempt.operation !== `${kind}_answer`
              )
                throw new TypeError('answer application lacks an accepted original answer');
              const rows = host.options.store.read_answer_events_in_transaction(
                tx,
                turn.conversation_id,
                answer_id,
              );
              if (
                rows.some(
                  (row) =>
                    row.payload instanceof AnswerAppliedEventPayload &&
                    row.payload.answer_id === answer_id,
                )
              )
                return { result: { recorded: true }, written: [] };
              const choice = rows
                .map((row) => row.payload)
                .find(
                  (payload) =>
                    (payload instanceof PermissionAnsweredEventPayload ||
                      payload instanceof UserInputAnsweredEventPayload) &&
                    payload.answer_id === answer_id &&
                    payload.fate === AnswerFate.saved,
                );
              if (!(
                choice instanceof PermissionAnsweredEventPayload ||
                choice instanceof UserInputAnsweredEventPayload
              ))
                throw new TypeError('answer application lacks its saved choice');
              const request_id =
                choice instanceof PermissionAnsweredEventPayload
                  ? choice.ask_id
                  : choice.request_id;
              const request = facts.find(
                (fact) => fact.kind === K.request && fact.identity === request_id,
              );
              const written = host.options.store.append_in_transaction(
                tx,
                turn.conversation_id,
                [new AnswerAppliedEventPayload({ answer_id })],
                {
                  lifecycle:
                    request?.status === S.answering ? [{ ...request, status: S.answered }] : [],
                },
              );
              return { result: { recorded: true }, written };
            }, in_commit),
          [],
          retry_later_at,
        );
        if (result.taken) host.note_written(state, result.written);
        return result;
      } finally {
        release();
      }
    })(),
  );
}
export async function fence_replaced_remote_child(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
  retry_later_at?: number,
): Promise<void> {
  const state = host.conversation_state(reference.conversation_id);
  if (state === null) return;
  const lease = await acquire_after_steers(state, retry_later_at);
  try {
    if (same_reference(state.retained_child_reference, reference)) {
      state.child = null;
      state.child_is_quarantined = true;
    }
  } finally {
    lease.release();
  }
}
export function schedule_child_retirement(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  child: BackendChild,
  wait_for_reservation = true,
): Promise<boolean> {
  const reference = state.retained_child_reference;
  if (reference === null) throw new TypeError('Retirement lacks retained identity');
  if (state.retirement_task !== null) {
    if (state.retiring_child !== child)
      throw new TypeError('Pending retirement belongs to another child');
    return state.retirement_task;
  }
  state.child_is_quarantined = true;
  const token =
    state.reserved_turn?.token ?? state.running_turn?.token ?? state.last_ended_turn_token;
  const dependency =
    wait_for_reservation &&
    !(state.phase === 'idle' && state.delivery_uncertain) &&
    same_turn(state.reserved_turn?.token ?? null, token)
      ? state.reserved_turn
      : null;
  let task!: Promise<boolean>;
  task = own_settlement(
    state,
    (async () => {
      if (dependency !== null) await dependency.resolved.promise;
      try {
        await child.stop();
      } catch (error) {
        console.error('Remote child termination remains unconfirmed', error);
        return false;
      }
      const retired = await state.lock.run(async () => {
        const retained = retained_run(host, state);
        if (
          retained === null ||
          !same_reference(retained.reference, reference) ||
          !retained.retirement_confirmed
        )
          return false;
        await host.persist(state, () =>
          host.start_admission.retire_remote(
            host.options.store.start_admission_database,
            reference,
          ),
        );
        await host.persist(state, () =>
          host.start_admission.retire_source(host.options.store.start_admission_database, child),
        );
        await child.detach();
        if (state.child === child) {
          state.child = null;
          state.child_event_sink = null;
        }
        await background_tasks_ended_with_child(host, state);
        if (same_reference(state.retained_child_reference, reference)) {
          state.retained_child_reference = null;
          state.child_is_quarantined = false;
          const pending = state.reserved_turn;
          if (pending !== null && state.delivery_uncertain && same_turn(pending.token, token)) {
            state.last_ended_turn_token = pending.token;
            state.reserved_turn = null;
            pending.resolved.resolve();
          }
        }
        return true;
      });
      if (retired && state.held_prompts.length && !host.shutting_down) host.schedule_drain(state);
      return retired;
    })(),
  );
  state.retirement_task = task;
  state.retiring_child = child;
  void task
    .finally(() => {
      if (state.retirement_task === task) {
        state.retirement_task = null;
        state.retiring_child = null;
      }
    })
    .catch(() => {});
  return task;
}
function recovery(host: SqliteProcessConversationSystem) {
  if (host.options.remote_recovery === undefined)
    throw new TypeError('Remote interruption recovery is not bound');
  return host.options.remote_recovery;
}
async function confirm_termination(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reference: ChildReference,
  replaced_by?: string,
): Promise<boolean> {
  const lease = await host.acquire_settled(state);
  try {
    if (!same_reference(state.retained_child_reference, reference)) return false;
    const retained = retained_run(host, state);
    if (
      retained === null ||
      !same_reference(retained.reference, reference) ||
      (replaced_by === undefined && !retained.retirement_confirmed)
    )
      throw new TypeError('Remote child termination was not committed');
    if (replaced_by !== undefined) await recovery(host).retire_replaced(reference, replaced_by);
    await host.persist(state, () =>
      host.start_admission.retire_remote(host.options.store.start_admission_database, reference),
    );
    if (state.running_turn !== null)
      await host.end_turn(state, state.running_turn, 'interrupted', null);
    if (state.child?.stop_outside_lock) await state.child.detach();
    state.child = null;
    state.child_event_sink = null;
    state.retained_child_reference = null;
    state.child_is_quarantined = false;
    await background_tasks_ended_with_child(host, state);
    if (state.reserved_turn !== null) {
      state.last_ended_turn_token = state.reserved_turn.token;
      state.reserved_turn.resolved.resolve();
      state.reserved_turn = null;
    }
    set_phase(state, state.running_turn === null ? 'idle' : 'running');
    return true;
  } finally {
    lease.release();
  }
}
export async function confirm_remote_child_termination(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
): Promise<void> {
  const state = host.conversation_state(reference.conversation_id);
  if (state !== null) await own_settlement(state, confirm_termination(host, state, reference));
}
async function interrupt_rewritten(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  running: RunningTurn,
): Promise<void> {
  const interrupted = await state.lock.run(async () => {
    if (host.shutting_down || state.running_turn !== running || running.ended) return false;
    await host.end_turn(state, running, 'interrupted', null);
    set_phase(state, state.running_turn === null ? 'idle' : 'running');
    return true;
  });
  if (!interrupted) {
    if (!host.shutting_down)
      await recovery(host).close_rewritten(
        state.record.conversation_id,
        running.token.turn_number,
        'The turn ended before its message was written again.',
      );
    return;
  }
  await recovery(host).recover(state.record.conversation_id);
  await host.drain_held_prompts(state);
}
async function deliver_rewritten(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  running: RunningTurn,
  prompt: UndeliveredTurnPrompt,
): Promise<void> {
  try {
    await write_replayed_prompt(host, state, running.token, prompt, () => {
      if (host.shutting_down || state.running_turn !== running || running.ended)
        throw new DeliveryCancelled();
    });
  } catch (error) {
    if (host.shutting_down && is_shutdown_business_cancellation(error)) return;
    if (error instanceof DeliveryCancelled) {
      if (!host.shutting_down)
        await recovery(host).close_rewritten(
          state.record.conversation_id,
          prompt.turn_number,
          'The turn ended before its message was written again.',
        );
      return;
    }
    console.error(
      `conversation ${state.record.conversation_id}: a fresh Mac child did not take a prompt that never ran`,
      error,
    );
    await interrupt_rewritten(host, state, running);
    return;
  }
  await recovery(host).close_rewritten(
    state.record.conversation_id,
    prompt.turn_number,
    'Its message was written again to a fresh agent.',
  );
}
export async function resume_undelivered_prompt(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
  prompt: UndeliveredTurnPrompt,
  start_needed: boolean,
): Promise<void> {
  const state = host.conversation_state(reference.conversation_id);
  if (state === null) return;
  const lease = await host.acquire_settled(state);
  let running: RunningTurn, child: BackendChild;
  try {
    if (
      !same_reference(state.retained_child_reference, reference) ||
      state.running_turn === null ||
      state.running_turn.ended ||
      state.child === null ||
      state.running_turn.token.turn_number !== prompt.turn_number
    )
      return;
    running = state.running_turn;
    child = state.child;
    running.callbacks_revoked = false;
  } finally {
    lease.release();
  }
  await own_settlement(
    state,
    (async () => {
      if (start_needed) {
        const previous = state.record.resolved_start();
        const { ResolvedConversationStart } = await import('../contracts.ts');
        try {
          await host.native_operation(() =>
            child.start(
              new ResolvedConversationStart({
                ...previous,
                model: prompt.model_change ?? previous.model,
                reasoning_effort:
                  prompt.reasoning_effort_change === null && !prompt.reasoning_effort_is_set
                    ? previous.reasoning_effort
                    : prompt.reasoning_effort_change,
              }),
              { vendor_session_cursor: state.record.vendor_session_cursor },
            ),
          );
        } catch (error) {
          if (host.shutting_down && is_shutdown_business_cancellation(error)) return;
          log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
          await interrupt_rewritten(host, state, running);
          return;
        }
      }
      await deliver_rewritten(host, state, running, prompt);
    })(),
  );
}
export async function retire_replaced_remote_child(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
  replaced_by: string,
  prompt?: UndeliveredTurnPrompt | null,
): Promise<void> {
  const state = host.conversation_state(reference.conversation_id);
  if (state === null) return;
  if (prompt != null) {
    const rewritten = await own_settlement(
      state,
      (async () => {
        const lease = await host.acquire_settled(state);
        let running: RunningTurn;
        try {
          if (
            !same_reference(state.retained_child_reference, reference) ||
            state.running_turn === null ||
            state.running_turn.ended ||
            state.running_turn.ending_is_the_cores ||
            state.running_turn.token.turn_number !== prompt.turn_number
          )
            return false;
          running = state.running_turn;
          await recovery(host).retire_replaced(
            reference,
            replaced_by,
            message_content_text(prompt.sender_content) +
              (prompt.sender_content.some((piece) => !(piece instanceof MessageText))
                ? ' (It also carried attachments, which are not repeated here.)'
                : ''),
          );
          if (state.child?.stop_outside_lock) await state.child.detach();
          state.child = null;
          state.child_event_sink = null;
          state.retained_child_reference = null;
          state.child_is_quarantined = false;
          running.callbacks_revoked = false;
        } finally {
          lease.release();
        }
        await deliver_rewritten(host, state, running, prompt);
        return true;
      })(),
    );
    if (rewritten) return;
  }
  if (!(await own_settlement(state, confirm_termination(host, state, reference, replaced_by))))
    return;
  await recovery(host).recover(reference.conversation_id);
  await host.drain_held_prompts(state);
}
export async function stop_interrupted_remote_child(
  host: SqliteProcessConversationSystem,
  reference: ChildReference,
  child: BackendChild,
): Promise<void> {
  const state = host.conversation_state(reference.conversation_id);
  if (state === null) throw new TypeError('remote inventory names an unknown conversation');
  await state.lock.run(() => {
    if (
      !same_reference(state.retained_child_reference, reference) ||
      !state.child_is_quarantined ||
      state.last_ended_turn_token === null
    )
      throw new TypeError('remote inventory lacks an exact interrupted child');
    if (state.retirement_task !== null) {
      if (
        !same_reference(
          (state.retiring_child as BackendChild & { reference?: ChildReference })?.reference ??
            null,
          reference,
        )
      )
        throw new TypeError('Pending retirement belongs to another child');
      return;
    }
    if (!child.stop_outside_lock)
      throw new TypeError('Retained child must allow callbacks during stop');
    state.child = child;
    schedule_child_retirement(host, state, child);
  });
}

/** Kill can withdraw a remote reservation without waiting for its lost native reply. */
export async function kill_initial_delivery(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  discard: () => Promise<void>,
): Promise<boolean> {
  return state.lock.run(async () => {
    const reservation = state.reserved_turn;
    if (reservation === null && state.running_turn !== null) return false;
    const factory = retained_factory(child_factory_for(host, state.record.backend_key));
    let child = state.child;
    if (reservation === null) {
      if (!state.child_is_quarantined || state.retained_child_reference === null) return false;
      child ??= factory?.recover(state.retained_child_reference) ?? null;
      state.child = child;
      await discard();
      if (child?.stop_outside_lock) schedule_child_retirement(host, state, child);
      return true;
    }
    if (reservation.shutdown_owned && reservation.shutdown_fate != null) {
      await discard();
      if (state.running_turn !== null)
        await host.end_turn(state, state.running_turn, 'interrupted', null);
      return true;
    }
    if (child !== null && !child.stop_outside_lock) return false;
    if (child === null) {
      if (factory === null) return false;
      if (state.retained_child_reference !== null) {
        child = factory.recover(state.retained_child_reference);
        state.child = child;
      }
    }
    if (!reservation.shutdown_owned && child?.stop_outside_lock && reservation.prompt_accepted)
      return false;
    reservation.shutdown_owned = true;
    let suppressed = child === null || (await child.withdraw_delivery());
    const previous = host.options.store
      .lifecycle_facts(state.record.conversation_id)
      .find(
        (fact) =>
          fact.kind === K.attempt && fact.identity === `prompt:${reservation.token.turn_number}`,
      );
    if (previous?.status === S.suppressed) suppressed = true;
    state.child_is_quarantined = child !== null;
    state.delivery_uncertain = !suppressed;
    set_phase(state, 'ending');
    await discard();
    const { discarded_row, uncertain_row } = await import('./messages.ts');
    const payloads = reservation.messages
      .filter(
        (message) =>
          message.sender_message_id === null ||
          host.options.store.sender_message_outcome(
            state.record.conversation_id,
            message.sender_message_id,
          ) === null,
      )
      .map((message) =>
        suppressed
          ? discarded_row(message, 'conversation_killed')
          : uncertain_row(message, reservation.mode),
      );
    host.note_written(
      state,
      await host.persist(state, () =>
        host.options.store.append_events(state.record.conversation_id, payloads, {
          lifecycle:
            state.running_turn === null
              ? [
                  {
                    kind: K.turn,
                    identity: String(reservation.token.turn_number),
                    turn_number: reservation.token.turn_number,
                    operation: O.turn,
                    status: S.unavailable,
                    provider_identity: null,
                  },
                  {
                    kind: K.attempt,
                    identity: `prompt:${reservation.token.turn_number}`,
                    turn_number: reservation.token.turn_number,
                    operation: O.prompt,
                    status: suppressed ? S.suppressed : S.unknown,
                    provider_identity: null,
                  },
                ]
              : [],
        }),
      ),
    );
    if (state.running_turn !== null)
      await host.end_turn(state, state.running_turn, 'interrupted', null);
    const { PromptDeliverySuppressed, PromptDeliveryUncertain } = await import('../contracts.ts');
    reservation.shutdown_fate = suppressed
      ? new PromptDeliverySuppressed({ reason: 'conversation_killed' })
      : new PromptDeliveryUncertain();
    state.last_ended_turn_token = reservation.token;
    state.running_turn = null;
    state.reserved_turn = null;
    set_phase(state, 'idle');
    reservation.resolved.resolve();
    if (child?.stop_outside_lock) schedule_child_retirement(host, state, child);
    return true;
  });
}

export function take_wire_backend_event(
  host: SqliteProcessConversationSystem,
  turn: WireValue<'TurnToken'> | null,
  event: BackendEventValue,
  options: {
    in_commit?: RecordCommitParticipant<WireValue<'JsonObject'>>;
    conversation_id?: string;
    retry_later_at?: number;
    expected_sink?: BackendEventSink;
  } = {},
): Promise<AcceptedRecordEvent<WireValue<'JsonObject'>>> {
  host.require_runtime_ownership();
  const token = turn === null ? null : admit_remote_turn(turn);
  if (
    token !== null &&
    options.conversation_id !== undefined &&
    options.conversation_id !== token.conversation_id
  )
    throw new TypeError('event conversation conflicts with turn');
  const id = token?.conversation_id ?? options.conversation_id;
  if (id === undefined) throw new TypeError('a session event must name its conversation');
  const state = host.conversation_state(id);
  if (state === null) throw new TypeError('event names an unknown conversation');
  return own_settlement(state, take_backend_event_with_receipt(host, state, token, event, options));
}
