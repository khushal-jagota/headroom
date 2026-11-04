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
export function recordKey(value: {kind:string; id:string}): string { return JSON.stringify([value.kind,value.id]); }
export function parseFence(raw: unknown): Fence { const value = parse<Fence>('Fence',raw); continuity(value); return value; }
