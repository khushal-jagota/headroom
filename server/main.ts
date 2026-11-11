import type { FastifyListenOptions } from 'fastify';
import { areas, commitSteps } from './areas.ts';
import { createApp } from './app.ts';
import { loadConfig } from './core/config.ts';
import { buildClock } from './core/clock.ts';
import { ChangeSignal } from './core/changes/index.ts';
import { createDatabase } from './core/database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './core/locks/index.ts';

const config = loadConfig();
const ownership = acquireRuntimeOwnership(config.db_path);
let dispatcher: ReturnType<typeof acquireLock> = null;
let database: ReturnType<typeof createDatabase> | undefined;
try {
  const changes = new ChangeSignal();
  database = createDatabase(config.db_path, { commitSteps, onChange: () => changes.emit() });
  if (!config.test_mode && config.dispatch_enabled) {
    dispatcher = acquireLock(config.dispatcher_lock_path);
  }
  const app = await createApp(config, { database, changes, areas: [...areas], clock: buildClock(config) });
  let closing: Promise<void> | undefined;
  const close = () => closing ??= app.close().finally(() => {
    database?.close();
    dispatcher?.close();
    ownership.close();
  });
  for (const signal of ['SIGTERM', 'SIGINT'] as const) {
    process.once(signal, () => { void close().catch((error) => {
      console.error(error);
      process.exitCode = 1;
    }); });
  }
  const inherited = process.env.PLAN_SERVER_LISTENER_FD;
  if (inherited !== undefined) {
    const fd = Number(inherited);
    if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
    await app.listen({ fd } as FastifyListenOptions & { fd: number });
  } else {
    await app.listen({ host: '127.0.0.1', port: config.port });
  }
} catch (error) {
  database?.close();
  dispatcher?.close();
  ownership.close();
  throw error;
}
