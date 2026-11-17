/** Adapted from T3 Code b79d277cddaf65858418f29d73b87fcc3d1a19c5 (its replay transcript entries).
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/**
 * One replay transcript: what a vendor process must receive and what it sends back.
 *
 * The file is NDJSON. Its first line holds the transcript's own fields and every later line
 * is one entry. There is no `transcript_start` entry: those fields sit on `Transcript`.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { is_record } from '../../../core/records.ts';
import { CLAUDE_STREAM_JSON_WIRE } from './wires/claude_stream_json.ts';
import { JSON_RPC_WIRE } from './wires/json_rpc.ts';

export const REPLAY_WIRES = ['claude_stream_json', 'codex_app_server', 'acp'] as const;
export type ReplayWire = (typeof REPLAY_WIRES)[number];
export const TRANSCRIPT_PROVENANCES = [
  'scripted_peer',
  'authored',
  't3_converted',
  'recorded',
] as const;
export type TranscriptProvenance = (typeof TRANSCRIPT_PROVENANCES)[number];

export type ReplayEntry =
  | {
      readonly type: 'expect_outbound';
      readonly label?: string;
      /** This entry was written by hand inside a file of another provenance. */
      readonly authored?: true;
      readonly frame: unknown;
    }
  | {
      readonly type: 'emit_inbound';
      readonly label?: string;
      readonly authored?: true;
      readonly frame: unknown;
      readonly after_ms?: number;
    }
  /** Ends the peer process with this code. It is always the last entry. */
  | { readonly type: 'runtime_exit'; readonly label?: string; readonly exit_code: number };

/** What a peer and the capture tee need to know about one wire. */
export interface ReplayWireRules {
  /** Fixed placeholders that the launch arguments decide, such as Claude's `<session>`. */
  launch_placeholders(argv: readonly string[]): Readonly<Record<string, string>>;
  /** The method of a request the driver sent, which a background row may answer. */
  request_method(frame: unknown): string | null;
  /** The reply to one background request, carrying that request's own id. */
  background_reply(request: unknown, reply: unknown): unknown;
  /** The values the driver generated in one outbound frame, which capture numbers. */
  generated_values(frame: unknown): readonly (string | number)[];
}
export function wire_rules(wire: ReplayWire): ReplayWireRules {
  return wire === 'claude_stream_json' ? CLAUDE_STREAM_JSON_WIRE : JSON_RPC_WIRE;
}

/** A request the driver may send at any point and any number of times. */
export interface BackgroundRequest {
  readonly method: string;
  readonly authored?: true;
  readonly request: unknown;
  readonly reply: unknown;
}
export interface Transcript {
  readonly driver_kind: string;
  readonly wire: ReplayWire;
  /** The executable's `--version` output, without its newline. */
  readonly version_line: string;
  /** The model the conversation starts on. Outbound frames carry it. */
  readonly model: string;
  readonly scenario: string;
  readonly provenance: TranscriptProvenance;
  readonly source: string | null;
  readonly background?: readonly BackgroundRequest[];
  readonly entries: readonly ReplayEntry[];
}

const HEADER_KEYS = new Set([
  'driver_kind',
  'wire',
  'version_line',
  'model',
  'scenario',
  'provenance',
  'source',
  'background',
]);
const ENTRY_KEYS: Readonly<Record<ReplayEntry['type'], ReadonlySet<string>>> = {
  expect_outbound: new Set(['type', 'label', 'authored', 'frame']),
  emit_inbound: new Set(['type', 'label', 'authored', 'frame', 'after_ms']),
  runtime_exit: new Set(['type', 'label', 'exit_code']),
};

