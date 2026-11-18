/** Adapted from T3 Code b79d277c (apps/server/src/provider/Drivers/antigravityUsageReader.ts).
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/** Token usage from Antigravity's conversation databases. Each conversation is one SQLite file
 * whose rows hold raw protobuf. Databases are opened read-only; a file that cannot be read yields
 * no records. */
import { statSync, type Stats } from 'node:fs';
import { basename } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import type { FileTranscriptParser } from '../../core/usage.ts';
import type { UsageRecord } from '../../../conversation/usage/usageTranscripts.ts';

type FieldValue = number | bigint | Uint8Array;
type Fields = Map<number, FieldValue[]>;

function protobuf_fields(bytes: Uint8Array): Fields {
  let offset = 0;
  const result: Fields = new Map();
  const varint = (): number | bigint => {
    let value = 0n;
    for (let shift = 0n; shift < 70n; shift += 7n) {
      const byte = bytes[offset++];
      if (byte === undefined || (shift === 63n && byte > 1)) throw new Error('bad varint');
      value |= BigInt(byte & 127) << shift;
      if (byte < 128) return value > BigInt(Number.MAX_SAFE_INTEGER) ? value : Number(value);
    }
    throw new Error('bad varint');
  };
  while (offset < bytes.length) {
    const tag = varint();
    if (typeof tag !== 'number') throw new Error('bad field');
    const number = Math.floor(tag / 8);
    const wire = tag % 8;
    if (number === 0) throw new Error('bad field');
    let value: FieldValue;
    if (wire === 0) {
      value = varint();
    } else if (wire === 1 || wire === 5 || wire === 2) {
      const length = wire === 2 ? varint() : wire === 1 ? 8 : 4;
      if (typeof length !== 'number' || length > bytes.length - offset)
        throw new Error('bad length');
      value = bytes.subarray(offset, offset + length);
      offset += length;
      // Fixed-width fields carry no usage; only length-delimited ones are kept.
      if (wire !== 2) continue;
    } else {
      throw new Error('unsupported wire type');
    }
    const entries = result.get(number) ?? [];
    entries.push(value);
    result.set(number, entries);
  }
  return result;
}

const number_at = (fields: Fields, key: number): number => {
  const entry = fields.get(key)?.[0];
  return typeof entry === 'number' ? entry : 0;
};
const bytes_at = (fields: Fields, key: number): Uint8Array | undefined => {
  const entry = fields.get(key)?.[0];
  return entry instanceof Uint8Array ? entry : undefined;
};
const nested = (fields: Fields, key: number): Fields => {
  const bytes = bytes_at(fields, key);
  return bytes === undefined ? new Map() : protobuf_fields(bytes);
};
const text_at = (fields: Fields, key: number): string => {
  const bytes = bytes_at(fields, key);
  return bytes === undefined ? '' : new TextDecoder('utf-8', { fatal: true }).decode(bytes).trim();
};
const timestamp_ms = (fields: Fields): number | null => {
  const seconds = number_at(fields, 1);
  return seconds > 0 ? seconds * 1000 + Math.floor(number_at(fields, 2) / 1_000_000) : null;
};

/** Offline fallback for rows that carry only a numeric model id. */
const MODEL_IDS: Readonly<Record<number, string>> = {
  246: 'gemini-2.5-pro',
  312: 'gemini-2.5-flash',
  313: 'gemini-2.5-flash-thinking',
  329: 'gemini-2.5-flash-thinking',
  330: 'gemini-2.5-flash-lite',
  281: 'claude-sonnet-4',
  282: 'claude-sonnet-4',
  290: 'claude-opus-4',
  291: 'claude-opus-4',
  333: 'claude-sonnet-4-5',
  334: 'claude-sonnet-4-5',
  340: 'claude-haiku-4-5',
  341: 'claude-haiku-4-5',
  342: 'gpt-oss-120b-medium',
  1026: 'claude-opus-4-6',
  1035: 'claude-sonnet-4-6',
  1016: 'gemini-3.1-pro',
  1036: 'gemini-3.1-pro',
  1037: 'gemini-3.1-pro',
  1018: 'gemini-3-flash-preview',
  1084: 'gemini-3-flash-preview',
  1047: 'gemini-3-flash-preview',
  1020: 'gemini-3.5-flash-medium',
  1021: 'gemini-3.1-flash-image',
  1050: 'gemini-3.1-flash-lite',
  1071: 'gemini-3.6-flash-high',
  1072: 'gemini-3.6-flash-medium',
  1073: 'gemini-3.6-flash-low',
  1132: 'gemini-3.5-flash-high',
  1133: 'gemini-3.5-flash-high',
  1187: 'gemini-3.5-flash-extra-low',
  1196: 'gemini-3.6-flash',
  1266: 'gemini-3.6-flash',
  1298: 'gemini-3.7-flash-high',
  1299: 'gemini-3.7-flash-medium',
  1300: 'gemini-3.7-flash-low',
  1301: 'gemini-3.7-flash-tiered',
  1318: 'gemini-3.8-flash-high',
  1319: 'gemini-3.8-flash-medium',
  1320: 'gemini-3.8-flash-low',
  1322: 'gemini-3.8-flash-tiered',
  1402: 'claude-opus-5-5',
  1417: 'gemini-4-argon',
  1418: 'gemini-4-argon',
  1419: 'gemini-4-argon',
};

