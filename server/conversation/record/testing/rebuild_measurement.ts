/** Export-only probe of the production record runtime and its unchanged rebuilder. */
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { setTimeout as pause } from 'node:timers/promises';
import { loadConfig } from '../../../core/config.ts';
import { createDatabase } from '../../../core/database/index.ts';
import { register_test_root } from '../../../core/test_paths.ts';
import { PROJECTION_BACKFILL_BATCH } from '../../views/configuration.ts';
import { out_of_date_conversation_ids } from '../../views/data/rebuild.ts';
import { createConversationRuntime } from '../runtime.ts';
import type {
  RebuildMeasurement,
  RebuildMeasurementRequest,
} from './rebuild_measurement_contracts.ts';

const HISTOGRAM_RESOLUTION_MS = 1;
const HEARTBEAT_INTERVAL_MS = 5;
const POLL_INTERVAL_MS = 10;
const MEASUREMENT_TIMEOUT_MS = 900_000;

export async function measure_record_rebuild(
  request: RebuildMeasurementRequest,
): Promise<RebuildMeasurement> {
  const unregister = register_test_root(request.root);
  const config = loadConfig(request.config_path);
  if (config.db_path !== request.database_path || !config.test_mode) {
    unregister();
    throw new Error('rebuild measurement needs its explicit synthetic test database');
  }
  const database = createDatabase(request.database_path);
  const runtime = createConversationRuntime();
  const delay = monitorEventLoopDelay({ resolution: HISTOGRAM_RESOLUTION_MS });
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let closed = false;
  try {
    const source = database.read((sql) => ({
      conversations: sql.get<{ total: number }>('SELECT COUNT(*) AS total FROM conversations')!
        .total,
      events: sql.get<{ total: number }>('SELECT COUNT(*) AS total FROM conversation_events')!
        .total,
      event_kinds: Object.fromEntries(
        sql
          .all<{ kind: string; total: number }>(
            'SELECT kind,COUNT(*) AS total FROM conversation_events GROUP BY kind ORDER BY kind',
          )
          .map(({ kind, total }) => [kind, total]),
      ),
    }));
    delay.enable();
    await pause(20);
    delay.reset();
    let last_heartbeat = performance.now();
    let heartbeat_samples = 0;
    let maximum_gap = 0;
    heartbeat = setInterval(() => {
      const now = performance.now();
      maximum_gap = Math.max(maximum_gap, now - last_heartbeat);
      last_heartbeat = now;
      heartbeat_samples++;
    }, HEARTBEAT_INTERVAL_MS);
    const started = performance.now();
    runtime.initialize(database, config, { integer_now: () => 1_800_000_000 });
    runtime.start_rebuild();
    const initialized = performance.now();
    while (out_of_date_conversation_ids(database).length > 0) {
      if (performance.now() - started > MEASUREMENT_TIMEOUT_MS) {
        throw new Error('record rebuild measurement exceeded fifteen minutes');
      }
      await pause(POLL_INTERVAL_MS);
    }
    const current = performance.now();
    // Let the final blocking install reach both instruments before they stop.
    await pause(20);
    delay.disable();
    clearInterval(heartbeat);
    heartbeat = undefined;
    const projection = database.read((sql) =>
      Object.fromEntries(
        ['state', 'rows', 'turns', 'groups', 'lookups'].map((table) => [
          table,
          sql.get<{ total: number }>(`SELECT COUNT(*) AS total FROM conversation_view_${table}`)!
            .total,
        ]),
      ),
    );
    const incomplete = database.read(
      (sql) =>
        sql.get<{ total: number }>(
          'SELECT COUNT(*) AS total FROM conversations c LEFT JOIN conversation_view_state v ' +
            'ON v.conversation_id=c.conversation_id WHERE v.through_sequence IS NULL ' +
            'OR v.through_sequence!=c.latest_sequence',
        )!.total,
    );
    const closing = performance.now();
    await runtime.close();
    closed = true;
    return {
      node_version: process.version,
      sqlite_version: database.read(
        (sql) => sql.get<{ version: string }>('SELECT sqlite_version() AS version')!.version,
      ),
      batch_events: PROJECTION_BACKFILL_BATCH,
      record_runtime_initialize_ms: initialized - started,
      all_views_current_ms: current - started,
      record_runtime_close_ms: performance.now() - closing,
      event_loop_delay_ms: {
        resolution: HISTOGRAM_RESOLUTION_MS,
        samples: delay.count,
        p50: delay.percentile(50) / 1_000_000,
        p95: delay.percentile(95) / 1_000_000,
        p99: delay.percentile(99) / 1_000_000,
        max: delay.max / 1_000_000,
      },
      heartbeat: {
        interval_ms: HEARTBEAT_INTERVAL_MS,
        samples: heartbeat_samples,
        max_gap_ms: maximum_gap,
      },
      poll_interval_ms: POLL_INTERVAL_MS,
      source,
      projection,
      incomplete_conversations: incomplete,
    };
  } finally {
    if (heartbeat !== undefined) clearInterval(heartbeat);
    delay.disable();
    if (!closed) await runtime.close();
    database.close();
    unregister();
  }
}
