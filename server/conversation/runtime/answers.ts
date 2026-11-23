/** Reserve one live ask before native I/O, then save its exact application fate. */
import { randomUUID } from 'node:crypto';
import { PermissionAnswerWriteFailed, UserInputAnswerWriteFailed } from '../backends/contracts.ts';
import type { BackendChild, TurnToken } from '../backends/contracts.ts';
import { log_backend_failure } from '../backends/failure_summary.ts';
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
type AnswerRow = PermissionAnsweredEventPayload | UserInputAnsweredEventPayload;
/** What an answer says, so a repeat with the same operation ID can be told from a different answer. */
const permission_content = (ask_id: string, option_id: string): string =>
  JSON.stringify(['permission', ask_id, option_id]);
const user_input_content = (request_id: string, answers: readonly UserInputAnswer[]): string =>
  JSON.stringify([
    'user_input',
    request_id,
    answers
      .map((answer) => [answer.question_id, answer.answers])
      .sort((a, b) => (a[0]! < b[0]! ? -1 : 1)),
  ]);
const row_content = (row: AnswerRow): string =>
  row.kind === 'permission_answered'
    ? permission_content(row.ask_id, row.option_id)
    : user_input_content(row.request_id, row.answers);
/**
 * With an operation ID, an answer happens once. A repeat returns the saved row's fate, and a repeat
 * that arrives while the first is still being written waits for it. A refusal saves no row, so a
 * repeat of a refused answer is judged again.
 */
async function answer_once(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  operation_id: string | null,
  content: string,
  answer: () => Promise<AnswerFate>,
): Promise<AnswerFate> {
  if (operation_id === null) return answer();
  const different = () => new Error('operation_id already names a different answer');
  const running = state.answers_in_flight.get(operation_id);
  if (running) {
    if (running.content !== content) throw different();
    return running.fate;
  }
  const saved = host.options.store.answer_operation_outcome(
    state.record.conversation_id,
    operation_id,
  );
  if (saved) {
    const row = saved.payload as AnswerRow;
    if (row_content(row) !== content) throw different();
    return row.fate;
  }
  const fate = answer();
  state.answers_in_flight.set(operation_id, { content, fate });
  try {
    return await fate;
  } finally {
    state.answers_in_flight.delete(operation_id);
  }
}
/** The saved fate of an answer by its operation ID, or null when none was saved. */
export function answer_receipt(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  operation_id: string,
): AnswerFate | null {
  const saved = host.options.store.answer_operation_outcome(conversation_id, operation_id);
  return saved ? (saved.payload as AnswerRow).fate : null;
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
    // The request returns to pending, and its answer row has no text, so the log keeps it.
    log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
    fate = AnswerFate.refused;
  }
  return state.lock.run(async () => {
    const running = state.running_turn;
    if (!running || !same_turn(running.token, token)) {
      if (fate === AnswerFate.refused) {
        await host.persist(state, () =>
          host.options.store.transition_lifecycle(state.record.conversation_id, [
            { ...attempt, status: S.refused },
          ]),
        );
        return fate;
      }
      // The backend took the answer before its turn ended, so the answer keeps its row and receipt.
      // Ending the turn already settled the request, so only the attempt is recorded here.
      host.note_written(
        state,
        await host.persist(state, () =>
          host.options.store.append_events(
            state.record.conversation_id,
            [pending.row(fate, fate === AnswerFate.saved ? attempt.identity : null)],
            { lifecycle: [{ ...attempt, status: S.accepted }] },
          ),
        ),
      );
      return fate;
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
  operation_id: string | null = null,
): Promise<AnswerFate> {
  return answer_once(host, state, operation_id, permission_content(ask_id, option_id), () =>
    answer_request(host, state, permission_selector(ask_id, option_id, operation_id)),
  );
}
function permission_selector(ask_id: string, option_id: string, operation_id: string | null) {
  return (running: RunningTurn, child: BackendChild): PendingAnswer | null => {
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
        new PermissionAnsweredEventPayload({ ask_id, option_id, fate, answer_id, operation_id }),
    };
  };
}
export function answer_user_input(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  request_id: string,
  answers: readonly UserInputAnswer[],
  operation_id: string | null = null,
): Promise<AnswerFate> {
  return answer_once(host, state, operation_id, user_input_content(request_id, answers), () =>
    answer_request(host, state, user_input_selector(request_id, answers, operation_id)),
  );
}
function user_input_selector(
  request_id: string,
  answers: readonly UserInputAnswer[],
  operation_id: string | null,
) {
  return (running: RunningTurn, child: BackendChild): PendingAnswer | null => {
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
        new UserInputAnsweredEventPayload({
          request_id,
          answers: ordered,
          fate,
          answer_id,
          operation_id,
        }),
    };
  };
}

export async function emit_answer(
  host: SqliteProcessConversationSystem,
  turn: TurnToken,
  emit: () => Promise<unknown>,
): Promise<void> {
  const state = host.conversation_state(turn.conversation_id);
  if (state !== null)
    await state.lock.run(async () => {
      if (
        same_turn(state.running_turn?.token ?? null, turn) &&
        !state.running_turn!.callbacks_revoked
      )
        await emit();
    });
}
