import type { CurrentAuthority } from './contracts';

export function compareRevision(left: string, right: string): number { const a = BigInt(left), b = BigInt(right); return a < b ? -1 : a > b ? 1 : 0; }
export function sameAuthority(left: CurrentAuthority, right: CurrentAuthority): boolean {
  return left.delivery_session_id === right.delivery_session_id
    && left.availability.state === right.availability.state
    && left.availability.generation === right.availability.generation
    && left.availability.reason === right.availability.reason
    && left.permit.scope === right.permit.scope && left.permit.runtime_epoch === right.permit.runtime_epoch
    && left.permit.installation_id === right.permit.installation_id && left.permit.generation === right.permit.generation;
}
export function recordKey(value: {kind:string; id:string}): string { return JSON.stringify([value.kind,value.id]); }
