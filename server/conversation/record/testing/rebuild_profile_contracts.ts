import type { RebuildMeasurementRequest } from './rebuild_measurement_contracts.ts';

export interface RebuildProfileRequest extends RebuildMeasurementRequest {
  mode: 'record-rebuild' | 'navigation-startup' | 'reuse-check';
}

export interface Distribution {
  count: number;
  total_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
  over_50_ms: number;
  over_100_ms: number;
  over_500_ms: number;
}

export interface ScopeSample {
  operation: 'read' | 'write';
  phase: string;
  start_offset_ms: number;
  duration_ms: number;
  sql_read_ms: number;
  sql_write_ms: number;
  callback_residual_js_ms: number;
  transaction_or_read_scope_overhead_ms: number;
  replayed_events: number;
  sql_calls: number;
}

export interface PhaseProfile {
  scopes: Distribution;
  sql_read_ms: number;
  sql_write_ms: number;
  callback_residual_js_ms: number;
  transaction_or_read_scope_overhead_ms: number;
  replayed_events: number;
}

export interface RebuildProfile {
  mode: RebuildProfileRequest['mode'];
  node_version: string;
  sqlite_version: string;
  source: Record<string, number>;
  source_projection_versions: number[];
  projection_version: number;
  phase_wall_ms: Record<string, number>;
  measured_wall_ms: number;
  synchronous_database_scope_ms: number;
  outside_database_yield_pacing_and_control_ms: number;
  phases: Record<string, PhaseProfile>;
  slowest_scopes: ScopeSample[];
  replay_batches: Distribution;
  install_transactions: Distribution;
  heartbeat: Distribution & { interval_ms: number };
  event_loop_delay_ms: { p50: number; p95: number; p99: number; max: number; samples: number };
  sql_by_total_time: {
    sql: string;
    operation: 'get' | 'all' | 'run';
    calls: number;
    total_ms: number;
    max_ms: number;
  }[];
  replayed_events: number;
  replay_batch_events: number;
  projection_current: boolean;
  projection_rows_digest_before: string;
  projection_rows_digest_after: string;
  navigation: Record<string, number | null>;
  limitations: string[];
}
