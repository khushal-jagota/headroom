export interface RebuildMeasurementRequest {
  root: string;
  database_path: string;
  config_path: string;
}

export interface RebuildMeasurement {
  node_version: string;
  sqlite_version: string;
  batch_events: number;
  record_runtime_initialize_ms: number;
  all_views_current_ms: number;
  record_runtime_close_ms: number;
  event_loop_delay_ms: {
    resolution: number;
    samples: number;
    p50: number;
    p95: number;
    p99: number;
    max: number;
  };
  heartbeat: { interval_ms: number; samples: number; max_gap_ms: number };
  poll_interval_ms: number;
  source: { conversations: number; events: number; event_kinds: Record<string, number> };
  projection: Record<string, number>;
  incomplete_conversations: number;
}
