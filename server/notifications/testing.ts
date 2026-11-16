/** Test-only composition cannot construct either provider transport. */
import type { Area } from '../core/authority/register.ts';
import type { APNsAdapter, NotificationIntent, WebPushAdapter } from './contracts.ts';
import { NotificationLoop } from './runtime.ts';
export const notificationTestArea: Area = {
  prefix: '/api/test',
  routes: async (app, services) => {
    if (!services.config.test_mode) throw new Error('Notification test routes require test mode');
    const intents: { transport: 'web' | 'apns'; intent: NotificationIntent }[] = [];
    const adapter: WebPushAdapter = {
      async send(_subscription, intent) {
        intents.push({ transport: 'web', intent });
        return { delivered: true };
      },
    };
    const apns_adapter: APNsAdapter = {
      async send(delivery) {
        intents.push({ transport: 'apns', intent: delivery.intent });
        return { delivered: true };
      },
      close() {},
    };
    const notifications = new NotificationLoop({
      database: services.database,
      clock: services.clock,
      canonical_origin: 'https://comparison.invalid',
      adapter,
      apns_adapter,
    });
    let pending: Promise<unknown> = Promise.resolve();
    app.post('/poll/notifications', async () => {
      const pass = pending.then(async () => {
        intents.length = 0;
        const delivered = await notifications.poll_once();
        return { delivered, intents: [...intents] };
      });
      pending = pass.catch(() => {});
      return pass;
    });
  },
};
