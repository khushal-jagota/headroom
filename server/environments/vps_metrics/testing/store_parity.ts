/** Export-only direct Python comparison. No HTTP host or production test route. */
import { statSync } from 'node:fs';
import { register_test_root } from '../../../core/test_paths.ts';
import { createReadAccess, createAuxiliaryDatabase } from '../../../core/database/index.ts';
import type { ReadSql } from '../../../core/database/contracts.ts';
import { JsonNumber } from '../../../core/http/numeric_json.ts';
import type { MetricsConfig } from '../configuration.ts';
import { metrics_config } from '../configuration.ts';
import { decode, encode, number } from '../codec.ts';
import type { MetricNumber, Sample } from '../contracts.ts';
import { Store } from '../data/store.ts';
import { counter_rate, host_cpu_percent } from '../logic/rates.ts';

interface Operation {
  op: string;
  [key: string]: unknown;
}
interface Request {
  root: string;
  database: string;
  config: Partial<MetricsConfig>;
  operations: Operation[];
  rates?: {
    current: MetricNumber;
    previous: MetricNumber | null;
    elapsed: MetricNumber;
    same_identity: boolean;
    unit: string;
    scale?: MetricNumber;
  }[];
  cpus?: { current: MetricNumber[]; previous: MetricNumber[] | null; same_identity: boolean }[];
}
function plain(value: unknown): unknown {
  if (value instanceof JsonNumber) return value.value;
  if (Array.isArray(value)) return value.map(plain);
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, plain(item)]));
  return value;
}
function rows(sql: ReadSql): Record<string, unknown[]> {
  return Object.fromEntries(
    Object.entries({
      metadata: 'key',
      samples: 'seq',
      snapshots: 'seq',
      aggregates: 'resolution,bucket,boot,entity',
      payload_totals: 'kind',
    }).map(([table, order]) => [
      table,
      sql
        .all<Record<string, unknown>>(`SELECT * FROM ${table} ORDER BY ${order}`)
        .map((row) =>
          Object.fromEntries(
            Object.entries(row).map(([key, value]) => [
              key,
              Buffer.isBuffer(value) ? { hex: value.toString('hex') } : value,
            ]),
          ),
        ),
    ]),
  );
}
function error_result(error: unknown): { type: string; message: string } {
  const value = error as Error & { code?: string };
  return {
    type: value.code?.startsWith('SQLITE_') ? 'OperationalError' : value.name,
    message: value.message,
  };
}
export function metrics_store_parity(input: string): string {
  const request = decode<Request>(input),
    unregister = register_test_root(request.root);
  let config = metrics_config(plain(request.config) as Partial<MetricsConfig>),
    store = new Store(request.database, config);
  store.initialize();
  const reader = createReadAccess(request.database);
  const results: unknown[] = [];
  try {
    for (const operation of request.operations) {
      let result: unknown = null,
        error: unknown = null;
      try {
        const op = operation.op;
        if (op === 'append') {
          const sample = operation.sample as Sample;
          if (operation.nonfinite) sample.metrics.cpu.value = Number.NaN;
          result = store.append(sample);
        } else if (op === 'prune') store.prune(number(operation.now as MetricNumber));
        else if (op === 'latest')
          result = reader.read((sql) => store.latest(sql, number(operation.now as MetricNumber)));
        else if (op === 'processes')
          result = reader.read((sql) =>
            store.processes(
              sql,
              number(operation.now as MetricNumber),
              plain(operation.query) as Parameters<Store['processes']>[2],
            ),
          );
        else if (op === 'history')
          result = reader.read((sql) =>
            store.history(sql, plain(operation.query) as Parameters<Store['history']>[1]),
          );
        else if (op === 'activity') result = store.renew_activity(operation.now as MetricNumber);
        else if (op === 'activity_until') result = store.activity_until();
        else if (op === 'failure')
          store.record_failure(
            operation.now as MetricNumber,
            String(operation.reason),
            operation.cadence as MetricNumber,
          );
        else if (op === 'reopen') {
          store.close();
          config = metrics_config({
            ...config,
            ...(plain(operation.config) as Partial<MetricsConfig>),
          });
          store = new Store(request.database, config);
          store.initialize();
        } else if (op === 'pinned_wal') {
          // Fixture-only writes establish physical pressure without changing metric rows.
          const pressure = createAuxiliaryDatabase(request.database, {
            schemaVersion: 2,
            maxBytes: config.database_bytes,
            initialize(tx) {
              tx.run(
                'CREATE TABLE IF NOT EXISTS parity_wal_pressure(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)',
              );
            },
          });
          const pinned = createReadAccess(request.database);
          try {
            result = pinned.read((sql) => {
              sql.get('SELECT COUNT(*) FROM samples');
              for (let i = 0; i < 100; i++) {
                pressure.write((tx) =>
                  tx.run('INSERT INTO parity_wal_pressure(payload) VALUES(?)', [
                    Buffer.alloc(4096, i),
                  ]),
                );
                if (statSync(request.database + '-wal').size >= config.wal_bytes) break;
              }
              try {
                return { unexpected_sequence: store.append(operation.sample as Sample) };
              } catch (caught) {
                return error_result(caught);
              }
            });
          } finally {
            pinned.close();
            pressure.close();
          }
        } else if (op === 'expired_read')
          result = reader.read((sql) => store.latest(sql, 0, process.hrtime.bigint() - 1n));
        else throw new Error('unknown parity operation');
      } catch (caught) {
        error = error_result(caught);
      }
      results.push({ result, error, rows: reader.read(rows) });
    }
    const rates = request.rates?.map((item) =>
      counter_rate(item.current, item.previous, item.elapsed, {
        same_identity: item.same_identity,
        unit: item.unit,
        ...(item.scale === undefined ? {} : { scale: number(item.scale) }),
      }),
    );
    const cpus = request.cpus?.map((item) =>
      host_cpu_percent(item.current.map(number), item.previous?.map(number) ?? null, {
        same_identity: item.same_identity,
      }),
    );
    return encode({ operations: results, rates: rates ?? [], cpus: cpus ?? [] });
  } finally {
    reader.close();
    store.close();
    unregister();
  }
}
