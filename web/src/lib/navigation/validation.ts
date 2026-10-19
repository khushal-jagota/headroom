import { wireDescriptors, SCHEMA_VERSION, SUPPORTED_SEMANTICS, WIRE_VERSION } from './contracts';
import type { SnapshotResponse, RevisionBatch, Fence, Cursor, Continuity, Reset, BatchEnvelope, CurrentAuthority, DegradedAuthority, NavigationRecord } from './contracts';

export class NavigationProtocolError extends Error {}
type Descriptor = {ref?: string; primitive?: string; literal?: unknown[]; array?: Descriptor; union?: Descriptor[]; object?: Record<string, Descriptor>};
function matchesCandidate(value: unknown, descriptor: Descriptor): boolean {
  if (descriptor.ref) return matchesCandidate(value, wireDescriptors[descriptor.ref] as Descriptor);
  if (descriptor.literal && !descriptor.literal.includes(value)) return false;
  if (descriptor.primitive) {
    const type = descriptor.primitive;
    if (!(type === 'null' ? value === null : type === 'integer' ? typeof value === 'number' && Number.isSafeInteger(value) : typeof value === type)) return false;
  }
  if (descriptor.object) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    for (const key in descriptor.object) {
      const field = descriptor.object[key];
      if (field.literal && !field.literal.includes(record[key])) return false;
    }
  }
  return true;
}
export function validate(value: unknown, descriptor: Descriptor, path: string): void {
  if (descriptor.ref) return validate(value, wireDescriptors[descriptor.ref] as Descriptor, path);
  if (descriptor.union) {
    for (const candidate of descriptor.union) {
      if (!matchesCandidate(value, candidate)) continue;
      try { validate(value, candidate, path); return; } catch (error) { if (!(error instanceof NavigationProtocolError)) throw error; }
    }
    throw new NavigationProtocolError(`${path}: invalid union`);
  }
  if (descriptor.literal && !descriptor.literal.includes(value)) throw new NavigationProtocolError(`${path}: invalid literal`);
  if (descriptor.primitive) {
    const type = descriptor.primitive;
    const valid = type === 'null' ? value === null : type === 'integer' ? typeof value === 'number' && Number.isSafeInteger(value) : typeof value === type;
    if (!valid) throw new NavigationProtocolError(`${path}: expected ${type}`);
  }
  if (descriptor.array) {
    if (!Array.isArray(value)) throw new NavigationProtocolError(`${path}: expected array`);
    value.forEach((item, index) => validate(item, descriptor.array!, `${path}[${index}]`));
  }
  if (descriptor.object) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new NavigationProtocolError(`${path}: expected object`);
    const record = value as Record<string, unknown>;
    if (Object.keys(record).length !== Object.keys(descriptor.object).length) throw new NavigationProtocolError(`${path}: unexpected fields`);
    for (const [key, field] of Object.entries(descriptor.object)) {
      if (!Object.hasOwn(record, key)) throw new NavigationProtocolError(`${path}: missing ${key}`);
      validate(record[key], field, `${path}.${key}`);
    }
  }
}
function parse<T>(name: string, value: unknown): T { validate(value, wireDescriptors[name] as Descriptor, name); return structuredClone(value) as T; }
export function parseRevision(value: string): bigint {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) throw new NavigationProtocolError('Invalid revision');
  return BigInt(value);
}
export function compareRevision(left: string, right: string): number { const a = parseRevision(left), b = parseRevision(right); return a < b ? -1 : a > b ? 1 : 0; }
export function continuity(value: Omit<Continuity, "semantics"> & {semantics:string}): void {
  if (value.schema_version !== SCHEMA_VERSION || value.semantics !== SUPPORTED_SEMANTICS || !value.scope || !value.epoch) throw new NavigationProtocolError('Unsupported continuity');
  parseRevision(value.revision);
}
export function encodeCursor(value: Continuity): string {
  const text = JSON.stringify({epoch:value.epoch, revision:value.revision, schema_version:value.schema_version, scope:value.scope, semantics:value.semantics, wire_version:WIRE_VERSION});
  return btoa(String.fromCharCode(...new TextEncoder().encode(text))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/, '');
}
export function decodeCursor(raw: string): Cursor {
  try {
    if (!raw || raw.length > 4096 || !/^[A-Za-z0-9_-]+$/.test(raw)) throw new Error();
    const text = new TextDecoder('utf-8', {fatal:true}).decode(Uint8Array.from(atob(raw.replaceAll('-','+').replaceAll('_','/') + '='.repeat((4-raw.length%4)%4)), c => c.charCodeAt(0)));
    const value = parse<Cursor>('Cursor', JSON.parse(text)); continuity(value);
    if (encodeCursor(value) !== raw) throw new Error();
    return value;
  } catch { throw new NavigationProtocolError('Invalid cursor'); }
}
export function assertCursorMatches(cursor: string, value: Continuity): void {
  const decoded = decodeCursor(cursor);
  for (const key of ['scope','schema_version','semantics','epoch','revision'] as const) if (decoded[key] !== value[key]) throw new NavigationProtocolError('Cursor mismatch');
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
export function authority(value: CurrentAuthority | DegradedAuthority): void {
  if (!value.delivery_session_id) throw new NavigationProtocolError('Empty delivery session');
  parseRevision(value.availability.generation);
  if (value.permit !== null) {
    for (const name of ['scope','runtime_epoch','installation_id'] as const) {
      if (!value.permit[name]) throw new NavigationProtocolError('Empty Signal permit identity');
    }
    parseRevision(value.permit.generation);
    if (value.permit.generation !== value.availability.generation) throw new NavigationProtocolError('Authority generation mismatch');
  }
}
function attentionProfile(records: NavigationRecord[], mode: SnapshotResponse['mode']): void {
  for (const record of records) {
    if (!record.id) throw new NavigationProtocolError('Empty record identity');
    if (record.kind !== 'conversation') continue;
    const knowledge = record.value.attention;
    if (mode === 'current') {
      if (knowledge.current === null
        || knowledge.current.signal.message_for_owner !== knowledge.durable.message_for_owner
        || knowledge.current.last_turn_failed !== knowledge.durable.last_turn_failed) {
        throw new NavigationProtocolError('Incomplete or contradictory current attention');
      }
    } else if (knowledge.current !== null) throw new NavigationProtocolError('Runtime attention in degraded view');
  }
}
export function parseSnapshotResponse(raw: unknown): SnapshotResponse {
  const value = parse<SnapshotResponse>('SnapshotResponse', raw);
  continuity(value.snapshot); authority(value.authority);
  if (value.mode === 'current') {
    assertCursorMatches(value.cursor,value.snapshot);
  }
  uniqueKeys(value.snapshot.records); attentionProfile(value.snapshot.records,value.mode);
  return value;
}
export function parseBatchEnvelope(raw: unknown): BatchEnvelope {
  const value = parse<BatchEnvelope>('BatchEnvelope',raw);
  authority(value.authority);
  value.batch = parseRevisionBatch(value.batch);
  attentionProfile(value.batch.upserts,'current');
  return value;
}
export function recordKey(value: {kind:string; id:string}): string { return JSON.stringify([value.kind,value.id]); }
function uniqueKeys(records: {kind:string;id:string}[]): void { if (new Set(records.map(recordKey)).size !== records.length) throw new NavigationProtocolError('Duplicate record key'); }
export function parseRevisionBatch(raw: unknown): RevisionBatch {
  const value = parse<RevisionBatch>('RevisionBatch', raw); continuity(value); parseRevision(value.base_revision);
  if (parseRevision(value.revision) !== parseRevision(value.base_revision) + 1n) throw new NavigationProtocolError('Nonadvancing batch');
  uniqueKeys([...value.upserts,...value.deletes]); return value;
}
export function parseFence(raw: unknown): Fence { const value = parse<Fence>('Fence',raw); continuity(value); return value; }
export function parseReset(raw: unknown): Reset { return parse<Reset>('Reset',raw); }
