import { PlannerError, legacyActor } from "../contracts.ts";
import type { Principal } from "../contracts.ts";
/** The authority service needs reads only. It never acquires the writer. */
export type { ReadSql as AuthoritySql } from "../database/index.ts";
import type { ReadSql as AuthoritySql } from "../database/index.ts";
export type TargetKind = "ticket" | "manager" | "goal" | "plan" | "owner_only";
export interface Target {
    kind: TargetKind;
    id: string;
    fields?: readonly string[];
}
export type { ChainFacts } from "./logic.ts";
import type { ChainFacts } from "./logic.ts";
import { isSelf, standsAbove } from "./logic.ts";
export { isSelf, standsAbove } from "./logic.ts";
export function chainFacts(sql: AuthoritySql, caller: Principal, target: Target): ChainFacts {
    const parent = (id: string) => sql.get<{
        id: string;
    }>("SELECT item.id AS id FROM tickets AS ticket JOIN managers AS item ON item.id = ticket.manager_id WHERE ticket.id = ? AND ticket.manager_kind = 'manager'", [id])?.id ?? null;
    return {
        targetParentManagerId: target.kind === "ticket" ? parent(target.id) : target.kind === "goal" ? sql.get<{
            holder_id: string;
        }>("SELECT holder_id FROM goals WHERE id=? AND holder_kind='manager'", [target.id])?.holder_id ?? null : null,
        callerParentManagerId: caller.kind === "ticket" ? parent(caller.id) : null,
        targetExists: target.kind === "ticket" || target.kind === "manager" ? !!sql.get(`SELECT 1 FROM ${target.kind === "ticket" ? "tickets" : "managers"} WHERE id=?`, [target.id]) : false,
    };
}
export function refuse(caller: Principal, target: Target): never {
    throw new PlannerError("agent_forbidden", "this principal does not stand above what it asked to act on", { actor: legacyActor(caller), principal_kind: caller.kind, principal_id: caller.id, target_kind: target.kind, target_id: target.id, target_fields: [...(target.fields ?? [])].sort() });
}
export function isAbove(sql: AuthoritySql, caller: Principal, target: Target) { return standsAbove(caller, target, chainFacts(sql, caller, target)); }
export function requireAbove(sql: AuthoritySql, caller: Principal, target: Target): void { if (!isAbove(sql, caller, target))
    refuse(caller, target); }
export function requireSelf(sql: AuthoritySql, caller: Principal, target: Target): void { if (!isSelf(caller, target, chainFacts(sql, caller, target)))
    refuse(caller, target); }
export function requireAboveOrSelf(sql: AuthoritySql, caller: Principal, target: Target): void { const facts = chainFacts(sql, caller, target); if (!(standsAbove(caller, target, facts) || isSelf(caller, target, facts)))
    refuse(caller, target); }
export function requireInChain(sql: AuthoritySql, caller: Principal, target: Target): void {
    const facts = chainFacts(sql, caller, target);
    if (!(standsAbove(caller, target, facts) || isSelf(caller, target, facts) || (facts.targetExists && caller.kind === "ticket" && target.kind === "manager" && facts.callerParentManagerId === target.id)))
        refuse(caller, target);
}
export function refuseManagerReparenting(caller: Principal, ticketId: string): void { if (caller.kind !== "owner" && caller.kind !== "chief")
    throw new PlannerError("agent_forbidden", "only Khushal or the Chief can move a Ticket out of its own chain", { field: "manager_id", ticket_id: ticketId }); }
export function canHoldReview(sql: AuthoritySql, caller: Principal, ticketId: string): boolean {
    const row = sql.get<{
        manager_kind: string;
        manager_id: string | null;
    }>("SELECT manager_kind,manager_id FROM tickets WHERE id=?", [ticketId]);
    return !!row && (caller.kind === "owner" || (caller.kind === "chief" && row.manager_kind === "chief") || (caller.kind === "manager" && row.manager_kind === "manager" && caller.id === row.manager_id));
}
