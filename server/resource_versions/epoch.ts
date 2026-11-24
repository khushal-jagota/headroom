import { randomUUID } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
/** Fastify plugin scopes share one HTTP server, and distinct application hosts do not. */
const epochs = new WeakMap<FastifyInstance['server'], string>();
export function resource_epoch(app: FastifyInstance): string {
  let epoch = epochs.get(app.server);
  if (epoch === undefined) {
    epoch = randomUUID();
    epochs.set(app.server, epoch);
  }
  return epoch;
}
