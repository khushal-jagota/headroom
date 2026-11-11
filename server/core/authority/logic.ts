import type { Principal } from "../contracts.ts";
import type { Target } from "./service.ts";
export interface ChainFacts {
    targetParentManagerId: string | null;
    callerParentManagerId: string | null;
    targetExists: boolean;
}
export function isSelf(caller: Principal, target: Target, facts: ChainFacts): boolean { return facts.targetExists && (caller.kind === "ticket" || caller.kind === "manager") && caller.kind === target.kind && caller.id === target.id; }
export function standsAbove(caller: Principal, target: Target, facts: ChainFacts): boolean {
    if (caller.kind === "owner" || caller.kind === "chief")
        return true;
    if (isSelf(caller, target, facts))
        return false;
    return caller.kind === "manager" && (target.kind === "ticket" || target.kind === "goal") && facts.targetParentManagerId === caller.id;
}
