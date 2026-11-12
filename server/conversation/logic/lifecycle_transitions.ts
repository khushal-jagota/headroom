/** Allowed knowledge transitions. Terminal facts cannot resurrect capability. */
import { LifecycleOperation as O, LifecycleStatus as S } from '../record/lifecycle.ts';
import type { LifecycleFact, LifecycleOperation, LifecycleStatus } from '../record/lifecycle.ts';

const operations: Record<LifecycleFact['kind'], ReadonlySet<LifecycleOperation>> = {
  turn: new Set([O.turn]),
  request: new Set([O.permission, O.user_input]),
  attempt: new Set([O.prompt, O.steer, O.permission_answer, O.user_input_answer, O.cancel]),
};
const statuses: Record<LifecycleFact['kind'], ReadonlySet<LifecycleStatus>> = {
  turn: new Set([S.intent, S.running, S.completed, S.failed, S.interrupted, S.unavailable]),
  request: new Set([S.pending, S.answering, S.answered, S.unavailable]),
  attempt: new Set([
    S.intent,
    S.started,
    S.injected,
    S.accepted,
    S.refused,
    S.suppressed,
    S.unknown,
  ]),
};
const transitions: Record<
  LifecycleFact['kind'],
  Partial<Record<LifecycleStatus, readonly LifecycleStatus[]>>
> = {
  turn: {
    intent: [S.running, S.unavailable],
    running: [S.completed, S.failed, S.interrupted, S.unavailable],
  },
  attempt: { intent: [S.started, S.injected, S.accepted, S.refused, S.suppressed, S.unknown] },
  request: {
    pending: [S.answering, S.answered, S.unavailable],
    answering: [S.pending, S.answered, S.unavailable],
  },
};

export function validate_fact(fact: LifecycleFact): void {
  if (!operations[fact.kind]?.has(fact.operation) || !statuses[fact.kind]?.has(fact.status)) {
    throw new Error('invalid lifecycle fact');
  }
  if (
    (fact.status === S.started && fact.operation !== O.prompt) ||
    (fact.status === S.injected && fact.operation !== O.steer) ||
    (fact.status === S.accepted && (fact.operation === O.prompt || fact.operation === O.steer))
  ) {
    throw new Error('a delivery outcome must name its exact admission boundary');
  }
}
export function validate_transition(previous: LifecycleFact, candidate: LifecycleFact): void {
  if (
    previous.kind !== candidate.kind ||
    previous.identity !== candidate.identity ||
    previous.turn_number !== candidate.turn_number ||
    previous.operation !== candidate.operation ||
    previous.provider_identity !== candidate.provider_identity
  ) {
    throw new Error('a lifecycle identity cannot change its meaning');
  }
  if (previous.status === candidate.status) return;
  if (!transitions[previous.kind]?.[previous.status]?.includes(candidate.status)) {
    throw new Error('a settled lifecycle fact cannot regress');
  }
}