function fail(where: string, why: string): never {
  throw new Error(`${where}: ${why}`);
}
function only_keys(value: Record<string, unknown>, keys: ReadonlySet<string>, where: string) {
  for (const key of Object.keys(value)) if (!keys.has(key)) fail(where, `unknown field ${key}`);
}
function text(value: unknown, where: string): string {
  if (typeof value !== 'string' || value === '') fail(where, 'expected a non-empty string');
  return value;
}
function optional_label(value: Record<string, unknown>, where: string): void {
  if ('label' in value) text(value.label, `${where} label`);
  if ('authored' in value && value.authored !== true) fail(where, 'authored must be true');
}
function entry(value: unknown, where: string, last: boolean): ReplayEntry {
  if (!is_record(value)) fail(where, 'an entry must be an object');
  const type = value.type;
  if (type !== 'expect_outbound' && type !== 'emit_inbound' && type !== 'runtime_exit')
    fail(where, `unknown entry type ${JSON.stringify(type)}`);
  only_keys(value, ENTRY_KEYS[type], where);
  optional_label(value, where);
  if (type === 'runtime_exit') {
    if (!Number.isInteger(value.exit_code)) fail(where, 'exit_code must be an integer');
    if (!last) fail(where, 'runtime_exit ends the peer, so it must be the last entry');
  } else {
    if (!('frame' in value)) fail(where, 'the entry has no frame');
    if (
      'after_ms' in value &&
      (typeof value.after_ms !== 'number' || !(value.after_ms >= 0) || value.after_ms > 10_000)
    )
      fail(where, 'after_ms must be a number from 0 to 10000');
  }
  return value as unknown as ReplayEntry;
}
function background(value: unknown, where: string): readonly BackgroundRequest[] {
  if (!Array.isArray(value)) fail(where, 'background must be an array');
  return value.map((row, index) => {
    const at = `${where} background ${index}`;
    if (!is_record(row)) fail(at, 'a background row must be an object');
    only_keys(row, new Set(['method', 'authored', 'request', 'reply']), at);
    text(row.method, `${at} method`);
    if ('authored' in row && row.authored !== true) fail(at, 'authored must be true');
    if (!('request' in row) || !('reply' in row))
      fail(at, 'a background row needs request and reply');
    return row as unknown as BackgroundRequest;
  });
}

/** Decode one NDJSON transcript. `where` names it in errors. */
export function decode_transcript(source: string, where: string): Transcript {
  const lines = source.split('\n').filter((line) => line.trim() !== '');
  if (lines.length < 2) fail(where, 'a transcript needs its fields line and at least one entry');
  const parsed = lines.map((line, index) => {
    try {
      return JSON.parse(line) as unknown;
    } catch (error) {
      return fail(`${where}:${index + 1}`, `not JSON (${String(error)})`);
    }
  });
  const header = parsed[0];
  if (!is_record(header)) fail(`${where}:1`, 'the fields line must be an object');
  only_keys(header, HEADER_KEYS, `${where}:1`);
  const wire = header.wire;
  if (!REPLAY_WIRES.includes(wire as ReplayWire)) fail(`${where}:1`, 'unknown wire');
  const provenance = header.provenance;
  if (!TRANSCRIPT_PROVENANCES.includes(provenance as TranscriptProvenance))
    fail(`${where}:1`, 'unknown provenance');
  if (header.source !== null) text(header.source, `${where}:1 source`);
  const entries = parsed
    .slice(1)
    .map((value, index) => entry(value, `${where}:${index + 2}`, index === parsed.length - 2));
  return {
    driver_kind: text(header.driver_kind, `${where}:1 driver_kind`),
    wire: wire as ReplayWire,
    version_line: text(header.version_line, `${where}:1 version_line`),
    model: text(header.model, `${where}:1 model`),
    scenario: text(header.scenario, `${where}:1 scenario`),
    provenance: provenance as TranscriptProvenance,
    source: header.source as string | null,
    ...('background' in header ? { background: background(header.background, `${where}:1`) } : {}),
    entries,
  };
}
/** Encode a transcript as the NDJSON `decode_transcript` reads. */
export function encode_transcript(transcript: Transcript): string {
  const { entries, ...fields } = transcript;
  return [fields, ...entries].map((line) => JSON.stringify(line)).join('\n') + '\n';
}
export function read_transcript(path: string): Transcript {
  return decode_transcript(readFileSync(path, 'utf8'), path);
}

export const TRANSCRIPTS_DIRECTORY = fileURLToPath(new URL('./transcripts/', import.meta.url));
/** Every `transcripts/<scenario>/<driver_kind>*.ndjson`, so a later recording needs no code. */
export function transcript_paths(scenario: string, driver_kind: string): readonly string[] {
  const directory = join(TRANSCRIPTS_DIRECTORY, scenario);
  let names: string[];
  try {
    names = readdirSync(directory);
  } catch {
    return [];
  }
  return names
    .filter((name) => name.startsWith(driver_kind) && name.endsWith('.ndjson'))
    .sort()
    .map((name) => join(directory, name));
}
