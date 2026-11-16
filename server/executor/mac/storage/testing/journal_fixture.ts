/** Finite Python output-policy oracle. All wire decoding and encoding use protocol.ts. */
import { readFileSync, writeFileSync } from 'node:fs';
import { isMainThread } from 'node:worker_threads';
import { register_test_root } from '../../../../core/test_paths.ts';
import { decode_json } from '../../../protocol.ts';
import type { JournalOutputPolicy } from '../journal_contracts.ts';

interface Fixture {
  entries: { root: string; hex: string }[];
  owed: [number, number, boolean][];
}
// Manual real-disk probes explicitly own this root. Each worker has a separate registry.
const owned_root = new URL(import.meta.url).searchParams.get('owned_root');
if (!isMainThread && owned_root) register_test_root(owned_root);
const signature = (value: unknown) =>
  JSON.stringify(value, (_key, item) =>
    item !== null && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : item,
  );
let table: Map<string, boolean> | undefined;
function records() {
  if (table) return table;
  const path = new URL(import.meta.url).searchParams.get('fixture');
  if (!path) throw new Error('A finite Python fixture file is required');
  const fixture = JSON.parse(readFileSync(path, 'utf8')) as Fixture;
  return (table = new Map(
    fixture.owed.map(([c, r, value]) => {
      const command = decode_json(Buffer.from(fixture.entries[c]!.hex, 'hex'), 'MailboxCommand');
      const result = decode_json(Buffer.from(fixture.entries[r]!.hex, 'hex'), 'CommandResult');
      return [signature(command) + ':' + signature(result), value] as const;
    }),
  ));
}
export const command_owes_outputs: JournalOutputPolicy['command_owes_outputs'] = (
  command,
  result,
) => {
  const key = signature(command) + ':' + signature(result),
    owed = records();
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
