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
import type { WorkspaceAdmission } from './core/http/middleware.ts';
import { installCompatibility } from './core/http/compatibility.ts';
import { registerCoreRoutes } from './core/routes.ts';
import type { Changes } from './core/routes.ts';
import { registerArea } from './core/authority/register.ts';
import type { Area } from './core/authority/register.ts';
import type { ConversationSystem } from './conversation/contracts.ts';
import type { SignalNavigationAuthority } from './conversation/publication/authority.ts';
export interface AppServices {
    conversationSystem?: ConversationSystem;
    signalAuthority?: SignalNavigationAuthority;
    database: Database;
    clock?: Clock;
    changes?: Changes;
    areas?: readonly Area[];
    workspaceAdmission?: WorkspaceAdmission;
}
export async function createApp(config: Config, services: AppServices): Promise<FastifyInstance> {
    const app = Fastify({ logger: false, exposeHeadRoutes: false, bodyLimit: Number.MAX_SAFE_INTEGER, forceCloseConnections: true });
    const clock = services.clock ?? buildClock(config);
    const changes = services.changes ?? new ChangeSignal();
    installCompatibility(app);
    installMiddleware(app, config, services.workspaceAdmission);
    const configuredRoot = process.env.PLAN_APP_ROOT;
    const root = configuredRoot ? resolve(configuredRoot === '~' ? homedir() : configuredRoot.startsWith('~/') ? join(homedir(), configuredRoot.slice(2)) : configuredRoot) : fileURLToPath(new URL('../', import.meta.url));
    await registerCoreRoutes(app, config, clock, changes, root, services.database);
    for (const area of services.areas ?? [])
        registerArea(app, area, { database: services.database, config, clock, conversationSystem: services.conversationSystem, signalAuthority: services.signalAuthority });
    return app;
}
