/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/** Token usage from Codex rollout files, one line at a time, with state carried across scans. */
import type { LineTranscriptParser } from '../../core/usage.ts';
import { is_record } from '../../../core/records.ts';
import { parse_numeric_json, stringify_python_json } from '../../../core/http/numeric_json.ts';
import type { UsageTokenTotals } from '../../../conversation/usage/contracts.ts';
import {
  EMPTY_TOTALS,
  NO_RECORDS,
  parseTimestampMs,
  tokenCount,
  totalTokens,
  type UsageRecord,
} from '../../../conversation/usage/usageTranscripts.ts';

/** The longest gap between the re-stamped parent-history copies that open a forked rollout. */
const FORK_COPY_MAX_GAP_MS = 1000;

/**
 * Rolling state for a single Codex rollout file.
 *
 * Codex `token_count` events carry no model, so the model is carried forward
 * from the most recent `turn_context`. Sessions that switch models mid-run
 * attribute correctly from the switch onward.
 */
type UsageCounters = Record<string, number>;
interface CompactionSnapshot {
  current: UsageCounters;
  previous: UsageCounters | null;
  last: UsageCounters;
}
interface CompactionCandidate {
  usage: UsageCounters;
  cumulative: UsageCounters;
  covered: boolean;
  record: UsageRecord;
}

export interface CodexScanState {
  model: string;
  sessionId: string;
  lastUsageSignature: string | null;
  sawSessionMeta: boolean;
  /** While true, leading usage events are re-stamped copies of parent history. */
  suppressingForkCopies: boolean;
  forkCopyAnchorMs: number;
  serviceTier: string | null;
  compactions: Record<string, CompactionCandidate>;
  compactionMarkers: Record<string, CompactionSnapshot[]>;
  completedCompactions: string[];
  cumulativeUsage: UsageCounters | null;
}

export function initialCodexScanState(): CodexScanState {
  return {
    model: '',
    sessionId: '',
    lastUsageSignature: null,
    sawSessionMeta: false,
    suppressingForkCopies: false,
    forkCopyAnchorMs: 0,
    serviceTier: null,
    compactions: Object.create(null),
    compactionMarkers: Object.create(null),
    completedCompactions: [],
    cumulativeUsage: null,
  };
}

/**
 * A forked or subagent rollout opens with the parent's full history copied in,
 * every line re-stamped to the fork instant. Those copies are written in one
 * synchronous burst (observed gaps 0-40ms), while the child's first genuine
 * usage event only lands after a real model turn (observed 5s+). One second of
 * separation splits the two cleanly; `ccusage` uses the same threshold.
 */

/** Whether a `session_meta` payload marks the rollout as a fork or subagent. */
function isForkedSessionMeta(payload: Record<string, unknown>): boolean {
  if (typeof payload['forked_from_id'] === 'string') return true;
  const source = payload['source'];
  if (!is_record(source)) return false;
  const subagent = (source as Record<string, unknown>)['subagent'];
  if (!is_record(subagent)) return false;
  const spawn = (subagent as Record<string, unknown>)['thread_spawn'];
  if (!is_record(spawn)) return false;
  return typeof (spawn as Record<string, unknown>)['parent_thread_id'] === 'string';
}