const GEMINI_3_FLASH_ROUTE = /^gemini-3-flash-(?:agent(?:-[ab])?|[ab])$/;

/** Effort and routing variants share the base model's price. */
export function antigravity_rate_model(model: string): string {
  const base = model.replace(/-(?:extra-low|low|medium|high|tiered|control|thinking|agent)$/, '');
  if (GEMINI_3_FLASH_ROUTE.test(model)) return 'gemini-3.5-flash';
  if (model === 'gemini-3.7-flash-safety-le') return 'gemini-3.7-flash';
  return base === 'gemini-3-flash' ? 'gemini-3-flash-preview' : base;
}

/** "Claude 4.5 Sonnet" becomes "claude-sonnet-4-5"; other names are lowercased and hyphenated. */
function model_name(name: string): string {
  if (name === '') return '';
  const normalized = name
    .toLowerCase()
    .replace(/\s*\(([^)]*)\)\s*$/, '-$1')
    .replace(/\s+/g, '-');
  if (!normalized.startsWith('claude-')) return normalized;
  return normalized
    .replace(/^claude-(\d+(?:\.\d+)?)-(sonnet|opus|haiku)/, 'claude-$2-$1')
    .replaceAll('.', '-');
}

function is_concrete_model(model: string): boolean {
  return (
    /^(?:gemini-\d|claude-(?:sonnet|opus|haiku)-\d|gpt-)/.test(model) &&
    !GEMINI_3_FLASH_ROUTE.test(model)
  );
}

interface Metadata {
  model: string;
  model_id: number;
  timestamp_ms: number | null;
  usages: Fields[];
}

/** A generation row (`gen_metadata`) or a step row (`steps`); they lay the same facts out differently. */
function read_metadata(bytes: Uint8Array, step: boolean): Metadata {
  const root = protobuf_fields(bytes);
  if (!step && bytes_at(root, 1) === undefined) throw new Error('no generation metadata');
  const data = step ? root : nested(root, 1);
  const model = step ? nested(data, 24) : data;
  const usages: Fields[] = [];
  const primary = bytes_at(data, step ? 9 : 4);
  if (primary !== undefined) usages.push(protobuf_fields(primary));
  for (const retry of data.get(step ? 28 : 17) ?? []) {
    if (!(retry instanceof Uint8Array)) throw new Error('bad retry metadata');
    const retry_usage = bytes_at(protobuf_fields(retry), 2);
    if (retry_usage !== undefined) usages.push(protobuf_fields(retry_usage));
  }
  const names = [text_at(model, step ? 12 : 19), text_at(model, step ? 8 : 21)].map(model_name);
  return {
    model:
      names.find(is_concrete_model) ?? names.find((name) => name && !/^model[_-]/.test(name)) ?? '',
    model_id:
      number_at(model, step ? 1 : 3) || (usages[0] === undefined ? 0 : number_at(usages[0], 1)),
    timestamp_ms: step
      ? (timestamp_ms(nested(data, 8)) ?? timestamp_ms(nested(data, 1)))
      : timestamp_ms(nested(nested(data, 9), 4)),
    usages,
  };
}

interface IndexedMetadata {
  idx: number;
  entry: Metadata;
}

