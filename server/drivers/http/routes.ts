/** GET /api/drivers and each driver's icon. Static: up while the conversation system is not. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { HttpError } from '../../core/http/errors.ts';
import type { BackendHttpRuntime } from '../../conversation/snapshot/contracts.ts';
import { driver_icon_file, drivers_json } from './presentation.ts';

export function createDriverRoutes(
  runtime: Pick<BackendHttpRuntime, 'drivers'>,
): FastifyPluginAsync<AreaServices> {
  return async (app) => {
    app.get('/drivers', async () => drivers_json(runtime.drivers));
    app.get<{ Params: { driver_kind: string } }>(
      '/drivers/:driver_kind/icon',
      async (request, reply) => {
        const driver = runtime.drivers.find(
          (candidate) => candidate.driver_kind === request.params.driver_kind,
        );
        if (driver === undefined) throw new HttpError(404, 'Not Found');
        const icon = driver_icon_file(driver);
        reply
          .header('content-type', icon.media_type)
          .header('etag', `"${icon.version}"`)
          .header('cache-control', 'private, max-age=31536000, immutable')
          .header('x-content-type-options', 'nosniff');
        if (icon.media_type === 'image/svg+xml') reply.header('content-security-policy', 'sandbox');
        return reply.send(icon.bytes);
      },
    );
  };
}
