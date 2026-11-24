import type { FastifyReply } from 'fastify';
import type { ClientIdentity } from '../../../client_versions/contracts.ts';
import { CLIENT_STREAM_WARNING_THRESHOLD } from './configuration.ts';

declare module 'fastify' {
  interface FastifyRequest {
    count_live_stream?: (() => void) | null;
  }
}

/** One counter belongs to one HTTP server, never to a socket or a process-wide client. */
export class ClientLiveStreams {
  readonly #clients = new Map<string, { total: number; routes: Map<string, number> }>();

  open(client: ClientIdentity, route: string): () => void {
    const identity = { device_id: client.device_id, kind: client.kind, version: client.version };
    const key = JSON.stringify(identity);
    let held = this.#clients.get(key);
    if (!held) {
      held = { total: 0, routes: new Map() };
      this.#clients.set(key, held);
    }
    held.total += 1;
    held.routes.set(route, (held.routes.get(route) ?? 0) + 1);
    if (held.total === CLIENT_STREAM_WARNING_THRESHOLD + 1)
      console.warn(
        `client_live_streams ${JSON.stringify({
          client: identity,
          total: held.total,
          routes: Object.fromEntries(held.routes),
        })}`,
      );
    let closed = false;
    return () => {
      if (closed) return;
      closed = true;
      held.total -= 1;
      const remaining = held.routes.get(route)! - 1;
      if (remaining) held.routes.set(route, remaining);
      else held.routes.delete(route);
      if (!held.total) this.#clients.delete(key);
    };
  }
}

/** The normal send hook and raw/hijacked routes share the same response ownership. */
export function count_live_stream(reply: FastifyReply): void {
  reply.request.count_live_stream?.();
}
