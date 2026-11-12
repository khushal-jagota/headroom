/** Export-only measurement instrumentation; production replay and navigation stay unchanged. */
import { createHash } from 'node:crypto';
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { setTimeout as pause } from 'node:timers/promises';
import { createAreasRuntime } from '../../../areas.ts';
import { buildClock } from '../../../core/clock.ts';
import { loadConfig } from '../../../core/config.ts';
import { createDatabase } from '../../../core/database/index.ts';
import type {
  Database,
  ReadSql,
  SqlParameters,
  TransactionSql,
  WriteOptions,
} from '../../../core/database/contracts.ts';
import { acquireRuntimeOwnership } from '../../../core/locks/index.ts';
import { register_test_root } from '../../../core/test_paths.ts';
import { SignalPublicationCoordinator } from '../../publication/coordinator.ts';
import { PROJECTION_BACKFILL_BATCH } from '../../views/configuration.ts';
import { VIEW_PROJECTION_VERSION } from '../../views/contracts.ts';
import { out_of_date_conversation_ids } from '../../views/data/rebuild.ts';
import { createConversationRuntime } from '../runtime.ts';
import type {
  Distribution,
  PhaseProfile,
  RebuildProfile,
  RebuildProfileRequest,
  ScopeSample,
} from './rebuild_profile_contracts.ts';

const HEARTBEAT_MS = 5;
const POLL_MS = 10;
const PROFILE_TIMEOUT_MS = 900_000;
const VIEW_TABLES = ['state', 'rows', 'turns', 'groups', 'lookups'] as const;

function distribution(values: readonly number[]): Distribution {
  const ordered = [...values].sort((left, right) => left - right);
  const percentile = (p: number): number =>
    ordered[Math.max(0, Math.ceil((p * ordered.length) / 100) - 1)] ?? 0;
  return {
    count: values.length,
    total_ms: values.reduce((sum, value) => sum + value, 0),
    p50_ms: percentile(50),
    p95_ms: percentile(95),
    p99_ms: percentile(99),
    max_ms: ordered.at(-1) ?? 0,
    over_50_ms: values.filter((value) => value > 50).length,
    over_100_ms: values.filter((value) => value > 100).length,
    over_500_ms: values.filter((value) => value > 500).length,
  };
}

function projection_digest(database: Database): string {
  const hash = createHash('sha256');
  database.read((sql) => {
    for (const table of VIEW_TABLES) {
      hash.update(table + '\0');
      const order = table === 'lookups' ? 'conversation_id,lookup_kind,lookup_id' : '1,2';
      for (const row of sql.all(`SELECT * FROM conversation_view_${table} ORDER BY ${order}`)) {
        hash.update(JSON.stringify(row) + '\0');
      }
    }
  });
  return hash.digest('hex');
}

function instrument(database: Database) {
  const samples: ScopeSample[] = [];
  const statements = new Map<string, RebuildProfile['sql_by_total_time'][number]>();
  let enabled = false;
  let origin = 0;
  let default_phase = 'record-startup-or-control';
  function scoped<T>(
    operation: 'read' | 'write',
    work: (sql: TransactionSql) => T,
    options?: WriteOptions,
  ): T {
    if (!enabled) {
      return operation === 'write'
        ? database.write(work, options)
        : database.read((sql) => work(sql as TransactionSql));
    }
    const start = performance.now();
    const sample: ScopeSample = {
      operation,
      phase: default_phase,
      start_offset_ms: start - origin,
      duration_ms: 0,
      sql_read_ms: 0,
      sql_write_ms: 0,
      callback_residual_js_ms: 0,
      transaction_or_read_scope_overhead_ms: 0,
      replayed_events: 0,
      sql_calls: 0,
    };
    let callback_ms = 0;
    const callback = (sql: ReadSql | TransactionSql): T => {
      const started = performance.now();
      const call = <Row>(
        kind: 'get' | 'all' | 'run',
        statement: string,
        parameters?: SqlParameters,
      ): Row => {
        if (
          statement.includes('SELECT sequence,created_at,kind,payload FROM conversation_events') &&
          statement.includes('LIMIT')
        ) {
          sample.phase = 'replay-batch';
        } else if (statement.includes('INSERT INTO main.conversation_view_')) {
          sample.phase = 'atomic-view-install';
        } else if (
          statement.startsWith('CREATE TEMP TABLE') &&
          sample.phase !== 'atomic-view-install'
        ) {
          sample.phase = 'private-view-setup';
        }
        const at = performance.now();
        let value: unknown;
        try {
          if (kind === 'get') value = sql.get(statement, parameters);
          else if (kind === 'all') value = sql.all(statement, parameters);
          else value = (sql as TransactionSql).run(statement, parameters);
          return value as Row;
        } finally {
          const elapsed = performance.now() - at;
          if (kind === 'run') sample.sql_write_ms += elapsed;
          else sample.sql_read_ms += elapsed;
          sample.sql_calls++;
          if (
            kind === 'all' &&
            sample.phase === 'replay-batch' &&
            statement.includes('SELECT sequence,created_at,kind,payload FROM conversation_events')
          ) {
            sample.replayed_events += (value as unknown[] | undefined)?.length ?? 0;
          }
          const key = `${kind}\0${statement}`;
          let summary = statements.get(key);
          if (!summary) {
            summary = { sql: statement, operation: kind, calls: 0, total_ms: 0, max_ms: 0 };
            statements.set(key, summary);
          }
          summary.calls++;
          summary.total_ms += elapsed;
          summary.max_ms = Math.max(summary.max_ms, elapsed);
        }
      };
      const wrapped: TransactionSql = {
        get: <Row>(statement: string, parameters?: SqlParameters) =>
          call<Row | undefined>('get', statement, parameters),
        all: <Row>(statement: string, parameters?: SqlParameters) =>
          call<Row[]>('all', statement, parameters),
        run: (statement, parameters) => call('run', statement, parameters),
      };
      try {
        return work(wrapped);
      } finally {
        callback_ms += performance.now() - started;
      }
    };
    try {
      return operation === 'write' ? database.write(callback, options) : database.read(callback);
    } finally {
      sample.duration_ms = performance.now() - start;
      sample.callback_residual_js_ms = callback_ms - sample.sql_read_ms - sample.sql_write_ms;
      sample.transaction_or_read_scope_overhead_ms = sample.duration_ms - callback_ms;
      samples.push(sample);
    }
  }
  const wrapped: Database = {
    read: <T>(work: (sql: ReadSql) => T) => scoped('read', work),
    write: <T>(work: (sql: TransactionSql) => T, options?: WriteOptions) =>
      scoped('write', work, options),
    close: () => database.close(),
  };
  return {
    database: wrapped,
    begin() {
      origin = performance.now();
      enabled = true;
      return origin;
    },
    phase(value: string) {
      default_phase = value;
    },
    end() {
      enabled = false;
    },
    samples,
    statements,
  };
}