const COUNTERS = [
  'input_tokens',
  'cached_input_tokens',
  'cache_write_input_tokens',
  'output_tokens',
  'reasoning_output_tokens',
  'total_tokens',
];
function usageCounters(value: unknown): UsageCounters {
  if (!is_record(value)) return {};
  return Object.fromEntries(
    COUNTERS.filter((key) => key in value).map((key) => [key, tokenCount(value[key])]),
  );
}
function equalCounters(a: UsageCounters, b: UsageCounters | null): boolean {
  return (
    b !== null &&
    Object.keys(a).length === Object.keys(b).length &&
    Object.entries(a).every(([key, value]) => b[key] === value)
  );
}
function snapshotCovers(candidate: CompactionCandidate, snapshot: CompactionSnapshot): boolean {
  const { current, previous, last } = snapshot;
  if (!Object.keys(current).length || equalCounters(current, previous)) return false;
  if (Object.keys(candidate.cumulative).length) {
    return Object.entries(candidate.cumulative).every(([key, value]) => current[key] === value);
  }
  if (
    previous !== null &&
    Object.keys(candidate.usage).length &&
    equalCounters(last, candidate.usage)
  ) {
    return ['input_tokens', 'output_tokens'].every(
      (key) =>
        key in current &&
        key in previous &&
        key in candidate.usage &&
        current[key]! - previous[key]! === candidate.usage[key],
    );
  }
  return false;
}
function compactionRecord(state: CodexScanState, responseId: string): UsageRecord | null {
  const candidate = Object.hasOwn(state.compactions, responseId)
    ? state.compactions[responseId]
    : undefined;
  if (!candidate || !Object.hasOwn(state.compactionMarkers, responseId)) return null;
  delete state.compactions[responseId];
  delete state.compactionMarkers[responseId];
  state.completedCompactions.push(responseId);
  return candidate.covered
    ? { ...candidate.record, totals: { ...EMPTY_TOTALS }, compactionCovered: true }
    : candidate.record;
}
function observeCompactionOverlap(state: CodexScanState, info: Record<string, unknown>): void {
  const current = usageCounters(info['total_token_usage']);
  const snapshot = {
    current,
    previous: state.cumulativeUsage,
    last: usageCounters(info['last_token_usage']),
  };
  for (const snapshots of Object.values(state.compactionMarkers)) snapshots.push(snapshot);
  for (const candidate of Object.values(state.compactions)) {
    candidate.covered ||= snapshotCovers(candidate, snapshot);
  }
  if (Object.keys(current).length) state.cumulativeUsage = current;
}

/**
 * Feeds one line of a Codex rollout into `state`, returning a record when the
 * line was a usage event.
 *
 * Deltas come from `last_token_usage`. Summing those across a session
 * reconciles with the session's final `total_token_usage`, provided
 * consecutive duplicate events are dropped, which this does.
 */
