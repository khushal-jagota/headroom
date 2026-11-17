/** Adapted from T3 Code b79d277cddaf65858418f29d73b87fcc3d1a19c5 (apps/server/scripts/acp-replay-agent.ts).
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/**
 * One replay process standing in for a vendor executable. It reads one transcript, expects
 * the driver's frames in order, and writes the recorded replies.
 *
 * Environment: `REPLAY_TRANSCRIPT_PATH`, `REPLAY_STATUS_PATH`, and `REPLAY_WORKSPACE` for the
 * `<workspace>` placeholder. The wire comes from the transcript. `--version` prints the
 * transcript's version line. The status file holds the cursor and the first failure.
 */
import { renameSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { ReplayBindings, match_frame, materialize_frame } from './frame_match.ts';
import { read_transcript, wire_rules } from './transcript.ts';
import type { BackgroundRequest } from './transcript.ts';

export interface PeerStatus {
  readonly cursor: number;
  readonly total: number;
  readonly failure: string | null;
}
const MISMATCH_EXIT_CODE = 70;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    process.stderr.write(`The replay peer requires ${name}.\n`);
    process.exit(2);
  }
  return value;
}
const transcript = read_transcript(required('REPLAY_TRANSCRIPT_PATH'));
if (process.argv.includes('--version')) {
  process.stdout.write(`${transcript.version_line}\n`);
  process.exit(0);
}
const status_path = required('REPLAY_STATUS_PATH');
const wire = wire_rules(transcript.wire);
const bindings = new ReplayBindings({
  '<workspace>': required('REPLAY_WORKSPACE'),
  ...wire.launch_placeholders(process.argv.slice(2)),
});
const entries = transcript.entries;
const background: readonly BackgroundRequest[] = transcript.background ?? [];
let cursor = 0;
let failure: string | null = null;
let emitting = false;
let exiting = false;

// The harness reads this file after the process ends. Rename keeps each write whole.
function write_status(): void {
  const status: PeerStatus = { cursor, total: entries.length, failure };
  const temporary = `${status_path}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(status));
  renameSync(temporary, status_path);
}
function stop_with_failure(detail: string, actual?: unknown): never {
  const entry = entries[cursor];
  failure ??= [
    detail,
    `at entry ${cursor + 1} of ${entries.length}${entry && 'label' in entry && entry.label ? ` (${entry.label})` : ''}`,
    ...(actual === undefined ? [] : [`actual ${JSON.stringify(actual)}`]),
  ].join('; ');
  write_status();
  process.stderr.write(`replay mismatch: ${failure}\n`);
  process.exit(MISMATCH_EXIT_CODE);
}
/** Exit once every written frame has left, since a pipe write can be asynchronous. */
function exit_after_output(code: number): void {
  exiting = true;
  process.stdout.write('', () => process.exit(code));
}
function write_frame(frame: unknown): void {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}
function materialized(frame: unknown): unknown {
  try {
    return materialize_frame(frame, bindings);
  } catch (error) {
    return stop_with_failure(error instanceof Error ? error.message : String(error));
  }
}

/** Write every inbound entry up to the next expected outbound frame, in order. */
async function emit_inbound(): Promise<void> {
  if (emitting) return;
  emitting = true;
  try {
    for (let entry = entries[cursor]; entry !== undefined; entry = entries[cursor]) {
      if (entry.type === 'expect_outbound') break;
      if (entry.type === 'runtime_exit') {
        cursor += 1;
        write_status();
        exit_after_output(entry.exit_code);
        return;
      }
      if (entry.after_ms) await new Promise((resolve) => setTimeout(resolve, entry.after_ms));
      write_frame(materialized(entry.frame));
      cursor += 1;
      write_status();
    }
  } finally {
    emitting = false;
  }
}
function answer_background(actual: unknown): boolean {
  const method = wire.request_method(actual);
  if (method === null) return false;
  for (const row of background) {
    if (row.method !== method) continue;
    if (!match_frame(row.request, actual, bindings).matched) continue;
    write_frame(materialized(wire.background_reply(actual, row.reply)));
    return true;
  }
  return false;
}
function take_outbound(line: string): void {
  let actual: unknown;
  try {
    actual = JSON.parse(line);
  } catch {
    stop_with_failure('the driver wrote a line that is not JSON', line);
  }
  const entry = entries[cursor];
  if (entry?.type === 'expect_outbound') {
    const match = match_frame(entry.frame, actual, bindings);
    if (match.matched) {
      cursor += 1;
      write_status();
      void emit_inbound();
      return;
    }
    if (answer_background(actual)) return;
    stop_with_failure(`unexpected outbound frame (${match.why})`, actual);
  }
  if (answer_background(actual)) return;
  stop_with_failure(
    entry === undefined
      ? 'outbound frame after the transcript ended'
      : 'outbound frame while the transcript emits',
    actual,
  );
}

write_status();
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
// One line at a time, so an inbound batch that waits on `after_ms` keeps the order.
let taken: Promise<void> = Promise.resolve();
input.on('line', (line) => {
  taken = taken.then(async () => {
    while (emitting) await new Promise((resolve) => setTimeout(resolve, 1));
    if (!exiting && line.trim() !== '') take_outbound(line);
  });
});
function finish(why: string): void {
  if (exiting) return;
  if (failure === null && cursor !== entries.length) {
    failure = `${why} before the transcript ended, at entry ${cursor + 1} of ${entries.length}`;
  }
  write_status();
  exit_after_output(0);
}
input.once('close', () => {
  void taken.then(() => finish('the driver closed standard input'));
});
process.once('SIGTERM', () => finish('the driver sent SIGTERM'));
process.once('SIGINT', () => finish('the driver sent SIGINT'));
await emit_inbound();
