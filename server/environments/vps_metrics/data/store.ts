import { existsSync, statSync } from 'node:fs';
import { createAuxiliaryDatabase, createReadAccess } from '../../../core/database/index.ts';
import type {
  AuxiliaryDatabase,
  AuxiliaryTransactionSql,
  ReadAccess,
  ReadSql,
  TransactionSql,
} from '../../../core/database/contracts.ts';
import type {
  AggregateEntry,
  CollectionStatus,
  HistoryPoint,
  HistoryQuery,
  HistoryResponse,
  LatestResponse,
  MetricNumber,
  MetricsStore,
  ProcessPage,
  ProcessQuery,
  ProcessSnapshot,
  ProgressMarker,
  Resolution,
  RetentionCoverage,
  Sample,
} from '../contracts.ts';
import { MetricsOperationalError, QueryLimitError, SnapshotExpiredError } from '../contracts.ts';
import { DEFAULT_METRICS_CONFIG } from '../configuration.ts';
import type { MetricsConfig } from '../configuration.ts';
import { decode, encode, float, number, pack, unpack } from '../codec.ts';
import {
  accumulate,
  bucket_intervals,
  empty_statistics,
  interval_start,
  signals,
} from '../logic/aggregation.ts';
import { initialize_metrics_schema } from './schema.ts';

