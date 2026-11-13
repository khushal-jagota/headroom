import Fastify from 'fastify';
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
import { stringify_python_json } from './core/http/numeric_json.ts';
import { integer_value, normalize_integer } from './executor/integers.ts';
import { create_executor_routes } from './executor/app_transport.ts';
import type { VpsExecutor } from './executor/vps_transport.ts';
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
  executor?: VpsExecutor | null;
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
  installMiddleware(app, config, services.workspaceAdmission, undefined, request_lifetime);
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
      endpoint: services.executor ?? null,
      updates: () => services.executor?.updates ?? null,
    }),
  );
  app.get('/api/executor/health', async (_request, reply) => {
    if (!services.executor) return { mode: 'local', connection_state: 'disconnected' };
    const health = services.executor.health_payload();
    const present = health.presence === 'present';
    const identity = services.executor.health().identity;
    return reply.type('application/json').send(
      stringify_python_json(
        {
          ...health,
          mode: 'remote',
          identity:
            identity === null
              ? null
              : {
                  ...identity,
                  connection_generation: normalize_integer(
                    integer_value(identity.connection_generation) > (present ? 1n : 0n)
                      ? integer_value(identity.connection_generation)
                      : present
                        ? 1n
                        : 0n,
                  ),
                },
          connection_state: present ? 'ready' : 'disconnected',
        },
        { compact: true },
      ),
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
