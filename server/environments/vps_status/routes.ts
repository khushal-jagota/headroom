import type { FastifyPluginAsync } from 'fastify';
import type { Config } from '../../core/config.ts';
import type { AreaServices } from '../../core/authority/register.ts';
import type { VpsStatusSnapshot } from './contracts.ts';
import { collect_vps_status } from './collect.ts';
/** Python exposes this read publicly. Cleanup remains a host-local command. */
export function create_vps_status_routes(
  collector: (config: Config) => Promise<VpsStatusSnapshot> = collect_vps_status,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.get('/vps-status', async () => collector(services.config));
  };
}
