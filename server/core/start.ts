import type { FastifyListenOptions } from 'fastify';
import { areas, commitSteps, initializeAreas, prepareAreas, closeAreas } from '../areas.ts';
import { createApp } from '../app.ts';
import { loadConfig } from './config.ts';
import { buildClock } from './clock.ts';
import type { Clock } from './clock.ts';
import type { Config } from './config.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './locks/index.ts';

import type { Area } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { createIdlePublicationAuthority } from '../conversation/publication/authority.ts';
import type { SignalPublicationAuthority } from '../conversation/publication/authority.ts';

/** Unit 21 supplies these factories explicitly. Undefined means off. */
export interface ComposedBackgroundService {
  start(): void | Promise<void>;
  close(): void | Promise<void>;
}
export interface BackgroundCompositionContext {
  database: Database;
  config: Config;
  clock: Clock;
  changes: ChangeSignal;
}
export type BackgroundServiceFactory = (
  context: BackgroundCompositionContext,
) => ComposedBackgroundService;

/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
  testAreas?: readonly Area[];
  /** Explicit composition switches. No factory means no task or provider adapter. */
  notificationDelivery?: BackgroundServiceFactory;
  stallChecker?: BackgroundServiceFactory;
  conversationSystem?: ConversationSystem;
  createSignalAuthority?: (database: Database) => SignalPublicationAuthority;
}
export async function startServer(options: ServerOptions = {}): Promise<void> {
  const config = loadConfig();
  const ownership = acquireRuntimeOwnership(config.db_path);
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let signalAuthority: SignalPublicationAuthority | undefined;
  const background: ComposedBackgroundService[] = [];
  const closeBackground = async () => {
    let failure: unknown;
    for (const service of background.splice(0).reverse()) {
      try {
        await service.close();
      } catch (error) {
        failure ??= error;
      }
    }
    if (failure) throw failure;
  };
  try {
    const changes = new ChangeSignal();
    database = createDatabase(config.db_path, { commitSteps, onChange: () => changes.emit() });
    prepareAreas(database);
    signalAuthority = (options.createSignalAuthority ?? createIdlePublicationAuthority)(database);
    initializeAreas(database, signalAuthority, config);
    if (!config.test_mode && config.dispatch_enabled) {
      dispatcher = acquireLock(config.dispatcher_lock_path);
    }
    const clock = buildClock(config);
    const app = await createApp(config, {
      database,
      changes,
      areas: [...areas, ...(options.testAreas ?? [])],
      clock,
      signalAuthority,
      conversationSystem: options.conversationSystem,
      workspaceAdmission: options.conversationSystem?.workspace_admission,
    });
    // Comparison mode never invokes composition factories, even when supplied.
    if (!config.test_mode) {
      const context = { database, config, clock, changes };
      if (dispatcher && options.notificationDelivery) {
        const delivery = options.notificationDelivery(context);
        background.push(delivery);
        await delivery.start();
      }
      if (options.stallChecker) {
        const checker = options.stallChecker(context);
        background.push(checker);
        await checker.start();
      }
    }
    let closing: Promise<void> | undefined;
    const close = () => {
      if (closing) return closing;
      signalAuthority?.invalidate('shutdown');
      return (closing = closeBackground()
        .finally(() => closeAreas())
        .finally(() => app.close())
        .finally(() => {
          database?.close();
          dispatcher?.close();
          ownership.close();
        }));
    };
    for (const signal of ['SIGTERM', 'SIGINT'] as const) {
      process.once(signal, () => {
        void close().catch((error) => {
          console.error(error);
          process.exitCode = 1;
        });
      });
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
    signalAuthority?.invalidate('shutdown');
    try {
      await closeBackground();
    } catch (cleanupError) {
      console.error('background cleanup failed', cleanupError);
    }
    try {
      await closeAreas();
    } catch (cleanupError) {
      console.error('area cleanup failed', cleanupError);
    } finally {
      database?.close();
      dispatcher?.close();
      ownership.close();
    }
    throw error;
  }
}
