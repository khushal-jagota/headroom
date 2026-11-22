/** Conservative schema admission: optional additions never narrow an old message. */
import { isDeepStrictEqual } from 'node:util';
import { is_record } from '../../core/records.ts';

function additive(old: unknown, next: unknown): boolean {
  if (isDeepStrictEqual(old, next)) return true;
  if (!is_record(old) || !is_record(next)) return false;
  for (const [name, value] of Object.entries(old)) {
    if (!(name in next)) return false;
    if (name === 'properties' || name === '$defs') {
      if (!is_record(value) || !is_record(next[name])) return false;
      for (const [field, schema] of Object.entries(value))
        if (!(field in next[name]) || !additive(schema, next[name][field])) return false;
    } else if (!additive(value, next[name])) return false;
  }
  // A new constraint can reject old data. Only annotations and optional properties are additions.
  return Object.keys(next).every(
    (name) =>
      name in old || ['description', 'title', 'default', 'properties', '$defs'].includes(name),
  );
}
function definitions(schema: unknown): Record<string, unknown> {
  if (!is_record(schema) || !is_record(schema.$defs))
    throw new Error('Invalid executor wire schema');
  return schema.$defs;
}
const channel_roots = [
  'MacUpdateCheck',
  'MacUpdateReport',
  'MacRuntimeManifest',
  'ExecutorIdentity',
  'LeaseReply',
  'LeaseRefused',
] as const;
function channel_closure(schema: unknown): Record<string, unknown> {
  const defs = definitions(schema),
    kept: Record<string, unknown> = {};
  const visit = (name: string) => {
    if (name in kept) return;
    if (!(name in defs)) throw new Error('Missing update-channel definition: ' + name);
    let definition = defs[name];
    if (name === 'LeaseReply' || name === 'LeaseRefused') {
      if (
        !is_record(definition) ||
        !is_record(definition.properties) ||
        !('update' in definition.properties)
      )
        throw new Error('Lease envelope lost the machine update offer');
      definition = {
        ...definition,
        properties: { update: definition.properties.update },
        required:
          Array.isArray(definition.required) && definition.required.includes('update')
            ? ['update']
            : [],
      };
    }
    kept[name] = definition;
    const walk = (value: unknown): void => {
      if (Array.isArray(value)) value.forEach(walk);
      else if (is_record(value)) {
        if (typeof value.$ref === 'string') {
          if (!value.$ref.startsWith('#/$defs/'))
            throw new Error('External wire reference is unsupported');
          visit(value.$ref.slice(8));
        }
        Object.values(value).forEach(walk);
      }
    };
    walk(definition);
  };
  for (const name of channel_roots) visit(name);
  return kept;
}
export function check_machine_wire(
  previous: unknown,
  candidate: unknown,
  channel_version: number,
): void {
  if (channel_version !== 1) throw new Error('Machine update channel must remain version 1');
  if (
    !is_record(previous) ||
    !is_record(candidate) ||
    !Array.isArray(previous.roots) ||
    !Array.isArray(candidate.roots)
  )
    throw new Error('Invalid machine wire roots');
  for (const name of channel_roots)
    if (previous.roots.includes(name) && !candidate.roots.includes(name))
      throw new Error('Machine update channel root was removed: ' + name);
  if (!additive({ $defs: channel_closure(previous) }, { $defs: channel_closure(candidate) }))
    throw new Error('Machine update channel changed non-additively');
  if (
    !is_record(previous) ||
    !is_record(candidate) ||
    !is_record(previous.manifest) ||
    !is_record(candidate.manifest)
  )
    throw new Error('Invalid wire version manifest');
  const before = previous.manifest.protocol_version,
    after = candidate.manifest.protocol_version;
  if (
    !Number.isSafeInteger(before) ||
    !Number.isSafeInteger(after) ||
    Number(after) < Number(before)
  )
    throw new Error('Invalid machine protocol progression');
  if (
    after === before &&
    (!Array.isArray(previous.roots) ||
      !Array.isArray(candidate.roots) ||
      previous.roots.some((root) => !(candidate.roots as unknown[]).includes(root)))
  )
    throw new Error('Machine wire roots changed without a protocol increase');
  if (
    after === before &&
    !additive({ $defs: definitions(previous) }, { $defs: definitions(candidate) })
  )
    throw new Error('Non-additive machine wire change requires a protocol increase');
}