export function parseCodexLine(line: string, state: CodexScanState): UsageRecord | null {
  let parsed: unknown;
  try {
    parsed = parse_numeric_json(line);
  } catch {
    return null;
  }
  if (!is_record(parsed)) return null;

  const record = parsed as Record<string, unknown>;
  const payload = record['payload'];
  if (!is_record(payload)) return null;
  const payloadRecord = payload as Record<string, unknown>;
  const payloadType = payloadRecord['type'];

  if (record['type'] === 'session_meta') {
    // Only the first meta describes this file's own session. A forked rollout
    // repeats the ancestors' metas right after it; letting those through would
    // reassign every subsequent record to an ancestor session.
    if (state.sawSessionMeta) return null;
    state.sawSessionMeta = true;
    const id = payloadRecord['id'] ?? payloadRecord['session_id'];
    if (typeof id === 'string') state.sessionId = id;
    const metaTimestampMs = parseTimestampMs(record['timestamp']);
    if (metaTimestampMs !== null && isForkedSessionMeta(payloadRecord)) {
      state.suppressingForkCopies = true;
      state.forkCopyAnchorMs = metaTimestampMs;
    }
    return null;
  }

  if (record['type'] === 'turn_context') {
    if (typeof payloadRecord['model'] === 'string') state.model = payloadRecord['model'];
    return null;
  }

  if (payloadType === 'thread_settings_applied') {
    const settings = payloadRecord['thread_settings'];
    if (is_record(settings) && 'service_tier' in settings) {
      state.serviceTier =
        typeof settings['service_tier'] === 'string' ? settings['service_tier'] : null;
    }
    return null;
  }
  if (record['type'] === 'compacted') {
    const responseId = payloadRecord['compaction_response_id'];
    if (
      typeof responseId !== 'string' ||
      !responseId ||
      state.completedCompactions.includes(responseId)
    )
      return null;
    if (!Object.hasOwn(state.compactionMarkers, responseId))
      state.compactionMarkers = { ...state.compactionMarkers, [responseId]: [] };
    return compactionRecord(state, responseId);
  }
  if (record['type'] === 'token_usage_record') {
    const responseId = payloadRecord['response_id'];
    const usage = usageCounters(payloadRecord['usage']);
    const timestampMs = parseTimestampMs(record['timestamp']);
    if (
      typeof responseId !== 'string' ||
      !responseId ||
      !Object.keys(usage).length ||
      timestampMs === null ||
      !state.model ||
      state.completedCompactions.includes(responseId) ||
      Object.hasOwn(state.compactions, responseId)
    )
      return null;
    const inclusive = usage['input_tokens'] ?? 0;
    const cached = usage['cached_input_tokens'] ?? 0;
    const creation = usage['cache_write_input_tokens'] ?? 0;
    const output = usage['output_tokens'] ?? 0;
    const candidate: CompactionCandidate = {
      usage,
      cumulative: usageCounters(payloadRecord['thread_token_usage']),
      covered: false,
      record: {
        provider: 'codex',
        timestampMs,
        model: typeof payloadRecord['model'] === 'string' ? payloadRecord['model'] : state.model,
        sessionId:
          typeof payloadRecord['session_id'] === 'string'
            ? payloadRecord['session_id']
            : state.sessionId,
        totals: {
          uncachedInputTokens: Math.max(0, inclusive - cached - creation),
          cachedInputTokens: cached,
          cacheCreationTokens: creation,
          outputTokens: output,
          reasoningTokens: Math.min(output, usage['reasoning_output_tokens'] ?? 0),
        },
        reportedCostUsd: null,
        dedupeKey: 'codex:compaction:' + responseId,
        serviceTier:
          typeof payloadRecord['service_tier'] === 'string'
            ? payloadRecord['service_tier']
            : state.serviceTier,
      },
    };
    state.compactions = { ...state.compactions, [responseId]: candidate };
    candidate.covered =
      Object.hasOwn(state.compactionMarkers, responseId) &&
      state.compactionMarkers[responseId]!.some((snapshot) => snapshotCovers(candidate, snapshot));
    return compactionRecord(state, responseId);
  }
  if (payloadType !== 'token_count') return null;

  const info = payloadRecord['info'];
  if (!is_record(info)) return null;
  const last = (info as Record<string, unknown>)['last_token_usage'];
  if (!is_record(last)) return null;
  const lastRecord = last as Record<string, unknown>;

  // Only an event that is otherwise eligible may consume the duplicate
  // signature. A token_count arriving before its turn_context (no model yet)
  // must not poison it, or the re-emitted copy after the model is known would
  // be skipped as a duplicate and those tokens never counted.
  const timestampMs = parseTimestampMs(record['timestamp']);
  if (timestampMs === null) return null;
  if (state.model.length === 0) return null;

  // Codex re-emits an unchanged token_count on some stream boundaries. Summing
  // those would double count, so identical consecutive payloads are skipped.
  const signature = stringify_python_json(lastRecord, { compact: true });
  if (signature === state.lastUsageSignature) return null;
  state.lastUsageSignature = signature;

  // In a forked rollout the copied parent history was already counted from the
  // parent's own file. Drop the leading burst; the first usage event separated
  // from its predecessor by a real turn's worth of time ends it for good.
  if (state.suppressingForkCopies) {
    if (timestampMs - state.forkCopyAnchorMs < FORK_COPY_MAX_GAP_MS) {
      state.forkCopyAnchorMs = timestampMs;
      return null;
    }
    state.suppressingForkCopies = false;
  }

  const inputTokens = tokenCount(lastRecord['input_tokens']);
  const cachedInputTokens = tokenCount(lastRecord['cached_input_tokens']);
  const cacheCreationTokens = tokenCount(lastRecord['cache_write_input_tokens']);
  const outputTokens = tokenCount(lastRecord['output_tokens']);

  const totals: UsageTokenTotals = {
    // Codex reports `input_tokens` inclusive of the cached portion.
    uncachedInputTokens: Math.max(0, inputTokens - cachedInputTokens - cacheCreationTokens),
    cachedInputTokens,
    cacheCreationTokens,
    outputTokens,
    // Reported inside output_tokens, surfaced separately for the token mix.
    reasoningTokens: Math.min(outputTokens, tokenCount(lastRecord['reasoning_output_tokens'])),
  };

  if (totalTokens(totals) === 0) return null;
  observeCompactionOverlap(state, info);

  return {
    provider: 'codex',
    timestampMs,
    model: state.model,
    sessionId: state.sessionId,
    totals,
    // Codex does not report cost in the rollout.
    reportedCostUsd: null,
    // Events surviving the fork-copy suppression above are unique to this
    // rollout, so they need no global dedup.
    dedupeKey: null,
  };
}