function put(sql: TransactionSql, key: string, value: unknown): void {
  sql.run('INSERT OR REPLACE INTO metadata VALUES(?,?)', [key, encode(value)]);
}
function get<T>(sql: ReadSql, key: string, fallback: T): T {
  const row = sql.get<{ value: string }>('SELECT value FROM metadata WHERE key=?', [key]);
  return row ? decode<T>(row.value) : fallback;
}
function current_time(): bigint {
  return process.hrtime.bigint();
}
export class Store implements MetricsStore {
  readonly path: string;
  readonly config: MetricsConfig;
  private owned: AuxiliaryDatabase | undefined;
  private activity_reader: ReadAccess | undefined;
  constructor(path: string, config: MetricsConfig = { ...DEFAULT_METRICS_CONFIG }) {
    this.path = path;
    this.config = config;
  }
  initialize(): void {
    if (this.owned) return;
    try {
      this.owned = createAuxiliaryDatabase(this.path, {
        schemaVersion: 2,
        initialize: initialize_metrics_schema,
        maxBytes: this.config.database_bytes,
        busyTimeoutMs: 500,
      });
      this.activity_reader = createReadAccess(this.path, { busyTimeoutMs: 100 });
    } catch (error) {
      this.owned?.close();
      this.owned = undefined;
      if (
        error instanceof Error &&
        error.message.startsWith('unsupported auxiliary database schema:')
      )
        throw new Error('unsupported VPS metrics schema');
      throw error;
    }
  }
  close(): void {
    this.activity_reader?.close();
    this.activity_reader = undefined;
    this.owned?.close();
    this.owned = undefined;
  }
  private database(): AuxiliaryDatabase {
    if (!this.owned) throw new Error('VPS metrics store is not initialized');
    return this.owned;
  }
  private checkpoint(): void {
    const wal = this.path + '-wal';
    if (existsSync(wal) && statSync(wal).size >= this.config.wal_bytes) {
      const result = this.database().checkpoint('TRUNCATE', { busyTimeoutMs: 100 });
      if (result.busy || (existsSync(wal) && statSync(wal).size >= this.config.wal_bytes))
        throw new MetricsOperationalError('VPS metrics WAL backpressure: checkpoint blocked');
    }
  }
  private write<T>(work: (sql: AuxiliaryTransactionSql) => T): T {
    this.checkpoint();
    return this.database().write(work, { changeSignal: 'silent' });
  }
  append(sample: Sample): number {
    this.checkpoint();
    const { processes, ...remaining } = sample;
    const current: Sample = { ...remaining, processes: [] };
    const snapshot: ProcessSnapshot = {
      processes,
      total: sample.process_count,
      availability: sample.process_availability,
      reason: sample.process_reason,
    };
    let process_encoded = encode(snapshot);
    if (Buffer.byteLength(process_encoded) > this.config.process_snapshot_bytes) {
      snapshot.processes = [];
      snapshot.availability = 'unavailable';
      snapshot.reason = 'complete process snapshot exceeds byte limit';
      process_encoded = encode(snapshot);
    }
    current.process_availability = snapshot.availability;
    current.process_reason = snapshot.reason;
    const payload = pack(current),
      process_payload = pack(snapshot);
    const payload_bytes = payload.length,
      latest_bytes = Buffer.byteLength(encode(current)),
      process_bytes = process_payload.length;
    if (latest_bytes > this.config.metrics_payload_bytes)
      throw new MetricsOperationalError('VPS metrics latest exceeds metrics payload budget');
    if (latest_bytes + payload_bytes + process_bytes > this.config.transaction_payload_bytes)
      throw new MetricsOperationalError('VPS metrics sample exceeds transaction byte limit');
    return this.write((sql) => {
      const duplicate = sql.get<{ seq: number }>(
        'SELECT seq FROM samples WHERE boot=? AND monotonic=?',
        [sample.boot_id, number(sample.monotonic)],
      );
      if (duplicate) return duplicate.seq;
      const previous = get<ProgressMarker | null>(sql, 'previous', null);
      if (
        previous &&
        previous.boot_id === sample.boot_id &&
        number(previous.monotonic) >= number(sample.monotonic)
      )
        return number(get<MetricNumber>(sql, 'sequence', 0));
      const inserted = sql.run(
        'INSERT INTO samples(timestamp,boot,monotonic,payload,bytes) VALUES(?,?,?,?,?)',
        [
          number(sample.timestamp),
          sample.boot_id,
          number(sample.monotonic),
          payload,
          payload_bytes,
        ],
      );
      const sequence = Number(inserted.lastInsertRowid);
      sql.run('INSERT INTO snapshots VALUES(?,?,?,?)', [
        sequence,
        number(sample.timestamp),
        process_payload,
        process_bytes,
      ]);
      let encoded_transaction = latest_bytes + payload_bytes + process_bytes;
      const previous_sample = get<Sample | null>(sql, 'latest', null);
      const previous_signals = previous_sample ? signals(previous_sample) : {};
      for (const [resolution, width] of [
        ['minute', 60],
        ['coarse', 900],
      ] as const) {
        const buckets = new Map<
          string,
          { bucket: number; entity: string; entries: Record<string, AggregateEntry> }
        >();
        for (const [entity, metrics] of Object.entries(signals(sample))) {
          for (const [signal, measurement] of Object.entries(metrics)) {
            let metric_end = number(sample.timestamp),
              metric_start = interval_start(sample, previous);
            const sampled_at = measurement.sampled_at;
            if (sampled_at !== undefined) {
              const old = previous_signals[entity]?.[signal],
                old_at = old?.sampled_at;
              if (
                !previous ||
                previous.boot_id !== sample.boot_id ||
                old_at === undefined ||
                number(sampled_at) <= number(old_at)
              )
                continue;
              metric_end = number(sampled_at);
              metric_start =
                metric_end - Math.min(metric_end - number(old_at), this.config.slow_seconds);
            }
            const duration = measurement.duration;
            if (duration !== undefined && duration !== null)
              metric_start = Math.max(metric_start, metric_end - Math.max(0, number(duration)));
            for (const [bucket, span] of bucket_intervals(metric_start, metric_end, width)) {
              const key = JSON.stringify([bucket, entity]);
              let stored = buckets.get(key);
              if (!stored) {
                const row = sql.get<{ payload: Buffer }>(
                  'SELECT payload FROM aggregates WHERE resolution=? AND bucket=? AND boot=? AND entity=?',
                  [resolution, bucket, sample.boot_id, entity],
                );
                stored = {
                  bucket,
                  entity,
                  entries: row ? unpack<Record<string, AggregateEntry>>(row.payload) : {},
                };
                buckets.set(key, stored);
              }
              const entry = (stored.entries[signal] ??= {
                unit: measurement.unit,
                statistics: empty_statistics(),
              });
              accumulate(entry.statistics, measurement, span);
            }
          }
        }
        for (const { bucket, entity, entries } of buckets.values()) {
          const aggregate_payload = pack(entries),
            size = aggregate_payload.length;
          encoded_transaction += size;
          if (encoded_transaction > this.config.transaction_payload_bytes)
            throw new MetricsOperationalError(
              'VPS metrics aggregate exceeds transaction byte limit',
            );
          sql.run(
            'INSERT INTO aggregates VALUES(?,?,?,?,?,?) ON CONFLICT(resolution,bucket,boot,entity) DO UPDATE SET payload=excluded.payload,bytes=excluded.bytes',
            [resolution, bucket, sample.boot_id, entity, aggregate_payload, size],
          );
        }
      }
      put(sql, 'previous', {
        timestamp: sample.timestamp,
        monotonic: sample.monotonic,
        boot_id: sample.boot_id,
      });
      put(sql, 'sequence', sequence);
      put(sql, 'collection', {
        availability: 'available',
        reason: null,
        last_sample_at: sample.timestamp,
        last_attempt_at: sample.timestamp,
        cadence_seconds: sample.expected_interval,
      });
      put(sql, 'latest', current);
      put(sql, 'latest_bytes', latest_bytes);
      this.prune_in_transaction(sql, number(sample.timestamp));
      return sequence;
    });
  }
  private prune_in_transaction(sql: TransactionSql, now: number): void {
    sql.run('DELETE FROM samples WHERE timestamp<?', [now - this.config.raw_seconds]);
    sql.run('DELETE FROM snapshots WHERE timestamp<?', [now - this.config.process_seconds]);
    for (const [resolution, width, retention] of [
      ['minute', 60, this.config.minute_seconds],
      ['coarse', 900, this.config.coarse_seconds],
    ] as const)
      sql.run('DELETE FROM aggregates WHERE resolution=? AND bucket<=?', [
        resolution,
        now - retention - width,
      ]);
    for (const [table, budget_value] of [
      ['snapshots', this.config.process_payload_bytes],
      ['metrics', this.config.metrics_payload_bytes],
    ] as const) {
      if (table === 'snapshots') {
        let total = sql.get<{ bytes: number }>(
          "SELECT bytes FROM payload_totals WHERE kind='snapshots'",
        )!.bytes;
        const rows =
          total > budget_value
            ? sql.all<{ seq: number; bytes: number }>(
                'SELECT seq,bytes FROM snapshots ORDER BY timestamp,seq',
              )
            : [];
        for (const row of rows) {
          if (total <= budget_value) break;
          sql.run('DELETE FROM snapshots WHERE seq=?', [row.seq]);
          total -= row.bytes;
          put(sql, 'process_budget_evicted', true);
        }
      } else {
        const budget = Math.max(
          0,
          budget_value - number(get<MetricNumber>(sql, 'latest_bytes', 0)),
        );
        let total = sql.get<{ bytes: number }>(
          "SELECT bytes FROM payload_totals WHERE kind='metrics'",
        )!.bytes;
        if (total > budget) {
          const rows = sql.all<{
            timestamp: number;
            bytes: number;
            resolution: string;
            key: number;
            boot: string;
          }>(
            "SELECT timestamp,bytes,'raw' AS resolution,seq AS key,'' AS boot FROM samples UNION ALL SELECT bucket,SUM(bytes),resolution,bucket,boot FROM aggregates GROUP BY resolution,bucket,boot ORDER BY 1",
          );
          for (const row of rows) {
            if (total <= budget) break;
            if (row.resolution === 'raw') sql.run('DELETE FROM samples WHERE seq=?', [row.key]);
            else
              sql.run('DELETE FROM aggregates WHERE resolution=? AND bucket=? AND boot=?', [
                row.resolution,
                row.key,
                row.boot,
              ]);
            total -= row.bytes;
            put(sql, 'metrics_budget_evicted', true);
          }
        }
      }
    }
  }
  prune(now: number): void {
    this.checkpoint();
    this.write((sql) => {
      this.prune_in_transaction(sql, now);
      sql.incrementalVacuum(128);
    });
    this.database().checkpoint('PASSIVE', { busyTimeoutMs: 100 });
  }
  renew_activity(now: MetricNumber): number {
    return this.write((sql) => {
      const until = Math.max(
        number(now) + this.config.activity_seconds,
        number(get<MetricNumber>(sql, 'activity', float(0))),
      );
      put(sql, 'activity', float(until));
      return until;
    });
  }
  activity_until(): number {
    if (!this.activity_reader) throw new Error('VPS metrics store is not initialized');
    return this.activity_reader.read((sql) => number(get<MetricNumber>(sql, 'activity', float(0))));
  }
  record_failure(now: MetricNumber, reason: string, cadence_seconds: MetricNumber): void {
    this.write((sql) => {
      const status = get<Partial<CollectionStatus>>(sql, 'collection', {});
      put(sql, 'collection', {
        availability: 'unavailable',
        reason,
        last_sample_at: status.last_sample_at ?? null,
        last_attempt_at: now,
        cadence_seconds,
      });
    });
  }
  private read<T>(operation: (check: () => void) => T, deadline?: bigint): T {
    const until = deadline ?? current_time() + BigInt(Math.ceil(this.config.read_seconds * 1e9));
    const check = () => {
      if (current_time() > until)
        throw new MetricsOperationalError('VPS metrics read deadline exceeded');
    };
    check();
    const result = operation(check);
    check();
    return result;
  }
  private coverage(
    sql: ReadSql,
    table: 'samples' | 'snapshots' | 'aggregates',
    policy: number,
    requested: number | null = null,
    resolution?: Resolution,
    entity?: string | null,
  ): RetentionCoverage {
    const column = table === 'aggregates' ? 'bucket' : 'timestamp';
    const row = sql.get<{ first: number | null; last: number | null }>(
      `SELECT MIN(${column}) AS first,MAX(${column}) AS last FROM ${table}${resolution ? ' WHERE resolution=? AND entity=?' : ''}`,
      resolution ? [resolution, entity ?? ''] : [],
    )!;
    return {
      requested_start: requested,
      retained_start: row.first,
      retained_end: row.last,
      policy_seconds: policy,
      budget_evicted: get(
        sql,
        table === 'snapshots' ? 'process_budget_evicted' : 'metrics_budget_evicted',
        false,
      ),
    };
  }
  latest(sql: ReadSql, now: number, deadline?: bigint): LatestResponse {
    return this.read(() => {
      const collection = get<CollectionStatus>(sql, 'collection', {
        availability: 'unavailable',
        reason: 'no sample yet',
        last_sample_at: null,
        last_attempt_at: null,
        cadence_seconds: float(this.config.idle_seconds),
      });
      const timestamp = collection.last_sample_at,
        allowance = number(collection.cadence_seconds) + this.config.activity_check_seconds;
      if (
        timestamp !== null &&
        now - number(timestamp) > allowance &&
        collection.availability === 'available'
      ) {
        collection.availability = 'unavailable';
        collection.reason = 'stored sample is overdue';
      }
      return {
        sequence: get<MetricNumber | null>(sql, 'sequence', null),
        sample: get<Sample | null>(sql, 'latest', null),
        collection,
        coverage: this.coverage(sql, 'samples', this.config.raw_seconds),
      };
    }, deadline);
  }
  processes(sql: ReadSql, _now: number, query: ProcessQuery, deadline?: bigint): ProcessPage {
    const { sequence, offset, limit } = query;
    if (offset < 0 || limit < 1 || limit > this.config.process_page_rows)
      throw new QueryLimitError('invalid process page bounds');
    return this.read(() => {
      const row =
        sequence === null
          ? sql.get<{ seq: number; timestamp: number; payload: Buffer }>(
              'SELECT seq,timestamp,payload FROM snapshots ORDER BY seq DESC LIMIT 1',
            )
          : sql.get<{ seq: number; timestamp: number; payload: Buffer }>(
              'SELECT seq,timestamp,payload FROM snapshots WHERE seq=?',
              [sequence],
            );
      if (!row && sequence !== null) throw new SnapshotExpiredError('process snapshot expired');
      const snapshot: ProcessSnapshot = row
        ? unpack<ProcessSnapshot>(row.payload)
        : { processes: [], total: 0, availability: 'unavailable', reason: 'no process snapshot' };
      const processes = snapshot.processes;
      return {
        sequence: row?.seq ?? null,
        timestamp: row?.timestamp ?? null,
        processes: processes.slice(offset, offset + limit),
        total: snapshot.total,
        offset,
        next_offset: offset + limit < processes.length ? offset + limit : null,
        availability: snapshot.availability,
        reason: snapshot.reason,
        coverage: this.coverage(sql, 'snapshots', this.config.process_seconds),
      };
    }, deadline);
  }
  history(sql: ReadSql, query: HistoryQuery, deadline?: bigint): HistoryResponse {
    const { signal, entity, start, end, resolution } = query;
    const policy = {
      raw: this.config.raw_seconds,
      minute: this.config.minute_seconds,
      coarse: this.config.coarse_seconds,
    }[resolution];
    if (end <= start || end - start > policy)
      throw new QueryLimitError('history range exceeds retention policy');
    return this.read((check) => {
      const rows =
        resolution === 'raw'
          ? sql.all<{ timestamp: number; boot: string; payload: Buffer }>(
              'SELECT timestamp,boot,payload FROM samples WHERE timestamp>=? AND timestamp<? ORDER BY timestamp LIMIT ?',
              [start, end, this.config.history_points + 1],
            )
          : sql.all<{ timestamp: number; boot: string; payload: Buffer }>(
              'SELECT bucket AS timestamp,boot,payload FROM aggregates WHERE resolution=? AND entity=? AND bucket>=? AND bucket<? ORDER BY bucket LIMIT ?',
              [resolution, entity ?? '', start, end, this.config.history_points + 1],
            );
      if (rows.length > this.config.history_points)
        throw new QueryLimitError('history point count exceeds limit');
      const points: HistoryPoint[] = [];
      for (const row of rows) {
        check();
        if (resolution === 'raw') {
          const measurement = signals(unpack<Sample>(row.payload))[entity ?? '']?.[signal];
          if (!measurement) continue;
          points.push({
            timestamp: row.timestamp,
            end: row.timestamp,
            boot_id: row.boot,
            value: measurement.value,
            unit: measurement.unit,
            reason: measurement.reason,
            statistics: null,
          });
        } else {
          const entry = unpack<Record<string, AggregateEntry>>(row.payload)[signal];
          if (!entry) continue;
          const stats = entry.statistics,
            value = number(stats.valid_duration)
              ? number(stats.weighted_sum) / number(stats.valid_duration)
              : null;
          points.push({
            timestamp: row.timestamp,
            end: row.timestamp + (resolution === 'minute' ? 60 : 900),
            boot_id: row.boot,
            value,
            unit: entry.unit,
            reason: value === null ? 'no valid coverage' : null,
            statistics: stats,
          });
        }
      }
      return {
        signal,
        entity,
        resolution,
        points,
        coverage: this.coverage(
          sql,
          resolution === 'raw' ? 'samples' : 'aggregates',
          policy,
          start,
          resolution === 'raw' ? undefined : resolution,
          entity,
        ),
      };
    }, deadline);
  }
}
