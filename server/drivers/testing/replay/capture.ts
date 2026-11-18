#!/usr/bin/env node
/**
 * A development tee that records a replay transcript. The tee never runs in a test.
 *
 *   node drivers/testing/replay/capture.ts --scenario two_turns --driver-kind claude \
 *     --model haiku --provenance scripted_peer --source "…" --output <file.ndjson> \
 *     [--target-environment NAME=VALUE …] -- <target executable> [target arguments…]
 *
 * It drives the scenario's steps through the driver's real adapter, with itself in front of
 * the target: a scripted peer or, for a real recording, the vendor binary. The target is
 * started with the driver's own arguments after the given ones, and the driver's environment
 * with each `--target-environment` value on top. The written file is normalized: the run's
 * workspace becomes `<workspace>`, launch values such as Claude's session become their
 * placeholder, and each value the driver generated becomes `<id:N>` in first-seen order.
 * Background tables are added by hand.
 */
import { spawn } from 'node:child_process';
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { is_record } from '../../../core/records.ts';
import { SCENARIOS } from './scenarios.ts';
import { TRANSCRIPT_PROVENANCES, encode_transcript, wire_rules } from './transcript.ts';
import type { ReplayEntry, ReplayWire, Transcript, TranscriptProvenance } from './transcript.ts';

const TEE_LOG = 'REPLAY_CAPTURE_LOG';
const TEE_TARGET = 'REPLAY_CAPTURE_TARGET';
const TEE_TARGET_ENVIRONMENT = 'REPLAY_CAPTURE_TARGET_ENVIRONMENT';
const WIRES: Readonly<Record<string, ReplayWire>> = {
  claude: 'claude_stream_json',
  codex: 'codex_app_server',
  hermes: 'acp',
};

export type TeeRecord =
  | { readonly kind: 'launch'; readonly argv: readonly string[] }
  | { readonly kind: 'version'; readonly line: string }
  | { readonly kind: 'outbound' | 'inbound'; readonly frame: unknown }
  /** The driver began stopping the target: its stdin ended or a stop signal arrived. */
  | { readonly kind: 'stop' }
  | { readonly kind: 'exit'; readonly code: number | null; readonly signal: string | null };

/** The tee itself: one target process with both directions of its stdio logged. */
function tee(log: string): void {
  const record = (value: TeeRecord) => appendFileSync(log, `${JSON.stringify(value)}\n`);
  const [command, ...fixed] = JSON.parse(process.env[TEE_TARGET]!) as string[];
  const argv = [...fixed, ...process.argv.slice(2)];
  const environment = { ...process.env };
  for (const name of [TEE_LOG, TEE_TARGET, TEE_TARGET_ENVIRONMENT]) delete environment[name];
  Object.assign(environment, JSON.parse(process.env[TEE_TARGET_ENVIRONMENT] ?? '{}'));
  const version = process.argv.includes('--version');
  if (!version) record({ kind: 'launch', argv: process.argv.slice(2) });
  const target = spawn(command!, argv, { env: environment, stdio: ['pipe', 'pipe', 'inherit'] });
  const lines = (stream: NodeJS.ReadableStream, take: (line: string) => void) =>
    createInterface({ input: stream, crlfDelay: Infinity }).on('line', take);
  if (version) {
    let output = '';
    target.stdout.on('data', (chunk: Buffer) => {
      output += chunk.toString('utf8');
      process.stdout.write(chunk);
    });
    target.once('close', (code) => {
      record({ kind: 'version', line: output.split('\n')[0]!.trim() });
      process.exit(code ?? 1);
    });
    return;
  }
  lines(process.stdin, (line) => {
    if (line.trim() !== '') record({ kind: 'outbound', frame: JSON.parse(line) });
    target.stdin.write(`${line}\n`);
  });
  let stopping = false;
  const stop_begins = () => {
    if (stopping) return;
    stopping = true;
    record({ kind: 'stop' });
  };
  process.stdin.once('end', () => {
    stop_begins();
    target.stdin.end();
  });
  lines(target.stdout, (line) => {
    if (line.trim() !== '') record({ kind: 'inbound', frame: JSON.parse(line) });
    process.stdout.write(`${line}\n`);
  });
  for (const signal of ['SIGTERM', 'SIGINT'] as const)
    process.on(signal, () => {
      stop_begins();
      target.kill(signal);
    });
  target.once('close', (code, signal) => {
    record({ kind: 'exit', code, signal });
    process.exit(code ?? 1);
  });
}