/**
 * Validates a persisted reducer state. Returns `undefined` for a corrupt or
 * absent value, so the file is read again from the start: resuming with a bad
 * state would attach appended usage to the wrong model or replay fork-copied
 * history.
 */
export function reviveCodexScanState(value: unknown): CodexScanState | undefined {
  if (!is_record(value)) return undefined;
  const state = value as Partial<CodexScanState>;
  if (
    typeof state.model !== 'string' ||
    typeof state.sessionId !== 'string' ||
    (state.lastUsageSignature !== null && typeof state.lastUsageSignature !== 'string') ||
    typeof state.sawSessionMeta !== 'boolean' ||
    typeof state.suppressingForkCopies !== 'boolean' ||
    typeof state.forkCopyAnchorMs !== 'number' ||
    !Number.isFinite(state.forkCopyAnchorMs)
  ) {
    return undefined;
  }
  const counters = (item: unknown): item is Record<string, number> =>
    is_record(item) &&
    Object.values(item).every(
      (value) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0,
    );
  if (
    !('serviceTier' in state) ||
    (state.serviceTier !== null && typeof state.serviceTier !== 'string') ||
    !('cumulativeUsage' in state) ||
    (state.cumulativeUsage !== null && !counters(state.cumulativeUsage)) ||
    !is_record(state.compactions) ||
    !is_record(state.compactionMarkers) ||
    !Array.isArray(state.completedCompactions) ||
    !state.completedCompactions.every((item) => typeof item === 'string')
  )
    return undefined;
  for (const snapshots of Object.values(state.compactionMarkers)) {
    if (
      !Array.isArray(snapshots) ||
      !snapshots.every(
        (snapshot: unknown) =>
          is_record(snapshot) &&
          counters(snapshot.current) &&
          counters(snapshot.last) &&
          'previous' in snapshot &&
          (snapshot.previous === null || counters(snapshot.previous)),
      )
    )
      return undefined;
  }
  for (const candidate of Object.values(state.compactions)) {
    if (
      !is_record(candidate) ||
      !counters(candidate.usage) ||
      !counters(candidate.cumulative) ||
      typeof candidate.covered !== 'boolean' ||
      !is_record(candidate.record)
    )
      return undefined;
    const record = candidate.record;
    if (
      record.provider !== 'codex' ||
      typeof record.model !== 'string' ||
      typeof record.sessionId !== 'string' ||
      typeof record.timestampMs !== 'number' ||
      !Number.isFinite(record.timestampMs) ||
      typeof record.dedupeKey !== 'string' ||
      !('reportedCostUsd' in record) ||
      !counters(record.totals) ||
      ![
        'uncachedInputTokens',
        'cachedInputTokens',
        'cacheCreationTokens',
        'outputTokens',
        'reasoningTokens',
      ].every((key) => Object.hasOwn(record.totals as object, key))
    )
      return undefined;
  }
  return {
    model: state.model,
    sessionId: state.sessionId,
    lastUsageSignature: state.lastUsageSignature ?? null,
    sawSessionMeta: state.sawSessionMeta,
    suppressingForkCopies: state.suppressingForkCopies,
    forkCopyAnchorMs: state.forkCopyAnchorMs,
    serviceTier: state.serviceTier,
    compactions: state.compactions,
    compactionMarkers: state.compactionMarkers,
    completedCompactions: state.completedCompactions,
    cumulativeUsage: state.cumulativeUsage,
  };
}

/** Rollout lines that carry no usage still move the model, session and fork state. */
function codexMightCarryUsage(line: string): boolean {
  return [
    'token_count',
    'token_usage_record',
    'compacted',
    'thread_settings_applied',
    'turn_context',
    'session_meta',
  ].some((marker) => line.includes(`"${marker}"`));
}

const codex_transcript_parser: LineTranscriptParser<CodexScanState> = {
  kind: 'lines',
  initial_state: initialCodexScanState,
  revive_state: reviveCodexScanState,
  might_carry_usage: codexMightCarryUsage,
  parse_line: (line, state) => {
    const record = parseCodexLine(line, state);
    return record === null ? NO_RECORDS : [record];
  },
};
export default codex_transcript_parser;
