/** Reserve one live ask before native I/O, then save its exact application fate. */
import { randomUUID } from 'node:crypto';
import { PermissionAnswerWriteFailed, UserInputAnswerWriteFailed } from '../backends/contracts.ts';
import type { BackendChild, TurnToken } from '../backends/contracts.ts';
import { AnswerFate } from '../contracts.ts';
import { PermissionAnsweredEventPayload, UserInputAnsweredEventPayload } from '../events.ts';
import type { UserInputAnswer } from '../events.ts';
import { ordered_user_input_answers } from '../logic/user_input.ts';
import type { RunningTurn } from '../record/contracts.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../record/lifecycle.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import type { RuntimeMutation } from './contracts.ts';
import { field_mutation } from './save_attempt.ts';
import type { ConversationState } from './state.ts';
import { same_turn } from './state.ts';

interface PendingAnswer {
  readonly request_id: string;
  readonly provider_id: string;
  readonly request_operation: LifecycleFact['operation'];
  readonly answer_operation: LifecycleFact['operation'];
  readonly take: RuntimeMutation;
  restore(current: RunningTurn): RuntimeMutation;
  write(answer_id: string, token: TurnToken): Promise<AnswerFate>;
  write_failed(error: unknown): boolean;
  row(
    fate: AnswerFate,
    answer_id: string | null,
  ): PermissionAnsweredEventPayload | UserInputAnsweredEventPayload;
}
async function answer_request(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  select: (running: RunningTurn, child: BackendChild) => PendingAnswer | null,
): Promise<AnswerFate> {
  state.last_touched_monotonic = host.monotonic_now();
  const reserved = await state.lock.run(async () => {
    const running = state.running_turn;
    const child = state.child;
    if (!running || !child) return null;
    const pending = select(running, child);
    if (!pending) return null;
    const request_fact: LifecycleFact = {
      kind: K.request,
      identity: pending.request_id,
      turn_number: running.token.turn_number,
      operation: pending.request_operation,
      status: S.answering,
      provider_identity: pending.provider_id,
    };
    const attempt = await host.begin_attempt(
      state,
      running.token,
      pending.answer_operation,
      pending.provider_id,
      [request_fact],
      [pending.take],
    );
    return { pending, attempt, request_fact, token: running.token };
  });
  if (!reserved) return AnswerFate.refused;
  const { pending, attempt, request_fact, token } = reserved;
  let fate: AnswerFate;
  try {
    fate = await host.native_operation(() => pending.write(attempt.identity, token));
  } catch (error) {
    if (!pending.write_failed(error)) throw error;
    fate = AnswerFate.refused;
  }
  return state.lock.run(async () => {
    const running = state.running_turn;
    if (!running || !same_turn(running.token, token)) {
      if (fate === AnswerFate.saved) {
        host.note_written(
          state,
          await host.persist(state, () =>
            host.options.store.append_events(
              state.record.conversation_id,
              [pending.row(fate, attempt.identity)],
              { lifecycle: [{ ...attempt, status: S.accepted }] },
            ),
          ),
        );
        return fate;
      }
      await host.persist(state, () =>
        host.options.store.transition_lifecycle(state.record.conversation_id, [
          { ...attempt, status: fate === AnswerFate.refused ? S.refused : S.accepted },
        ]),
      );
      return AnswerFate.refused;
    }
    if (fate === AnswerFate.refused) {
      await host.persist(
        state,
        () =>
          host.options.store.transition_lifecycle(state.record.conversation_id, [
            { ...attempt, status: S.refused },
            { ...request_fact, status: S.pending },
          ]),
        [pending.restore(running)],
      );
      return fate;
    }
    host.note_written(
      state,
      await host.persist(state, () =>
        host.options.store.append_events(
          state.record.conversation_id,
          [pending.row(fate, fate === AnswerFate.saved ? attempt.identity : null)],
          {
            lifecycle: [
              { ...attempt, status: S.accepted },
              { ...request_fact, status: fate === AnswerFate.saved ? S.answering : S.answered },
            ],
          },
        ),
      ),
    );
    return fate;
  });
}
export function answer_permission_ask(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  ask_id: string,
  option_id: string,
): Promise<AnswerFate> {
  return answer_request(host, state, (running, child) => {
    if (!running.pending_permission_ask_ids.has(ask_id)) return null;
    const provider_id = running.permission_provider_ids.get(ask_id)!;
    const remaining = new Set(running.pending_permission_ask_ids);
    remaining.delete(ask_id);
    return {
      request_id: ask_id,
      provider_id,
      request_operation: O.permission,
      answer_operation: O.permission_answer,
      take: field_mutation(running, 'pending_permission_ask_ids', remaining),
      restore: (current) =>
        field_mutation(
          current,
          'pending_permission_ask_ids',
          new Set([...current.pending_permission_ask_ids, ask_id]),
        ),
      write: (answer_id, turn_token) =>
        child.answer_permission_ask(provider_id, option_id, { answer_id, turn_token }),
      write_failed: (error) => error instanceof PermissionAnswerWriteFailed,
      row: (fate, answer_id) =>
        new PermissionAnsweredEventPayload({ ask_id, option_id, fate, answer_id }),
    };
  });
}
export function answer_user_input(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  request_id: string,
  answers: readonly UserInputAnswer[],
): Promise<AnswerFate> {
  return answer_request(host, state, (running, child) => {
    const questions = running.pending_user_input_questions.get(request_id);
    if (!questions) return null;
    const ordered = ordered_user_input_answers(questions, answers);
    if (!ordered) return null;
    const provider_id = running.user_input_provider_ids.get(request_id)!;
    const remaining = new Map(running.pending_user_input_questions);
    remaining.delete(request_id);
    return {
      request_id,
      provider_id,
      request_operation: O.user_input,
      answer_operation: O.user_input_answer,
      take: field_mutation(running, 'pending_user_input_questions', remaining),
      restore: (current) =>
        field_mutation(
          current,
          'pending_user_input_questions',
          new Map([...current.pending_user_input_questions, [request_id, questions]]),
        ),
      write: (answer_id, turn_token) =>
        child.answer_user_input(provider_id, ordered, { answer_id, turn_token }),
      write_failed: (error) => error instanceof UserInputAnswerWriteFailed,
      row: (fate, answer_id) =>
        new UserInputAnsweredEventPayload({ request_id, answers: ordered, fate, answer_id }),
    };
  });
}