function replace_values(
  value: unknown,
  numbered: ReadonlyMap<string | number, string>,
  fixed: ReadonlyMap<string, string>,
  top_level_id: boolean,
): unknown {
  if (typeof value === 'string') {
    const placeholder = numbered.get(value);
    if (placeholder !== undefined) return placeholder;
    let result = value;
    for (const [text, name] of fixed) result = result.replaceAll(text, name);
    // A generated uuid also names peer values such as `script-lifecycle-<uuid>-queued`.
    for (const [text, name] of numbered)
      if (typeof text === 'string' && text.length >= 8) result = result.replaceAll(text, name);
    return result;
  }
  if (Array.isArray(value))
    return value.map((each) => replace_values(each, numbered, fixed, false));
  if (!is_record(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, each]) => [
      key,
      // A numbered number is only ever an id member. Elsewhere a number is data.
      top_level_id && key === 'id' && typeof each === 'number' && numbered.has(each)
        ? numbered.get(each)
        : replace_values(each, numbered, fixed, false),
    ]),
  );
}
function leaf_values(value: unknown, into: Set<unknown>): void {
  if (Array.isArray(value)) for (const each of value) leaf_values(each, into);
  else if (is_record(value)) for (const each of Object.values(value)) leaf_values(each, into);
  else into.add(value);
}
/** Turn the tee's log into a transcript. */
export function normalized_transcript(
  records: readonly TeeRecord[],
  fields: Omit<Transcript, 'entries' | 'version_line' | 'wire'>,
  workspace: string,
): Transcript {
  const wire = WIRES[fields.driver_kind];
  if (wire === undefined) throw new Error(`no wire for driver ${fields.driver_kind}`);
  const rules = wire_rules(wire);
  const version = records.find((record) => record.kind === 'version');
  const launch = records.find((record) => record.kind === 'launch');
  if (version === undefined || launch === undefined)
    throw new Error('the target was never probed and launched');
  const fixed = new Map<string, string>([[workspace, '<workspace>']]);
  for (const [name, text] of Object.entries(rules.launch_placeholders(launch.argv)))
    fixed.set(text, name);
  const seen = new Set<unknown>();
  const numbered = new Map<string | number, string>();
  const entries: ReplayEntry[] = [];
  // An exit once the driver began its own stop is that stop, not a crash to replay.
  let stopping = false;
  for (const record of records) {
    if (record.kind === 'outbound') {
      for (const value of rules.generated_values(record.frame))
        if (!seen.has(value) && !numbered.has(value))
          numbered.set(value, `<id:${numbered.size + 1}>`);
      entries.push({
        type: 'expect_outbound',
        frame: replace_values(record.frame, numbered, fixed, true),
      });
      leaf_values(record.frame, seen);
    } else if (record.kind === 'inbound') {
      entries.push({
        type: 'emit_inbound',
        frame: replace_values(record.frame, numbered, fixed, true),
      });
      leaf_values(record.frame, seen);
    } else if (record.kind === 'stop') {
      stopping = true;
    } else if (record.kind === 'exit' && !stopping && record.signal === null && record.code !== 0) {
      entries.push({ type: 'runtime_exit', exit_code: record.code ?? 1 });
    }
  }
  return { ...fields, wire, version_line: version.line, entries };
}

async function capture(): Promise<void> {
  // A deployment's PLAN_* values would make the driver act for that deployment.
  const inherited = Object.keys(process.env).filter((name) => name.startsWith('PLAN_'));
  if (inherited.length) throw new Error(`unset ${inherited.join(', ')} before a capture`);
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      scenario: { type: 'string' },
      'driver-kind': { type: 'string' },
      model: { type: 'string' },
      provenance: { type: 'string' },
      source: { type: 'string' },
      output: { type: 'string' },
      'target-environment': { type: 'string', multiple: true },
    },
  });
  const scenario = SCENARIOS.find((each) => each.name === values.scenario);
  const driver_kind = values['driver-kind'];
  const provenance = values.provenance as TranscriptProvenance;
  if (
    scenario === undefined ||
    !driver_kind ||
    !values.model ||
    !values.output ||
    !TRANSCRIPT_PROVENANCES.includes(provenance) ||
    positionals.length === 0
  )
    throw new Error('see the usage at the top of capture.ts');
  const target_environment = Object.fromEntries(
    (values['target-environment'] ?? []).map((pair) => {
      const split = pair.indexOf('=');
      if (split < 1) throw new Error(`--target-environment needs NAME=VALUE, not ${pair}`);
      return [pair.slice(0, split), pair.slice(split + 1)];
    }),
  );
  // The tee loads none of the drivers, so it starts as fast as the target.
  const { drive_driver, run_folders, write_shim } = await import('./harness.ts');
  const folders = await run_folders();
  const log = join(folders.root, 'capture.ndjson');
  const executable = join(folders.root, driver_kind);
  await write_shim(executable, new URL(import.meta.url), {
    [TEE_LOG]: log,
    [TEE_TARGET]: JSON.stringify(positionals),
    [TEE_TARGET_ENVIRONMENT]: JSON.stringify(target_environment),
  });
  try {
    await drive_driver({ driver_kind, model: values.model, executable, folders, scenario });
  } finally {
    const records = readFileSync(log, 'utf8')
      .split('\n')
      .filter((line) => line !== '')
      .map((line) => JSON.parse(line) as TeeRecord);
    const transcript = normalized_transcript(
      records,
      {
        driver_kind,
        model: values.model,
        scenario: scenario.name,
        provenance,
        source: values.source ?? null,
      },
      folders.workspace,
    );
    writeFileSync(values.output, encode_transcript(transcript));
    process.stdout.write(`${values.output}: ${transcript.entries.length} entries\n`);
  }
}

if (process.env[TEE_LOG]) tee(process.env[TEE_LOG]);
else if (import.meta.url === pathToFileURL(resolve(process.argv[1]!)).href) await capture();
