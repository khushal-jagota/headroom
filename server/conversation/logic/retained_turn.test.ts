/** Port of test_retained_turn_logic.py: seven functions, eight collected cases.
 * Risk: restart/adoption disagree about asks, unresolved attempts, or the retained turn.
 * SQL comparison cannot prove adopted in-memory callback and provider-ID state.
 */
import { expect, it } from 'vitest';
import { UserInputQuestion } from '../events.ts';
import { adopted_running_turn, facts_after_restart, kept_turn_number } from './retained_turn.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../record/lifecycle.ts';
import type {
  LifecycleFact,
  LifecycleKind,
  LifecycleOperation,
  LifecycleStatus,
} from '../record/lifecycle.ts';
import type { OpenRunTurn } from '../record/contracts.ts';

const TOKEN = { conversation_id: 'c', turn_number: 2 };
function fact(
  kind: LifecycleKind,
  identity: string,
  turn_number: number,
  operation: LifecycleOperation,
  status: LifecycleStatus,
  provider_identity: string | null = null,
): LifecycleFact {
  return { kind, identity, turn_number, operation, status, provider_identity };
}
const TURN = fact(K.turn, '2', 2, O.turn, S.running);
const ASK = fact(K.request, '2:permission:a', 2, O.permission, S.pending, 'a');
const ANSWERING = fact(K.request, '2:permission:b', 2, O.permission, S.answering, 'b');
const INPUT = fact(K.request, '2:user_input:q', 2, O.user_input, S.pending, 'q');
const STEER = fact(K.attempt, 'steer:2:x', 2, O.steer, S.intent);
const RESERVED = fact(K.turn, '3', 3, O.turn, S.intent);
const RESERVED_PROMPT = fact(K.attempt, 'prompt:3', 3, O.prompt, S.intent);
const PROMPT = fact(K.attempt, 'prompt:2', 2, O.prompt, S.intent);
const EARLIER = fact(K.turn, '1', 1, O.turn, S.completed);
const CONVERSATION = [EARLIER, TURN, PROMPT, ASK, ANSWERING, STEER, RESERVED, RESERVED_PROMPT];
function after(
  facts: readonly LifecycleFact[],
  open_run: OpenRunTurn | null,
): Record<string, LifecycleStatus> {
  return Object.fromEntries(
    facts_after_restart(facts, open_run).map((value) => [value.identity, value.status]),
  );
}

it('a restart keeps the current process turn and its asks', () => {
  expect(after(CONVERSATION, { turn_number: 2, executor_process_current: true })).toEqual({
    'prompt:2': S.started,
    'steer:2:x': S.unknown,
    '3': S.unavailable,
    'prompt:3': S.suppressed,
  });
});
it('a replaced process keeps its turn for the drain but not its asks', () => {
  const result = after(CONVERSATION, { turn_number: 2, executor_process_current: false });
  expect(result).not.toHaveProperty('2');
  expect(result['prompt:2']).toBe(S.unknown);
  expect(result['2:permission:a']).toBe(S.unavailable);
  expect(result['2:permission:b']).toBe(S.unavailable);
});
it.each([
  [
    { ...TURN, status: S.intent },
    { ...PROMPT, status: S.unknown },
  ],
  [RESERVED, RESERVED_PROMPT],
])('a turn the Mac is not on ends as any other (%#)', (...facts: LifecycleFact[]) => {
  const open_run = { turn_number: 2, executor_process_current: true };
  expect(kept_turn_number(facts, open_run)).toBeNull();
  const turn = facts.find((value) => value.kind === K.turn)!;
  expect(after(facts, open_run)[turn.identity]).toBe(S.unavailable);
});
it('without an open run every live fact is reconciled', () => {
  expect(after(CONVERSATION, null)).toEqual({
    '2': S.unavailable,
    'prompt:2': S.unknown,
    '2:permission:a': S.unavailable,
    '2:permission:b': S.unavailable,
    'steer:2:x': S.unknown,
    '3': S.unavailable,
    'prompt:3': S.unknown,
  });
});
it('the adopted turn comes back with its open asks', () => {
  const questions = [
    new UserInputQuestion({
      question_id: 'scope',
      header: 'Scope',
      question: 'Which parts?',
      options: [],
      multi_select: false,
      allow_other: true,
    }),
  ];
  const running = adopted_running_turn(
    TOKEN,
    [TURN, ASK, ANSWERING, INPUT, STEER],
    new Map([['2:user_input:q', questions]]),
    { executor_process_current: true },
  );
  expect(running).not.toBeNull();
  expect(running!.token).toEqual(TOKEN);
  expect(running!.pending_permission_ask_ids).toEqual(new Set(['2:permission:a']));
  expect(running!.permission_provider_ids).toEqual(
    new Map([
      ['2:permission:a', 'a'],
      ['2:permission:b', 'b'],
    ]),
  );
  expect(running!.pending_user_input_questions).toEqual(new Map([['2:user_input:q', questions]]));
  expect(running!.user_input_provider_ids).toEqual(new Map([['2:user_input:q', 'q']]));
  expect(running!.callbacks_revoked).toBe(false);
});
it('a turn nobody can answer takes no new asks', () => {
  const cancel = fact(K.attempt, 'cancel-1', 2, O.cancel, S.unknown);
  for (const [facts, current] of [
    [[TURN, ASK], false],
    [[TURN, ASK, cancel], true],
  ] as const) {
    const running = adopted_running_turn(TOKEN, facts, new Map(), {
      executor_process_current: current,
    });
    expect(running).not.toBeNull();
    expect(running!.callbacks_revoked).toBe(true);
    expect(running!.pending_permission_ask_ids).toEqual(new Set());
  }
});
it('an ended turn is not adopted as running', () => {
  const ended = fact(K.turn, '2', 2, O.turn, S.completed);
  expect(
    adopted_running_turn(TOKEN, [ended, ASK], new Map(), { executor_process_current: true }),
  ).toBeNull();
  expect(adopted_running_turn(TOKEN, [], new Map(), { executor_process_current: true })).toBeNull();
});
