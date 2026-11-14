import type { FastifyPluginAsync } from 'fastify';
import type { Config } from '../../core/config.ts';
import type { AreaServices } from '../../core/authority/register.ts';
import type { VpsStatusSnapshot } from './contracts.ts';
/** Status reads are public. Cleanup remains a host-local command. */
export function create_vps_status_routes(
  collector: (config: Config) => Promise<VpsStatusSnapshot>,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.get('/vps-status', async () => collector(services.config));
  };
}
