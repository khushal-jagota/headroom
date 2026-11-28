import type { FastifyInstance, FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { Database } from '../database/index.ts';
import type { Config } from '../config.ts';
import type { Clock } from '../clock.ts';
import type { ChangeSignal } from '../changes/index.ts';
import { requestContext, TRUSTED_INGRESS_CONFIGURED, REQUEST_IDENTITY_READER } from './identity.ts';
import { requestAdmission } from './intake.ts';
import type { AdmissionDependencies } from './intake.ts';
import type { ConversationSystem } from '../../conversation/contracts.ts';
import type { SignalNavigationAuthority } from '../../conversation/publication/authority.ts';
import type { LiveSubscriptions } from '../../live_updates/registry.ts';
export interface AreaServices {
  changes?: { subscribe(callback: () => void): () => void };
  conversationSystem?: ConversationSystem;
  signalAuthority?: SignalNavigationAuthority;
  /** Where each feed registers how the live socket opens it. */
  live_subscriptions?: LiveSubscriptions;
  database: Database;
  config: Config;
  clock: Clock;
  identity: (request: FastifyRequest) => ReturnType<typeof requestContext>;
}
/** Background and test composition receives this only under database ownership. */
export interface OwnedAreaContext extends Omit<AreaServices, 'identity'> {
  changes: ChangeSignal;
  assert_database_owned(): void;
}
export interface Area {
  prefix: string;
  routes: FastifyPluginAsync<AreaServices>;
  admission?: AdmissionDependencies;
}
/** Routes register inertly; their existing owner starts delivery after app readiness. */
export interface PreparedAreaRoutes {
  readonly routes: FastifyPluginAsync<AreaServices>;
  start(): void | Promise<void>;
}
/** Every ordinary area passes this door; executor transport has a separate boundary. */
export function registerArea(
  app: FastifyInstance,
  area: Area,
  services: Omit<AreaServices, 'identity'>,
): void {
  app.register(
    async (scope) => {
      scope.addHook('onRequest', async (request: FastifyRequest) => {
        Object.assign(request, {
          [TRUSTED_INGRESS_CONFIGURED]: Boolean(services.config.trusted_ingress_provider),
          [REQUEST_IDENTITY_READER]: () =>
            services.database.read((sql) => requestContext(request, sql)),
        });
      });
      scope.addHook('preHandler', async (request: FastifyRequest) => {
        services.database.read((sql) => requestAdmission(sql, request, area.admission));
      });
      await scope.register(area.routes, {
        ...services,
        identity: (request) => services.database.read((sql) => requestContext(request, sql)),
      });
    },
    { prefix: area.prefix },
  );
}