function read_rows(db: BetterSqlite3.Database, query: string, step: boolean): IndexedMetadata[] {
  const entries: IndexedMetadata[] = [];
  for (const row of db.prepare(query).iterate() as Iterable<{ idx: unknown; data: unknown }>) {
    if (row.data === null || row.data === undefined) continue;
    if (typeof row.idx !== 'number' || !(row.data instanceof Uint8Array))
      throw new Error('bad row');
    entries.push({ idx: row.idx, entry: read_metadata(row.data, step) });
  }
  return entries.sort((a, b) => a.idx - b.idx);
}

interface Candidate {
  record: UsageRecord;
  model_id: number;
  model_quality: number;
  keys: readonly string[];
  timestamp_quality: number;
}

function candidates_of(
  session_id: string,
  generations: readonly IndexedMetadata[],
  steps: readonly IndexedMetadata[],
  trajectory_timestamp: number | null,
  fallback_timestamp: number,
): Candidate[] {
  const result: Candidate[] = [];
  const generation_by_idx = new Map(generations.map(({ idx, entry }) => [idx, entry]));
  for (const [source, entries] of [
    ['step', steps],
    ['generation', generations],
  ] as const) {
    for (const [index, { idx, entry }] of entries.entries()) {
      for (const [usage_index, usage] of entry.usages.entries()) {
        const model_id = number_at(usage, 1) || entry.model_id;
        const generation = source === 'step' ? generation_by_idx.get(idx) : undefined;
        // A retry can use a different model than its enclosing generation or step.
        const matches = (metadata: Metadata) => model_id === 0 || metadata.model_id === model_id;
        const model =
          matches(entry) && entry.model
            ? entry.model
            : generation && matches(generation)
              ? generation.model
              : '';
        const output_tokens = Math.max(
          number_at(usage, 3),
          number_at(usage, 9) + number_at(usage, 10),
        );
        const totals = {
          uncachedInputTokens: number_at(usage, 2),
          cachedInputTokens: number_at(usage, 5),
          cacheCreationTokens: number_at(usage, 4),
          outputTokens: output_tokens,
          reasoningTokens: Math.min(output_tokens, number_at(usage, 9)),
        };
        if (
          totals.uncachedInputTokens +
            totals.cachedInputTokens +
            totals.cacheCreationTokens +
            output_tokens ===
          0
        )
          continue;
        const keys = ([11, 12, 7] as const).flatMap((key) => {
          const id = text_at(usage, key);
          return id ? [`antigravity:${key}:${id}`] : [];
        });
        result.push({
          record: {
            provider: 'antigravity',
            sessionId: session_id,
            timestampMs: entry.timestamp_ms ?? trajectory_timestamp ?? fallback_timestamp,
            model,
            totals,
            reportedCostUsd: null,
            dedupeKey: keys[0] ?? `antigravity:${session_id}:${source}:${index}:${usage_index}`,
          },
          model_id,
          model_quality: is_concrete_model(model) ? 4 : model ? 1 : 0,
          keys,
          timestamp_quality:
            entry.timestamp_ms !== null ? 2 : trajectory_timestamp !== null ? 1 : 0,
        });
      }
    }
  }
  return result;
}

/** The same request is recorded in a step and in its generation, and a retry repeats ids. Records
 * sharing any id are one request: keep the largest of each count, the best model and the earliest
 * well-sourced time. */
function merge_duplicates(candidates: readonly Candidate[]): Candidate[] {
  const groups: Array<Candidate | null> = [];
  const owner_of_key = new Map<string, number>();
  const redirect = new Map<number, number>();
  const resolve = (index: number): number => {
    let current = index;
    for (let next = redirect.get(current); next !== undefined; next = redirect.get(current))
      current = next;
    return current;
  };
  const combine = (target: Candidate, source: Candidate): Candidate => {
    const best_model = source.model_quality > target.model_quality ? source : target;
    const best_time =
      source.timestamp_quality > target.timestamp_quality ||
      (source.timestamp_quality === target.timestamp_quality &&
        source.record.timestampMs < target.record.timestampMs)
        ? source
        : target;
    const x = target.record.totals;
    const y = source.record.totals;
    return {
      ...target,
      record: {
        ...target.record,
        model: best_model.record.model,
        timestampMs: best_time.record.timestampMs,
        totals: {
          uncachedInputTokens: Math.max(x.uncachedInputTokens, y.uncachedInputTokens),
          cachedInputTokens: Math.max(x.cachedInputTokens, y.cachedInputTokens),
          cacheCreationTokens: Math.max(x.cacheCreationTokens, y.cacheCreationTokens),
          outputTokens: Math.max(x.outputTokens, y.outputTokens),
          reasoningTokens: Math.max(x.reasoningTokens, y.reasoningTokens),
        },
      },
      model_quality: best_model.model_quality,
      timestamp_quality: best_time.timestamp_quality,
    };
  };
  for (const candidate of candidates) {
    let index = groups.length;
    groups.push(candidate);
    for (const key of candidate.keys) {
      const existing = owner_of_key.get(key);
      if (existing !== undefined) {
        const target = resolve(existing);
        const current = resolve(index);
        if (target !== current) {
          const [keep, drop] = target < current ? [target, current] : [current, target];
          groups[keep] = combine(groups[keep]!, groups[drop]!);
          groups[drop] = null;
          redirect.set(drop, keep);
          index = keep;
        }
      }
      owner_of_key.set(key, resolve(index));
    }
  }
  return groups.filter((group): group is Candidate => group !== null);
}

