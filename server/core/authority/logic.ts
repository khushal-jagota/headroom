import type { Principal } from '../contracts.ts';
import type { Target } from './service.ts';
export interface ChainFacts {
  targetParentManagerId: string | null;
  callerParentManagerId: string | null;
  targetExists: boolean;
  /** Empty for ordinary callers. Retains the Python declaration decision seam. */
  callerDeclaredTargets?: readonly Target[];
}
export function isSelf(caller: Principal, target: Target, facts: ChainFacts): boolean {
  return (
    facts.targetExists &&
    (caller.kind === 'ticket' || caller.kind === 'manager') &&
    caller.kind === target.kind &&
    caller.id === target.id
  );
}
export function standsAbove(caller: Principal, target: Target, facts: ChainFacts): boolean {
  if (caller.kind === 'owner' || caller.kind === 'chief') return true;
  if (isSelf(caller, target, facts)) return false;
  if (caller.kind === 'manager')
    return (
      (target.kind === 'ticket' || target.kind === 'goal') &&
      facts.targetParentManagerId === caller.id
    );
  if (caller.kind === 'ticket')
    return (facts.callerDeclaredTargets ?? []).some((declared) => covers(declared, target));
  return false;
}

/** A field declaration reaches only a nonempty subset of its named fields. */
function covers(declared: Target, target: Target): boolean {
  if (declared.kind !== target.kind || (declared.id !== '*' && declared.id !== target.id))
    return false;
  if (!declared.fields?.length) return true;
  return (
    !!target.fields?.length && target.fields.every((field) => declared.fields!.includes(field))
  );
}
