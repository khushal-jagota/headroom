import type { FastifyRequest } from 'fastify';

/** Match Uvicorn's default trusted peer and forwarded scheme, without trusting a forwarded Host. */
export function requestOrigin(request: FastifyRequest): string {
  let scheme: string = request.protocol;
  if (request.raw.socket.remoteAddress === '127.0.0.1') {
    let forwarded: string | undefined;
    const headers = request.raw.rawHeaders;
    for (let i = 0; i < headers.length; i += 2) {
      if (headers[i]!.toLowerCase() === 'x-forwarded-proto') forwarded = headers[i + 1]!.trim();
    }
    if (forwarded && ['http', 'https', 'ws', 'wss'].includes(forwarded)) scheme = forwarded;
  }
  return `${scheme}://${request.host}`;
}
