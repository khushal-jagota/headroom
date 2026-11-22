import { expect, test } from 'vitest';
import { TestClock } from '../core/clock.ts';
import { create, fixture, now, propose } from '../tests/ticket_fixture.ts';
import { initialize_notifications, set_preference } from './data.ts';
import { desktop_notification_streams, type DesktopNotification } from './desktop_streams.ts';
import { NotificationLoop } from './runtime.ts';

// The desktop path has no delivery table, so only this proves that it sits behind the
// same policy door: an allowed edge reaches an open stream once, a switched-off one never.
test('the delivery pass hands each allowed notification to open desktop streams once', async () => {
  const database = fixture();
  database.write((sql) => initialize_notifications(sql, now));
  const loop = new NotificationLoop({
    database,
    clock: new TestClock(new Date(now * 1000).toISOString()),
    canonical_origin: null,
    adapter: { send: async () => ({ delivered: true }) },
    apns_adapter: null,
  });
  const received: DesktopNotification[] = [];
  const stop = desktop_notification_streams(database).listen((item) => received.push(item));

  const allowed = propose(database, create(database), 'A proposal');
  await loop.poll_once();
  await loop.poll_once();
  expect(received).toEqual([
    {
      title: 'Test ticket',
      body: expect.stringContaining('waits for your approval'),
      route: `/#/workspace/${allowed.id}`,
      tag: `panels-ticket-${allowed.id}`,
    },
  ]);

  database.write((sql) => set_preference(sql, 'tickets', 'awaiting_approval', false, now));
  propose(database, create(database), 'Another proposal');
  await loop.poll_once();
  expect(received).toHaveLength(1);
  stop();
});
