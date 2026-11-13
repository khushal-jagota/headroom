import { dirname, basename, join } from 'node:path';
import { MetricsValueError } from './contracts.ts';
import { resolved_path } from '../paths.ts';
export interface MetricsConfig {
  idle_seconds: number;
  active_seconds: number;
  activity_seconds: number;
  activity_check_seconds: number;
  ownership_retry_seconds: number;
  slow_seconds: number;
  prune_seconds: number;
  raw_seconds: number;
  minute_seconds: number;
  coarse_seconds: number;
  process_seconds: number;
  metrics_payload_bytes: number;
  process_payload_bytes: number;
  process_snapshot_bytes: number;
  transaction_payload_bytes: number;
  database_bytes: number;
  wal_bytes: number;
  read_seconds: number;
  read_workers: number;
  process_page_rows: number;
  history_points: number;
  service_query_seconds: number;
  service_output_bytes: number;
  filesystem_path: string;
}
export const DEFAULT_METRICS_CONFIG: Readonly<MetricsConfig> = Object.freeze({
  idle_seconds: 15,
  active_seconds: 2,
  activity_seconds: 30,
  activity_check_seconds: 1,
  ownership_retry_seconds: 15,
  slow_seconds: 60,
  prune_seconds: 60,
  raw_seconds: 3600,
  minute_seconds: 48 * 3600,
  coarse_seconds: 31 * 86400,
  process_seconds: 15 * 60,
  metrics_payload_bytes: 192 * 1024 * 1024,
  process_payload_bytes: 64 * 1024 * 1024,
  process_snapshot_bytes: 4 * 1024 * 1024,
  transaction_payload_bytes: 8 * 1024 * 1024,
  database_bytes: 512 * 1024 * 1024,
  wal_bytes: 64 * 1024 * 1024,
  read_seconds: 0.5,
  read_workers: 2,
  process_page_rows: 250,
  history_points: 2000,
  service_query_seconds: 1,
  service_output_bytes: 1024 * 1024,
  filesystem_path: '/',
});
const INTEGER_FIELDS = new Set([
  'metrics_payload_bytes',
  'process_payload_bytes',
  'process_snapshot_bytes',
  'transaction_payload_bytes',
  'database_bytes',
  'wal_bytes',
  'read_workers',
  'process_page_rows',
  'history_points',
  'service_output_bytes',
]);
function configured_number(raw: string, integer: boolean): number {
  // int()/float() accept decimal Unicode digits and underscores between digits.
  const text = raw
    .replace(/\p{Decimal_Number}/gu, (character) => {
      const point = character.codePointAt(0)!;
      let first = point;
      while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
      return String((point - first) % 10);
    })
    .replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
  const valid = integer
    ? /^[+-]?\d(?:_?\d)*$/.test(text)
    : /^[+-]?(?:(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:e[+-]?\d(?:_?\d)*)?|inf(?:inity)?|nan)$/i.test(
        text,
      );
  if (!valid)
    throw new MetricsValueError(
      integer
        ? `Invalid integer: ${JSON.stringify(raw)}`
        : `Invalid number: ${JSON.stringify(raw)}`,
    );
  if (/^[+-]?inf(?:inity)?$/i.test(text)) return text.startsWith('-') ? -Infinity : Infinity;
  return Number(text.replaceAll('_', ''));
}
export function metrics_config(overrides: Partial<MetricsConfig> = {}): MetricsConfig {
  const config = { ...DEFAULT_METRICS_CONFIG, ...overrides };
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === 'number' && (!Number.isFinite(value) || value <= 0))
      throw new MetricsValueError(`${key} must be positive`);
  }
  if (config.active_seconds > config.idle_seconds)
    throw new MetricsValueError('active_seconds must not exceed idle_seconds');
  if (!(
    config.raw_seconds <= config.minute_seconds && config.minute_seconds <= config.coarse_seconds
  ))
    throw new MetricsValueError('retention tiers must increase');
  if (config.process_snapshot_bytes > config.transaction_payload_bytes)
    throw new MetricsValueError('process snapshots must fit the transaction payload limit');
  if (config.transaction_payload_bytes > config.wal_bytes)
    throw new MetricsValueError('transactions must fit the WAL limit');
  return config;
}
export function load_metrics_config(environment: NodeJS.ProcessEnv = process.env): MetricsConfig {
  const overrides: Record<string, string | number> = {};
  for (const [key, fallback] of Object.entries(DEFAULT_METRICS_CONFIG)) {
    const raw = environment['PLAN_VPS_METRICS_' + key.toUpperCase()];
    if (raw === undefined) continue;
    if (typeof fallback === 'string') overrides[key] = raw;
    else {
      const integer = INTEGER_FIELDS.has(key);
      const value = configured_number(raw, integer);
      if (integer && !Number.isSafeInteger(value))
        throw new MetricsValueError(`${key} exceeds the supported exact integer range`);
      overrides[key] = value;
    }
  }
  return metrics_config(overrides as Partial<MetricsConfig>);
}
export async function store_path(application_database: string): Promise<string> {
  const database = await resolved_path(application_database);
  return join(dirname(database), basename(database) + '.vps-metrics.sqlite3');
}
