/** Keep a snapshot's permit live through framework hooks and transport admission. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import type { CurrentAuthority } from '../../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority } from '../contracts.ts';
interface Fence { authority: NavigationAuthority; token: CurrentAuthority; invalid: boolean }
export class SnapshotFences {
  private readonly pending = new WeakMap<FastifyRequest, Fence>();
  constructor(app: FastifyInstance) {
    // Plugin hooks run after inherited hooks, including asynchronous response hooks.
    app.addHook('onSend', (request, reply, payload, done) => {
      const fence = this.pending.get(request);
      if (fence) {
        try { fence.authority.validate(fence.token); } catch { fence.invalid = true; }
        if (fence.invalid) {
          if (reply.raw.headersSent) { reply.raw.destroy(); done(null, null); return; }
          reply.code(503).header('cache-control', 'no-store').type('application/json');
          reply.removeHeader('content-encoding').removeHeader('content-length');
          done(null, JSON.stringify({ detail: 'navigation publication is not ready' }));
          return;
        }
      }
      done(null, payload);
    });
  }
  hold(request: FastifyRequest, reply: FastifyReply, authority: NavigationAuthority, token: CurrentAuthority): void {
    const fence: Fence = { authority, token, invalid: false };
    let forget = () => {};
    const release = () => { forget(); this.pending.delete(request); };
    this.pending.set(request, fence);
    try {
      forget = authority.watch(token, () => {
        fence.invalid = true;
        // Bytes already admitted cannot be withdrawn. Cancel any remaining transfer.
        if (reply.raw.headersSent && !reply.raw.writableFinished) reply.raw.destroy();
      });
    } catch { fence.invalid = true; }
    reply.raw.once('finish', release);
    reply.raw.once('close', release);
  }
}
