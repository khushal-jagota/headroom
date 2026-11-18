/**
 * A target's non-zero exit becomes a replayed crash only when it came before the driver began
 * its own stop. An exit the stop caused would otherwise replay as a mid-run crash.
 */
import { expect, test } from 'vitest';
import { normalized_transcript, type TeeRecord } from './capture.ts';

const FIELDS = {
  driver_kind: 'codex',
  model: 'gpt-test',
  scenario: 'two_turns',
  provenance: 'scripted_peer',
  source: null,
} as const;
const OPENING: readonly TeeRecord[] = [
  { kind: 'version', line: 'codex-cli 0.0.0' },
  { kind: 'launch', argv: ['app-server'] },
  { kind: 'outbound', frame: { id: 1, method: 'initialize' } },
  { kind: 'inbound', frame: { id: 1, result: {} } },
];
const entry_types = (records: readonly TeeRecord[]) =>
  normalized_transcript(records, FIELDS, '/workspace').entries.map((entry) => entry.type);

test('an exit before the stop is a runtime exit, and one after it is not', () => {
  const crashed = normalized_transcript(
    [...OPENING, { kind: 'exit', code: 7, signal: null }],
    FIELDS,
    '/workspace',
  );
  expect(crashed.entries.at(-1)).toEqual({ type: 'runtime_exit', exit_code: 7 });
  expect(
    entry_types([...OPENING, { kind: 'stop' }, { kind: 'exit', code: 1, signal: null }]),
  ).toEqual(['expect_outbound', 'emit_inbound']);
});
