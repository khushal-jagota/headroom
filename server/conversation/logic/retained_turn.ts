/** What a VPS restart keeps of an independently hosted Mac turn. */
import type { TurnToken } from '../backends/contracts.ts';
import type { UserInputQuestion } from '../events.ts';
import type { OpenRunTurn, RunningTurn } from '../record/contracts.ts';
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

export const LIVE_FACT_STATUSES: ReadonlySet<LifecycleStatus> = new Set([
  S.intent,
  S.running,
  S.pending,
  S.answering,
]);

function fact(
  facts: readonly LifecycleFact[],
  kind: LifecycleKind,
  turn_number: number,
  operation: LifecycleOperation,
): LifecycleFact | undefined {
  return facts.find(
    (value) =>
      value.kind === kind &&
      value.turn_number === turn_number &&
      value.operation === operation &&
      (kind !== K.attempt || value.identity === `prompt:${turn_number}`),
  );
}
function being_cancelled(facts: readonly LifecycleFact[], turn_number: number): boolean {
  return facts.some(
    (value) =>
      value.kind === K.attempt && value.operation === O.cancel && value.turn_number === turn_number,
  );
}
export function kept_turn_number(
  facts: readonly LifecycleFact[],
  open_run: OpenRunTurn | null,
): number | null {
  if (open_run === null) return null;
  const number = open_run.turn_number;
  const turn = fact(facts, K.turn, number, O.turn);
  const prompt = fact(facts, K.attempt, number, O.prompt);
  if (turn === undefined) return null;
  if (turn.status === S.running) return number;
  if (
    turn.status === S.intent &&
    prompt !== undefined &&
    (prompt.status === S.intent || prompt.status === S.started)
  )
    return number;
  return null;
}
export function facts_after_restart(
  facts: readonly LifecycleFact[],
  open_run: OpenRunTurn | null,
): readonly LifecycleFact[] {
  const kept = kept_turn_number(facts, open_run);
  const asks_stay_open =
    open_run !== null &&
    open_run.executor_process_current &&
    kept !== null &&
    !being_cancelled(facts, kept);
  const settled: LifecycleFact[] = [];
  for (const value of facts) {
    if (!LIVE_FACT_STATUSES.has(value.status)) continue;
    if (open_run !== null && value.turn_number === kept) {
      if (value.kind === K.turn) continue;
      if (value.kind === K.request && asks_stay_open) continue;
      if (
        value.kind === K.attempt &&
        value.operation === O.prompt &&
        open_run.executor_process_current
      ) {
        settled.push({ ...value, status: S.started });
        continue;
      }
    }
    if (
      open_run !== null &&
      value.turn_number > open_run.turn_number &&
      value.kind === K.attempt &&
      value.operation === O.prompt
    ) {
      settled.push({ ...value, status: S.suppressed });
      continue;
    }
    settled.push({ ...value, status: value.kind === K.attempt ? S.unknown : S.unavailable });
  }
  return settled;
}
export function adopted_running_turn(
  token: TurnToken,
  facts: readonly LifecycleFact[],
  user_input_questions: ReadonlyMap<string, readonly UserInputQuestion[]>,
  options: { executor_process_current: boolean },
): RunningTurn | null {
  const turn = fact(facts, K.turn, token.turn_number, O.turn);
  if (turn === undefined || (turn.status !== S.intent && turn.status !== S.running)) return null;
  const running: RunningTurn = {
    token,
    automatic_compaction: false,
    compaction_confirmed: false,
    pending_permission_ask_ids: new Set(),
    permission_provider_ids: new Map(),
    user_input_provider_ids: new Map(),
    pending_user_input_questions: new Map(),
    automatic_notice: false,
    ended: false,
    ending_is_the_cores: false,
    callbacks_revoked:
      !options.executor_process_current || being_cancelled(facts, token.turn_number),
  };
  if (running.callbacks_revoked) return running;
  for (const value of facts) {
    if (
      value.kind !== K.request ||
      value.turn_number !== token.turn_number ||
      value.provider_identity === null ||
      (value.status !== S.pending && value.status !== S.answering)
    )
      continue;
    const pending = value.status === S.pending;
    if (value.operation === O.permission) {
      running.permission_provider_ids.set(value.identity, value.provider_identity);
      if (pending) running.pending_permission_ask_ids.add(value.identity);
    } else if (value.operation === O.user_input) {
      running.user_input_provider_ids.set(value.identity, value.provider_identity);
      const questions = user_input_questions.get(value.identity);
      if (pending && questions !== undefined)
        running.pending_user_input_questions.set(value.identity, questions);
    }
  }
  return running;
}
