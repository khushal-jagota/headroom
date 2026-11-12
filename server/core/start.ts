import type { FastifyListenOptions } from 'fastify';
import { areas, commitSteps, initializeAreas, prepareAreas, closeAreas } from '../areas.ts';
import { createApp } from '../app.ts';
import { loadConfig } from './config.ts';
import { buildClock } from './clock.ts';
import { prepare_background_loops, type BackgroundLoops } from './loops.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './locks/index.ts';

import type { Area } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { createIdlePublicationAuthority } from '../conversation/publication/authority.ts';
import type { SignalPublicationAuthority } from '../conversation/publication/authority.ts';

/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
  /** Off until Unit 21 explicitly composes the production lifecycle. */
  enableWorkerReadiness?: boolean;
  /** Unit 21 supplies UpdateService.poll. True suppresses this poll. */
  beforeWorkerReadinessPoll?: () => boolean;
  testAreas?: readonly Area[];
  conversationSystem?: ConversationSystem;
  createSignalAuthority?: (database: Database) => SignalPublicationAuthority;
}
export async function startServer(options: ServerOptions = {}): Promise<void> {
  const config = loadConfig();
  const ownership = acquireRuntimeOwnership(config.db_path);
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let loops: BackgroundLoops | undefined;
  let signalAuthority: SignalPublicationAuthority | undefined;
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
    loops = prepare_background_loops({
      enable_worker_readiness: options.enableWorkerReadiness ?? false,
      test_mode: config.test_mode,
      dispatch_enabled: config.dispatch_enabled,
      dispatcher_owned: dispatcher !== null,
      before_poll: options.beforeWorkerReadinessPoll,
      worker: options.conversationSystem
        ? {
            connect_database: () => database!,
            conversation_system: options.conversationSystem,
            worker_type_registry: configured_worker_type_registry(),
            now: () => clock.nowUnix(),
          }
        : null,
    });
    const app = await createApp(config, {
      database,
      changes,
      areas: [...areas, ...(options.testAreas ?? [])],
      clock,
      signalAuthority,
      conversationSystem: options.conversationSystem,
      workspaceAdmission: options.conversationSystem?.workspace_admission,
    });
    let closing: Promise<void> | undefined;
    const close = () => {
      if (closing) return closing;
      signalAuthority?.invalidate('shutdown');
      return (closing = loops!
        .stop()
        .then(() => closeAreas())
        .then(() => app.close())
        .then(() => {
          database?.close();
          dispatcher?.close();
          ownership.close();
        })
        .catch((error) => {
          closing = undefined;
          throw error;
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
    loops.start(config.tick_seconds, changes);
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
    await loops?.stop();
    await closeAreas();
    database?.close();
    dispatcher?.close();
    ownership.close();
    throw error;
  }
}
