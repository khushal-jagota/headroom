import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { IdlePublicationAuthority } from '../conversation/publication/authority.ts';
import { SnapshotFences } from '../navigation/delivery/snapshot_fence.ts';

// Distinct gap: HTTP snapshots cannot force authority loss inside an asynchronous
// framework send hook. Existing authority tests stop before transport admission.
// Fastify inject uses the real hooks and producer, with no database or server host.
test('navigation_snapshot_admission', async () => {
  const authority = new IdlePublicationAuthority({
    scope: 'source',
    runtime_epoch: 'runtime',
    installation_id: 'installation',
    delivery_session_id: 'delivery',
  });
  const app = Fastify();
  app.addHook('onSend', async (_request, _reply, payload) => {
    await new Promise<void>((resolve) => setImmediate(resolve));
    authority.invalidate('shutdown');
    return payload;
  });
  app.register(async (scope) => {
    const fences = new SnapshotFences(scope);
    scope.get('/snapshot', async (request, reply) => {
      const captured = authority.capture();
      fences.hold(request, reply, authority, captured);
      return reply.header('cache-control', 'no-store').send({ snapshot: 'stale private content' });
    });
  });
  try {
    const response = await app.inject('/snapshot');
    expect(response.statusCode).toBe(503);
    expect(response.headers['cache-control']).toBe('no-store');
    expect(response.json()).toEqual({ detail: 'navigation publication is not ready' });
  } finally {
    await app.close();
    authority.close();
  }
});
