/** Python's shared principal and rejection contracts. */
import type { Principal as GeneratedPrincipal } from "../../web/src/lib/ticketWorkspace/contracts.ts";
export type Principal = GeneratedPrincipal;
export type PrincipalKind = Principal["kind"];
export interface RequestContext {
    principal: Principal;
    conversationId: string | null;
}
export const OWNER: Principal = { kind: "owner", id: "owner" };
export const CHIEF: Principal = { kind: "chief", id: "chief" };
export function legacyActor(principal: Principal): string {
    return principal.kind === "owner" ? "unattributed" : principal.kind === "ticket" ? "worker" : principal.kind;
}
export class PlannerError extends Error {
    readonly code: string;
    readonly detail: Record<string, unknown>;
    readonly statusCode: number;
    constructor(code: string, message: string, detail: Record<string, unknown> = {}) {
        super(message);
        this.name = "PlannerError";
        this.code = code;
        this.detail = detail;
        this.statusCode = ({ not_found: 404, diagnostic_expired: 410, stale_claim: 409, already_running: 409, gateway_offline: 503, skill_save_incomplete: 500, executor_version_refused: 409, executor_incarnation_not_current: 409 } as Record<string, number>)[code] ?? 400;
    }
    toPayload() { return { error: { code: this.code, message: this.message, detail: this.detail } }; }
}
