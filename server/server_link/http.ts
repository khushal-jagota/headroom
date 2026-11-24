/** Every machine response carries authority before a caller can consume work. */
import type { FastifyInstance } from 'fastify';
import type { ServerLocation } from './contracts.ts';
import {
  MOVE_NUMBER_HEADER,
  SERVER_ID_HEADER,
  SERVER_LOCATION_HEADER,
  SERVER_LOCATION_PATH,
} from './contracts.ts';

export function install_server_link(app: FastifyInstance, read: () => ServerLocation): void {
  app.addHook('onRequest', async (request, reply) => {
    const location = read();
    reply.header(
      SERVER_LOCATION_HEADER,
      Buffer.from(JSON.stringify(location)).toString('base64url'),
    );
    if (location.state === 'moved') {
      reply.header('cache-control', 'no-store').code(503).send(location);
      return;
    }
    if (!new URL(request.url, 'http://server').pathname.startsWith('/api/executor/')) return;
    const identity = request.headers[SERVER_ID_HEADER];
    const number = request.headers[MOVE_NUMBER_HEADER];
    // Adjacent older peers have neither header. New peers always send both.
    if (identity === undefined && number === undefined) return;
    if (identity !== location.server_id || number !== String(location.move_number))
      reply
        .header('cache-control', 'no-store')
        .code(409)
        .send({
          error: {
            code: 'server_authority',
            message: 'This is not the current server.',
            detail: {},
          },
        });
  });
  app.get(SERVER_LOCATION_PATH, async () => read());
}
