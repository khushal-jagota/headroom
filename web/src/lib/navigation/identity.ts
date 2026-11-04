import { WIRE_VERSION } from './contracts';
import type { Continuity, CurrentAuthority, DegradedAuthority } from './contracts';

export function compareRevision(left: string, right: string): number { const a = BigInt(left), b = BigInt(right); return a < b ? -1 : a > b ? 1 : 0; }
export function encodeCursor(value: Continuity): string {
  const text = JSON.stringify({epoch:value.epoch, revision:value.revision, schema_version:value.schema_version, scope:value.scope, semantics:value.semantics, wire_version:WIRE_VERSION});
  return btoa(String.fromCharCode(...new TextEncoder().encode(text))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/, '');
}
export function sameAuthority(left: CurrentAuthority | DegradedAuthority, right: CurrentAuthority | DegradedAuthority): boolean {
  return left.delivery_session_id === right.delivery_session_id
    && left.availability.state === right.availability.state
    && left.availability.generation === right.availability.generation
    && left.availability.reason === right.availability.reason
    && (left.permit === null ? right.permit === null : right.permit !== null
      && left.permit.scope === right.permit.scope && left.permit.runtime_epoch === right.permit.runtime_epoch
      && left.permit.installation_id === right.permit.installation_id && left.permit.generation === right.permit.generation);
}
export function recordKey(value: {kind:string; id:string}): string { return JSON.stringify([value.kind,value.id]); }