function final_model(candidate: Candidate, learned: ReadonlyMap<number, string | null>): Candidate {
  const learned_model = learned.get(candidate.model_id);
  const fallback = learned_model === null ? undefined : MODEL_IDS[candidate.model_id];
  const model =
    (candidate.model_quality === 4 ? candidate.record.model : '') ||
    learned_model ||
    fallback ||
    candidate.record.model ||
    (candidate.model_id > 0 ? `antigravity-model-${candidate.model_id}` : 'antigravity-unknown');
  return { ...candidate, record: { ...candidate.record, model } };
}

function with_pricing_model(record: UsageRecord): UsageRecord {
  const pricing_model = antigravity_rate_model(record.model);
  return pricing_model === record.model ? record : { ...record, pricing_model };
}

function read_database(path: string): UsageRecord[] {
  const db = new BetterSqlite3(path, { readonly: true, fileMustExist: true, timeout: 100 });
  try {
    const stat = statSync(path);
    const tables = new Set(
      (
        db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all() as {
          name: string;
        }[]
      ).map((row) => row.name),
    );
    if (!tables.has('gen_metadata') && !tables.has('steps')) throw new Error('not a conversation');
    const generations = tables.has('gen_metadata')
      ? read_rows(db, 'SELECT idx, data FROM gen_metadata ORDER BY rowid', false)
      : [];
    let trajectory_timestamp: number | null = null;
    if (tables.has('trajectory_metadata_blob')) {
      for (const row of db.prepare('SELECT data FROM trajectory_metadata_blob').all() as {
        data: unknown;
      }[]) {
        if (!(row.data instanceof Uint8Array)) throw new Error('bad trajectory row');
        trajectory_timestamp ??= timestamp_ms(nested(protobuf_fields(row.data), 2));
      }
    }
    const steps = tables.has('steps')
      ? read_rows(db, 'SELECT idx, metadata AS data FROM steps ORDER BY rowid', true)
      : [];
    // A model id seen with one concrete name is that name for every row of it; two names disagree.
    const learned = new Map<number, string | null>();
    for (const { entry } of [...generations, ...steps]) {
      if (entry.model_id > 0 && is_concrete_model(entry.model)) {
        const previous = learned.get(entry.model_id);
        learned.set(
          entry.model_id,
          previous === undefined || previous === entry.model ? entry.model : null,
        );
      }
    }
    const session_id = basename(path, '.db');
    return merge_duplicates(
      candidates_of(session_id, generations, steps, trajectory_timestamp, stat.mtimeMs),
    ).map((candidate) => with_pricing_model(final_model(candidate, learned).record));
  } finally {
    db.close();
  }
}

function size_and_mtime(path: string): string {
  try {
    const stat = statSync(path);
    return `${stat.size}:${stat.mtimeMs}`;
  } catch {
    return '-';
  }
}

const antigravity_transcript_parser: FileTranscriptParser = {
  kind: 'files',
  /** The write-ahead log holds new rows before the database file changes. */
  fingerprint(path: string, stat: Stats): string {
    if (!path.endsWith('.db')) return 'not-a-database';
    return `${stat.size}:${stat.mtimeMs}/${size_and_mtime(`${path}-wal`)}`;
  },
  async read_file(path: string): Promise<readonly UsageRecord[]> {
    if (!path.endsWith('.db')) return [];
    try {
      return read_database(path);
    } catch {
      return [];
    }
  },
};
export default antigravity_transcript_parser;