export async function profile_record_rebuild(
  request: RebuildProfileRequest,
): Promise<RebuildProfile> {
  const unregister = register_test_root(request.root);
  const config = loadConfig(request.config_path);
  if (!config.test_mode || config.db_path !== request.database_path)
    throw new Error('profile requires its explicit synthetic test database');
  const ownership = acquireRuntimeOwnership(request.database_path);
  const areas = request.mode === 'navigation-startup' ? createAreasRuntime() : null;
  const raw = createDatabase(request.database_path, { commitSteps: areas?.commitSteps });
  const profiler = instrument(raw);
  const database = profiler.database;
  const runtime = request.mode === 'navigation-startup' ? null : createConversationRuntime();
  const source = raw.read((sql) =>
    Object.fromEntries(
      [
        'conversations',
        'conversation_events',
        'tickets',
        'managers',
        'projects',
        'ticket_conversations',
        'agents',
      ].map((table) => [
        table,
        sql.get<{ total: number }>(`SELECT COUNT(*) AS total FROM ${table}`)!.total,
      ]),
    ),
  );
  const source_versions = raw.read((sql) =>
    sql
      .all<{ version: number }>(
        'SELECT DISTINCT projection_version AS version FROM conversation_view_state ORDER BY version',
      )
      .map((row) => row.version),
  );
  const before = projection_digest(raw);
  if (request.mode !== 'record-rebuild' && out_of_date_conversation_ids(raw).length > 0)
    throw new Error('navigation/reuse needs Python-written current-version view rows');
  const delay = monitorEventLoopDelay({ resolution: 1 });
  const gaps: number[] = [];
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let publication: SignalPublicationCoordinator | undefined;
  let ownership_current = true;
  const wall_phases: Record<string, number> = {};
  try {
    delay.enable();
    await pause(20);
    delay.reset();
    let last = performance.now();
    heartbeat = setInterval(() => {
      const now = performance.now();
      gaps.push(now - last);
      last = now;
    }, HEARTBEAT_MS);
    const started = profiler.begin();
    if (areas) {
      publication = new SignalPublicationCoordinator({
        database,
        require_runtime_ownership() {
          if (!ownership_current) throw new Error('profile ownership closed');
        },
      });
      areas.bindPublication(publication);
      let at = performance.now();
      profiler.phase('area-prepare');
      areas.prepare(database);
      wall_phases.area_prepare_ms = performance.now() - at;
      at = performance.now();
      profiler.phase('area-initialize-with-current-views');
      areas.initialize(database, config, buildClock(config));
      wall_phases.area_initialize_ms = performance.now() - at;
      at = performance.now();
      profiler.phase('signal-source-initialize');
      publication.bind_runtime(() => null);
      publication.initialize();
      wall_phases.signal_source_initialize_ms = performance.now() - at;
      at = performance.now();
      profiler.phase('navigation-attach-and-initialize');
      areas.attachNavigation();
      wall_phases.navigation_attach_ms = performance.now() - at;
    } else {
      const at = performance.now();
      profiler.phase('record-startup-or-control');
      runtime!.initialize(database, config, { integer_now: () => 1_800_000_000 });
      wall_phases.record_initialize_ms = performance.now() - at;
      while (out_of_date_conversation_ids(database).length > 0) {
        if (performance.now() - started > PROFILE_TIMEOUT_MS)
          throw new Error('profile exceeded fifteen minutes');
        await pause(POLL_MS);
      }
      wall_phases.all_views_current_ms = performance.now() - started;
    }
    const measured_wall = performance.now() - started;
    profiler.end();
    await pause(20);
    delay.disable();
    clearInterval(heartbeat);
    heartbeat = undefined;
    const phases: Record<string, PhaseProfile> = {};
    for (const phase of new Set(profiler.samples.map((sample) => sample.phase))) {
      const samples = profiler.samples.filter((sample) => sample.phase === phase);
      phases[phase] = {
        scopes: distribution(samples.map((sample) => sample.duration_ms)),
        sql_read_ms: samples.reduce((total, sample) => total + sample.sql_read_ms, 0),
        sql_write_ms: samples.reduce((total, sample) => total + sample.sql_write_ms, 0),
        callback_residual_js_ms: samples.reduce(
          (total, sample) => total + sample.callback_residual_js_ms,
          0,
        ),
        transaction_or_read_scope_overhead_ms: samples.reduce(
          (total, sample) => total + sample.transaction_or_read_scope_overhead_ms,
          0,
        ),
        replayed_events: samples.reduce((total, sample) => total + sample.replayed_events, 0),
      };
    }
    const synchronous = profiler.samples.reduce((total, sample) => total + sample.duration_ms, 0);
    return {
      mode: request.mode,
      node_version: process.version,
      sqlite_version: raw.read(
        (sql) => sql.get<{ version: string }>('SELECT sqlite_version() AS version')!.version,
      ),
      source,
      source_projection_versions: source_versions,
      projection_version: VIEW_PROJECTION_VERSION,
      phase_wall_ms: wall_phases,
      measured_wall_ms: measured_wall,
      synchronous_database_scope_ms: synchronous,
      outside_database_yield_pacing_and_control_ms: measured_wall - synchronous,
      phases,
      slowest_scopes: [...profiler.samples]
        .sort((left, right) => right.duration_ms - left.duration_ms)
        .slice(0, 20),
      replay_batches: distribution(
        profiler.samples
          .filter((sample) => sample.phase === 'replay-batch')
          .map((sample) => sample.duration_ms),
      ),
      install_transactions: distribution(
        profiler.samples
          .filter((sample) => sample.phase === 'atomic-view-install')
          .map((sample) => sample.duration_ms),
      ),
      heartbeat: { interval_ms: HEARTBEAT_MS, ...distribution(gaps) },
      event_loop_delay_ms: {
        p50: delay.percentile(50) / 1_000_000,
        p95: delay.percentile(95) / 1_000_000,
        p99: delay.percentile(99) / 1_000_000,
        max: delay.max / 1_000_000,
        samples: delay.count,
      },
      sql_by_total_time: [...profiler.statements.values()]
        .sort((left, right) => right.total_ms - left.total_ms)
        .slice(0, 30),
      replayed_events: profiler.samples.reduce(
        (total, sample) => total + sample.replayed_events,
        0,
      ),
      replay_batch_events: PROJECTION_BACKFILL_BATCH,
      projection_current: out_of_date_conversation_ids(raw).length === 0,
      projection_rows_digest_before: before,
      projection_rows_digest_after: projection_digest(raw),
      navigation: raw.read((sql) =>
        Object.fromEntries(
          ['navigation_records', 'navigation_view_ordered', 'navigation_signal_publications'].map(
            (table) => [
              table,
              sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [table])
                ? sql.get<{ total: number }>(`SELECT COUNT(*) AS total FROM ${table}`)!.total
                : null,
            ],
          ),
        ),
      ),
      limitations: [
        'Profiling wrappers and per-SQL timers add overhead. Do not compare its wall time directly with the earlier plain measurement.',
        'SQL method time includes driver preparation, execution, and row materialization. Residual callback time is JS reduction/control plus instrumentation, not pure reducer time.',
        'Scope overhead includes BEGIN/COMMIT, write-door checks, and unwrapped area commit steps in navigation mode.',
        'Outside synchronous database scopes combines yield, production pacing, timer scheduling, and other control work. It is not measured pure idle time.',
        'Record-only replay omits area commit steps. Navigation mode uses actual area commit steps and current Python-written projections, with no conversation replay.',
        'The canonical fixture has completed synthetic turns and no linked Ticket or Manager conversations. Navigation results do not represent a populated application workload.',
        'No production providers, browser, HTTP listener, live data, or concurrent external writer participates.',
      ],
    };
  } finally {
    profiler.end();
    if (heartbeat !== undefined) clearInterval(heartbeat);
    delay.disable();
    publication?.invalidate('shutdown');
    if (areas) {
      await areas.closeRecord();
      areas.detachNavigation();
    } else {
      await runtime!.close();
    }
    raw.close();
    ownership_current = false;
    ownership.close();
    unregister();
  }
}
