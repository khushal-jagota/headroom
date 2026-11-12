import type { JsonNumber } from '../../core/http/numeric_json.ts';
import type { ReadSql } from '../../core/database/contracts.ts';

export type MetricNumber = number | JsonNumber;
export type Availability = 'available' | 'partial' | 'unavailable';
export interface MetricsErrorWire {
  name: string;
  message: string;
  code?: string;
}
export type Resolution = 'raw' | 'minute' | 'coarse';
export interface Measurement {
  value: MetricNumber | null;
  unit: string;
  reason: string | null;
  sampled_at?: MetricNumber;
  numerator?: MetricNumber | null;
  duration?: MetricNumber | null;
}
export interface ProcessRecord {
  identity: { boot_id: string; pid: MetricNumber; start_ticks: MetricNumber };
  parent_pid: MetricNumber;
  uid: MetricNumber | null;
  name: string;
  state: string;
  service: string;
  metrics: Record<string, Measurement>;
}
export interface ServiceRecord {
  identity: string;
  name: string;
  kind: string;
  metrics: Record<string, Measurement>;
  state: string | null;
  state_reason: string | null;
  process_count: MetricNumber;
}
export interface Sample {
  timestamp: MetricNumber;
  monotonic: MetricNumber;
  boot_id: string;
  expected_interval: MetricNumber;
  core_count: MetricNumber;
  metrics: Record<string, Measurement>;
  services: ServiceRecord[];
  processes: ProcessRecord[];
  process_count: MetricNumber;
  process_availability: Availability;
  process_reason: string | null;
  disk_total_members: string[];
  network_total_members: string[];
}
export interface AggregateStatistics {
  weighted_sum: MetricNumber;
  valid_duration: MetricNumber;
  expected_duration: MetricNumber;
  minimum: MetricNumber | null;
  maximum: MetricNumber | null;
  count: MetricNumber;
  numerator: MetricNumber | null;
  denominator: MetricNumber | null;
}
export interface AggregateEntry {
  unit: string;
  statistics: AggregateStatistics;
}
export interface RetentionCoverage {
  requested_start: MetricNumber | null;
  retained_start: MetricNumber | null;
  retained_end: MetricNumber | null;
  budget_evicted: boolean;
  policy_seconds: MetricNumber;
}
export interface CollectionStatus {
  availability: Availability;
  reason: string | null;
  last_sample_at: MetricNumber | null;
  last_attempt_at: MetricNumber | null;
  cadence_seconds: MetricNumber;
}
export interface LatestResponse {
  sequence: MetricNumber | null;
  sample: Sample | null;
  collection: CollectionStatus;
  coverage: RetentionCoverage;
}
export interface ProcessPage {
  sequence: number | null;
  timestamp: MetricNumber | null;
  processes: ProcessRecord[];
  total: MetricNumber;
  offset: number;
  next_offset: number | null;
  availability: Availability;
  reason: string | null;
  coverage: RetentionCoverage;
}
export interface HistoryPoint {
  timestamp: MetricNumber;
  end: MetricNumber;
  boot_id: string;
  value: MetricNumber | null;
  unit: string;
  reason: string | null;
  statistics: AggregateStatistics | null;
}
export interface HistoryResponse {
  signal: string;
  entity: string | null;
  resolution: Resolution;
  points: HistoryPoint[];
  coverage: RetentionCoverage;
}
export interface HistoryQuery {
  signal: string;
  entity: string | null;
  start: number;
  end: number;
  resolution: Resolution;
}
export interface ProcessQuery {
  sequence: number | null;
  offset: number;
  limit: number;
}
export interface Collector {
  collect(options: { include_slow: boolean }): Sample | Promise<Sample>;
}
export interface StoreReads {
  latest(sql: ReadSql, now: number, deadline?: bigint): LatestResponse;
  processes(sql: ReadSql, now: number, query: ProcessQuery, deadline?: bigint): ProcessPage;
  history(sql: ReadSql, query: HistoryQuery, deadline?: bigint): HistoryResponse;
}
export interface ProgressMarker {
  timestamp: MetricNumber;
  monotonic: MetricNumber;
  boot_id: string;
}
export interface ProcessSnapshot {
  processes: ProcessRecord[];
  total: MetricNumber;
  availability: Availability;
  reason: string | null;
}
export class SnapshotExpiredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SnapshotExpiredError';
  }
}
export class QueryLimitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'QueryLimitError';
  }
}
export class MetricsOperationalError extends Error {
  readonly code = 'SQLITE_ERROR';
  constructor(message: string) {
    super(message);
    this.name = 'OperationalError';
  }
}
export class MetricsValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}

export interface MetricsStore extends StoreReads {
  initialize(): void;
  append(sample: Sample): number;
  prune(now: number): void;
  renew_activity(now: MetricNumber): number;
  activity_until(): number;
  record_failure(now: MetricNumber, reason: string, cadence_seconds: MetricNumber): void;
  close(): void;
}
