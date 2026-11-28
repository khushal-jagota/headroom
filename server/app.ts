import { initialize_server_location, read_server_location } from './server_link/data.ts';
import { install_server_link } from './server_link/http.ts';
import { install_slow_task_logging } from './core/slow_tasks.ts';
import Fastify from 'fastify';
import type { PreparedPreviewRoutes } from './previews/routes.ts';
import { read_home_trial } from './environments/updates/home_trial.ts';
import type { UpdateBarrier } from './environments/update_barrier.ts';
import type { FastifyInstance, FastifyPluginAsync } from 'fastify';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import type { Config } from './core/config.ts';
import type { Clock } from './core/clock.ts';
import { buildClock } from './core/clock.ts';
import { ChangeSignal } from './core/changes/index.ts';
import type { Database } from './core/database/index.ts';
import { installMiddleware } from './core/http/middleware.ts';
import { install_request_lifetime, type HttpRequestScope } from './core/http/lifetime.ts';
import type { WorkspaceAdmission } from './core/http/middleware.ts';
import type { StreamCloseSink } from './core/http/live_streams/records.ts';
import { installCompatibility } from './core/http/compatibility.ts';
import { registerCoreRoutes } from './core/routes.ts';
import { LiveSubscriptions } from './live_updates/registry.ts';
import { install_live_socket } from './live_updates/socket.ts';
import { registerArea } from './core/authority/register.ts';
import { create_executor_routes } from './executor/app_transport.ts';
import type { VpsExecutorRegistry } from './executor/registry.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from './execution_identity.ts';
import { PlannerError } from './core/contracts.ts';
import type { Area } from './core/authority/register.ts';
import type { ConversationSystem } from './conversation/contracts.ts';
import type { SignalNavigationAuthority } from './conversation/publication/authority.ts';
export interface Changes {
  subscribe(callback: () => void): () => void;
  emit(): void;
}
export interface AppServices {
  /** Publish the real instance before registration can fail or await. */
  own_app?(app: FastifyInstance): void;
  request_lifetime?: HttpRequestScope;
  conversationSystem?: ConversationSystem;
  signalAuthority?: SignalNavigationAuthority;
  database: Database;
  clock?: Clock;
  changes?: Changes;
  areas?: readonly Area[];
  workspaceAdmission?: WorkspaceAdmission;
  updateBarrier?: UpdateBarrier;
  waitingScreensRoot?: () => string | null;
  streamCloseSink?: StreamCloseSink;
  executor?: VpsExecutorRegistry | null;
  /** More executor routes that authenticate machines themselves, such as the server move. */
  executorRoutes?: readonly FastifyPluginAsync[];
  previews?: PreparedPreviewRoutes;
}
export async function createApp(config: Config, services: AppServices): Promise<FastifyInstance> {
  const app = Fastify({
    logger: false,
    exposeHeadRoutes: false,
    bodyLimit: Number.MAX_SAFE_INTEGER,
    forceCloseConnections: false,
  });
  services.own_app?.(app);
  install_slow_task_logging(app);
  app.decorate('executor_registry', services.executor ?? null);
  const clock = services.clock ?? buildClock(config);
  const request_lifetime = install_request_lifetime(app, services.request_lifetime);
  const changes = services.changes ?? new ChangeSignal();
  initialize_server_location(services.database);
  installCompatibility(app);
  install_server_link(app, () => services.database.read((sql) => read_server_location(sql)!));
  installMiddleware(
    app,
    config,
    services.workspaceAdmission,
    request_lifetime,
    services.updateBarrier,
    { database: services.database, clock },
    services.streamCloseSink,
  );
  const configuredRoot = process.env.PLAN_APP_ROOT;
  const root = configuredRoot
    ? resolve(
        configuredRoot === '~'
          ? homedir()
          : configuredRoot.startsWith('~/')
            ? join(homedir(), configuredRoot.slice(2))
            : configuredRoot,
      )
    : fileURLToPath(new URL('../', import.meta.url));
  const trial = read_home_trial(services.updateBarrier, config.app_sha);
  await registerCoreRoutes(
    app,
    config,
    clock,
    root,
    services.database,
    services.waitingScreensRoot,
    trial,
  );
  app.register(
    create_executor_routes({
      trial,
      config,
      database: services.database,
      registry: services.executor ?? null,
      previews: services.previews,
    }),
  );
  for (const routes of services.executorRoutes ?? []) app.register(routes);
  app.get('/api/executor/health', async (request, reply) => {
    const machine_id =
      new URL(request.url, 'http://executor').searchParams.getAll('machine_id').at(-1) ||
      ORIGINAL_MAC_MACHINE_ID;
    const endpoint = services.executor?.get(machine_id);
    if (!endpoint) {
      if (machine_id !== HOME_MACHINE_ID && machine_id !== ORIGINAL_MAC_MACHINE_ID)
        throw new PlannerError('not_found', 'Unknown executor destination');
      return { mode: 'local', connection_state: 'disconnected' };
    }
    const health = endpoint.health_payload();
    const present = health.presence === 'present';
    if (machine_id !== ORIGINAL_MAC_MACHINE_ID)
      return reply.type('application/json').send(
        JSON.stringify({
          mode: 'remote',
          ...health,
          connection_state: present ? 'ready' : 'disconnected',
        }),
      );
    const identity = endpoint.health().identity;
    return reply.type('application/json').send(
      JSON.stringify({
        ...health,
        mode: 'remote',
        identity:
          identity === null
            ? null
            : {
                ...identity,
                connection_generation: Math.max(identity.connection_generation, present ? 1 : 0),
              },
        connection_state: present ? 'ready' : 'disconnected',
      }),
    );
  });
  const live_subscriptions = new LiveSubscriptions();
  install_live_socket(app, {
    config,
    database: services.database,
    clock,
    changes,
    subscriptions: live_subscriptions,
    stream_close_sink: services.streamCloseSink,
  });
  for (const area of services.areas ?? [])
    registerArea(app, area, {
      database: services.database,
      config,
      clock,
      conversationSystem: services.conversationSystem,
      signalAuthority: services.signalAuthority,
      live_subscriptions,
      changes,
    });
  return app;
}
