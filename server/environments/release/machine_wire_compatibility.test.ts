import { expect, test } from 'vitest';
import { check_machine_wire } from './machine_wire_compatibility.ts';

function schema() {
  const update = {
    type: 'object',
    properties: { target: { type: 'string' } },
    required: ['target'],
  };
  return {
    roots: ['Work'],
    manifest: { protocol_version: 27 },
    $defs: {
      MacUpdateCheck: update,
      MacUpdateReport: { type: 'object', properties: {} },
      MacRuntimeManifest: { type: 'object', properties: {} },
      ExecutorIdentity: { type: 'object', properties: {} },
      LeaseReply: {
        type: 'object',
        properties: { update: { $ref: '#/$defs/MacUpdateCheck' } },
        required: ['update'],
      },
      LeaseRefused: {
        type: 'object',
        properties: { update: { $ref: '#/$defs/MacUpdateCheck' } },
        required: ['update'],
      },
      Work: { type: 'object', properties: { task: { type: 'string' } }, required: ['task'] },
    },
  };
}
test('wire changes require a protocol increase, while optional fields remain additive', () => {
  const before = schema(),
    candidate = schema();
  Object.assign(candidate.$defs.Work.properties, { extra: { type: 'boolean' } });
  expect(() => check_machine_wire(before, candidate, 1)).not.toThrow();
  candidate.$defs.Work.properties.task.type = 'number';
  expect(() => check_machine_wire(before, candidate, 1)).toThrow('protocol increase');
  candidate.manifest.protocol_version++;
  expect(() => check_machine_wire(before, candidate, 1)).not.toThrow();
});
test('protocol increase never permits update-channel or lease-offer restrictions', () => {
  const before = schema(),
    candidate = schema();
  candidate.manifest.protocol_version++;
  candidate.$defs.MacUpdateCheck.properties.target.type = 'number';
  expect(() => check_machine_wire(before, candidate, 1)).toThrow('update channel');
  candidate.$defs.MacUpdateCheck = structuredClone(before.$defs.MacUpdateCheck);
  delete (candidate.$defs.LeaseRefused.properties as Record<string, unknown>).update;
  expect(() => check_machine_wire(before, candidate, 1)).toThrow('update offer');
  expect(() => check_machine_wire(before, before, 2)).toThrow('version 1');
});
