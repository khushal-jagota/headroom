/** Turn attempts, request revocation, and turn endings. */
import { ConversationState, turn_is_maintenance } from './state.ts';
import type { TurnToken } from '../backends/contracts.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';
import type { RuntimeMutation } from './contracts.ts';
import { LifecycleKind as K, LifecycleStatus as S } from '../record/lifecycle.ts';
import { randomUUID } from 'node:crypto';
import type { RunningTurn, StoredConversationEvent } from '../record/contracts.ts';
import { interpret_backend_event } from '../logic/backend_events.ts';
import { TurnEnded } from '../backends/contracts.ts';
import { field_mutation } from './save_attempt.ts';
import { ConversationRecord } from '../record/contracts.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

export async function begin_attempt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken,
  operation: LifecycleFact['operation'],
  provider_identity: string | null = null,
  related: readonly LifecycleFact[] = [],
  mutations: readonly RuntimeMutation[] = [],
): Promise<LifecycleFact> {
  const attempt: LifecycleFact = {
    kind: K.attempt,
    identity: randomUUID().replaceAll('-', ''),
    turn_number: token.turn_number,
    operation,
    status: S.intent,
    provider_identity,
  };
  await host.persist(
    state,
    () =>
      host.options.store.transition_lifecycle(state.record.conversation_id, [attempt, ...related]),
    mutations,
  );
  return attempt;
}

export async function revoke_turn_requests(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  running: RunningTurn,
): Promise<void> {
  const facts = host.options.store
    .lifecycle_facts(state.record.conversation_id)
    .filter(
      (fact) =>
        fact.kind === K.request &&
        fact.turn_number === running.token.turn_number &&
        (fact.status === S.pending || fact.status === S.answering),
    )
    .map((fact) => ({ ...fact, status: S.unavailable }));
  await host.persist(state, () =>
    host.options.store.transition_lifecycle(state.record.conversation_id, facts),
  );
  running.callbacks_revoked = true;
}

export async function end_turn(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  running: RunningTurn,
  ending: import('../events.ts').ConversationTurnEnding,
  error: string | null,
  cancel_fact: LifecycleFact | null = null,
): Promise<StoredConversationEvent | null> {
  if (running.ended) return null;
  await host.revoke_turn_requests(state, running);
  const interpreted = interpret_backend_event(
    running.token,
    new TurnEnded({ ending, error_summary: error }),
    running,
    host.options.store.lifecycle_facts(state.record.conversation_id),
  );
  const written = await host.persist(
    state,
    () =>
      host.options.store.append_turn_ending(state.record.conversation_id, interpreted.payloads, {
        lifecycle: [
          interpreted.lifecycle[0],
          ...(cancel_fact ? [cancel_fact] : []),
          ...interpreted.lifecycle.slice(1),
        ],
        agent_activity: !turn_is_maintenance(running),
        automatic_compaction_confirmed: running.compaction_confirmed,
        working_turn_compaction_confirmed:
          running.compaction_confirmed && !running.automatic_compaction,
        automatic_compaction_result: interpreted.compaction_result,
        create_failure_notice: interpreted.failure_notice,
      }),
    [
      field_mutation(running, 'ended', true),
      field_mutation(running, 'pending_permission_ask_ids', new Set()),
      field_mutation(running, 'pending_user_input_questions', new Map()),
      field_mutation(running, 'permission_provider_ids', new Map()),
      field_mutation(running, 'user_input_provider_ids', new Map()),
      field_mutation(state, 'last_ended_turn_token', running.token),
      field_mutation(
        state,
        'last_ended_turn_was_automatic_compaction',
        running.automatic_compaction,
      ),
      field_mutation(state, 'running_turn', null),
    ],
  );
  host.note_written(state, written);
  if (written.length > 0) {
    const last = written[written.length - 1];
    const latest_agent_activity_sequence = turn_is_maintenance(running)
      ? state.record.latest_agent_activity_sequence
      : last.sequence;
    state.record = new ConversationRecord({
      ...state.record,
      latest_agent_activity_at: turn_is_maintenance(running)
        ? state.record.latest_agent_activity_at
        : last.created_at,
      latest_agent_activity_sequence,
      automatic_compaction_attempted_through_sequence:
        running.compaction_confirmed || interpreted.compaction_result !== null
          ? latest_agent_activity_sequence
          : state.record.automatic_compaction_attempted_through_sequence,
    });
  }
  if (!host.shutting_down) host.options.emit_change?.();
  return written[written.length - 1] ?? null;
}
