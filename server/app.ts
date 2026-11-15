import Fastify from 'fastify';
import type { UpdateBarrier } from './environments/update_barrier.ts';
import type { FastifyInstance } from 'fastify';
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
import { installCompatibility } from './core/http/compatibility.ts';
import { registerCoreRoutes } from './core/routes.ts';
import type { Changes } from './core/routes.ts';
import { registerArea } from './core/authority/register.ts';
import { create_executor_routes } from './executor/app_transport.ts';
import type { VpsExecutorRegistry } from './executor/registry.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from './execution_identity.ts';
import { PlannerError } from './core/contracts.ts';
import type { Area } from './core/authority/register.ts';
import type { ConversationSystem } from './conversation/contracts.ts';
import type { SignalNavigationAuthority } from './conversation/publication/authority.ts';
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
  executor?: VpsExecutorRegistry | null;
}
export async function createApp(config: Config, services: AppServices): Promise<FastifyInstance> {
  const app = Fastify({
    logger: false,
    exposeHeadRoutes: false,
    bodyLimit: Number.MAX_SAFE_INTEGER,
    forceCloseConnections: false,
  });
  services.own_app?.(app);
  const clock = services.clock ?? buildClock(config);
  const request_lifetime = install_request_lifetime(app, services.request_lifetime);
  const changes = services.changes ?? new ChangeSignal();
  installCompatibility(app);
  installMiddleware(
    app,
    config,
    services.workspaceAdmission,
    undefined,
    request_lifetime,
    services.updateBarrier,
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
  await registerCoreRoutes(app, config, clock, changes, root, services.database);
  app.register(
    create_executor_routes({
      config,
      database: services.database,
      registry: services.executor ?? null,
    }),
  );
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
  for (const area of services.areas ?? [])
    registerArea(app, area, {
      database: services.database,
      config,
      clock,
      conversationSystem: services.conversationSystem,
      signalAuthority: services.signalAuthority,
      changes,
    });
  return app;
}
