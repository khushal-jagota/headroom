/** Finite Python oracle table. Unknown inputs fail; this is not a wire decoder. */
import { readFileSync, writeFileSync } from 'node:fs';
import { isMainThread } from 'node:worker_threads';
import { register_test_root } from '../../../../core/test_paths.ts';
import {
  clone_numeric_json,
  parse_numeric_json,
  stringify_python_json,
} from '../../../../core/http/numeric_json.ts';
import type { RequiredJournalProtocol, JournalOutputPolicy } from '../journal_contracts.ts';

interface Fixture {
  entries: { root: string; hex: string; aliases?: string[] }[];
  owed: [number, number, boolean][];
}
// Manual real-disk probes explicitly own this root. Each worker has a separate registry.
const owned_root = new URL(import.meta.url).searchParams.get('owned_root');
if (!isMainThread && owned_root) register_test_root(owned_root);
const signature = (value: unknown) =>
  stringify_python_json(value, { sort_keys: true, compact: true });
let table:
  | { decoded: Map<string, unknown>; encoded: Map<string, unknown>; owed: Map<string, boolean> }
  | undefined;
function records() {
  if (table) return table;
  const path = new URL(import.meta.url).searchParams.get('fixture');
  if (!path) throw new Error('A finite Python fixture file is required');
  const fixture = JSON.parse(readFileSync(path, 'utf8')) as Fixture;
  const decoded = new Map<string, unknown>(),
    encoded = new Map<string, unknown>();
  const values = fixture.entries.map((entry) => {
    // These are trusted, already validated fixture bytes, never incoming wire bytes.
    const value = parse_numeric_json(Buffer.from(entry.hex, 'hex').toString('utf8'));
    decoded.set(entry.root + ':' + entry.hex, value);
    for (const key of [signature(value), ...(entry.aliases ?? [])])
      encoded.set(entry.root + ':' + key, value);
    return value;
  });
  const owed = new Map(
    fixture.owed.map(([c, r, value]) => [signature(values[c]) + ':' + signature(values[r]), value]),
  );
  return (table = { decoded, encoded, owed });
}
function known(map: Map<string, unknown>, key: string): unknown {
  if (!map.has(key)) throw new Error('Input is absent from the finite Python oracle table');
  return clone_numeric_json(map.get(key));
}
export const decode_json: RequiredJournalProtocol['decode_json'] = (bytes, root) => {
  const raw = typeof bytes === 'string' ? Buffer.from(bytes) : bytes;
  return known(records().decoded, root + ':' + Buffer.from(raw).toString('hex')) as never;
};
export const encode_value: RequiredJournalProtocol['encode_value'] = (value, root) =>
  known(records().encoded, root + ':' + signature(value)) as never;
export const command_owes_outputs: JournalOutputPolicy['command_owes_outputs'] = (
  command,
  result,
) => {
  const key = signature(command) + ':' + signature(result),
    owed = records().owed;
  if (!owed.has(key))
    throw new Error('Output policy pair is absent from the finite Python oracle table');
  const parameters = new URL(import.meta.url).searchParams;
  if (!isMainThread && command.command_id === parameters.get('gate_command_id')) {
    const gate = parameters.get('gate');
    if (!gate) throw new Error('The finite transaction gate requires its owned marker path');
    // The comparison parent owns this child and kills it after this transaction callback.
    writeFileSync(gate, command.command_id, { flag: 'wx' });
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20_000);
    throw new Error('The owned comparison child did not receive SIGKILL at its transaction gate');
  }
  return owed.get(key)!;
};
