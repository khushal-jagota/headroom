/** Existing createApp/inject, actual sampler/readers, and a synthetic sidecar. No listener. */
import { createApp } from '../../../app.ts';
import { loadConfig } from '../../../core/config.ts';
import { createDatabase, createAuxiliaryDatabase } from '../../../core/database/index.ts';
import type { AuxiliaryDatabase } from '../../../core/database/contracts.ts';
import { metrics_config } from '../configuration.ts';
import type { MetricsConfig } from '../configuration.ts';
import { encode } from '../codec.ts';
import { MetricsRuntime } from '../runtime.ts';
import { create_metrics_routes } from '../routes.ts';

interface Request {
  root: string;
  database: string;
  config: Partial<MetricsConfig>;
  requests: {
    method: 'GET' | 'POST';
    path: string;
    headers?: Record<string, string>;
    fault?: string;
  }[];
}
function fault_writer(database: AuxiliaryDatabase): (kind: string) => void {
  const saved = new Map<string, string | Buffer>();
  return (kind) => {
    const [action, target] = kind.split('_');
    database.write((sql) => {
      if (target === 'samples' || target === 'metadata') {
        sql.run(
          `ALTER TABLE ${action === 'hide' ? target : 'hidden_' + target} RENAME TO ${action === 'hide' ? 'hidden_' + target : target}`,
        );
      } else if (target === 'snapshot') {
        if (!saved.has(target))
          saved.set(
            target,
            sql.get<{ payload: Buffer }>('SELECT payload FROM snapshots WHERE seq=1')!.payload,
          );
        sql.run('UPDATE snapshots SET payload=? WHERE seq=1', [
          action === 'corrupt' ? Buffer.from([0, 1, 2]) : saved.get(target)!,
        ]);
      } else {
        if (!saved.has(target))
          saved.set(
            target,
            sql.get<{ value: string }>('SELECT value FROM metadata WHERE key=?', [target])!.value,
          );
        sql.run('UPDATE metadata SET value=? WHERE key=?', [
          action === 'corrupt' ? 'invalid JSON' : saved.get(target)!,
          target,
        ]);
      }
    });
  };
}
export async function metrics_route_probe(input: string): Promise<string> {
  const request = JSON.parse(input) as Request;
  const config = metrics_config(request.config);
  const runtime = new MetricsRuntime({
    path: request.database,
    config,
    collector: null,
    wall_clock: () => 100,
  });
  const application = createDatabase(request.root + '/application.sqlite');
  let faults: AuxiliaryDatabase | undefined;
  let app: Awaited<ReturnType<typeof createApp>> | undefined;
  try {
    await runtime.start();
    faults = createAuxiliaryDatabase(request.database, {
      schemaVersion: 2,
      maxBytes: config.database_bytes,
      initialize() {},
    });
    const change = fault_writer(faults);
    app = await createApp(loadConfig(), {
      database: application,
      areas: [{ prefix: '/api', routes: create_metrics_routes(runtime) }],
    });
    const responses: unknown[] = [];
    for (const operation of request.requests) {
      if (operation.fault) change(operation.fault);
      const response = await app.inject({
        method: operation.method,
        url: operation.path,
        headers: operation.headers,
      });
      responses.push({
        status: response.statusCode,
        body: response.headers['content-type']?.startsWith('application/json')
          ? response.json()
          : response.body,
      });
    }
    return encode({ responses });
  } finally {
    await app?.close();
    await runtime.stop({ deadline: performance.now() / 1000 + 1 });
    await runtime.settled();
    faults?.close();
    application.close();
  }
}
