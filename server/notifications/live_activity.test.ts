import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import {
  LIVE_ACTIVITY_LIFETIME_SECONDS,
  live_activity_payload,
  plan_live_activity,
  type ActivityDeviceState,
} from './live_activity.ts';
import * as data from './live_activity_data.ts';
import * as apns_data from './apns_data.ts';

const card = (key: string, title = key) => ({ key, title });
const running = (last_cards: string[] | null, started_at = 1000): ActivityDeviceState => ({
  push_to_start_token: 'aa',
  update_token: 'bb',
  started_at,
  last_cards,
});

test('a running activity is pushed only when the queue changed, and a new card carries its title', () => {
  const [a, b, c] = [card('a'), card('b'), card('c', 'Third card')];
  expect(plan_live_activity(running(['a', 'b']), [a, b], 2000)).toBeNull();
  // One card handled while another arrived: the count stays, the alert still fires.
  expect(plan_live_activity(running(['a', 'b']), [b, c], 2000)).toEqual({
    kind: 'update',
    token: 'bb',
    content: { count: 2, arrivedTitle: 'Third card' },
    alert: true,
  });
  expect(plan_live_activity(running(['a', 'b']), [a], 2000)).toMatchObject({
    content: { count: 1 },
    alert: false,
  });
  // After a new activity reports itself, the first push is whole and quiet.
  expect(plan_live_activity(running(null), [a], 2000)).toMatchObject({ alert: false });
});

test('an ended or aged activity restarts, once per retry window, and clear stays present', () => {
  const none: ActivityDeviceState = {
    push_to_start_token: 'aa',
    update_token: null,
    started_at: null,
    last_cards: null,
  };
  expect(plan_live_activity(none, [], 5000)).toEqual({
    kind: 'start',
    token: 'aa',
    content: { count: 0 },
  });
  expect(plan_live_activity({ ...none, started_at: 4900 }, [], 5000)).toBeNull();
  const aged = running(['a'], 1000);
  expect(
    plan_live_activity(aged, [card('a')], 1000 + LIVE_ACTIVITY_LIFETIME_SECONDS + 1),
  ).toMatchObject({ kind: 'start' });
  expect(plan_live_activity({ ...none, push_to_start_token: null }, [], 5000)).toBeNull();
});

// The widget decodes this body with a default JSONDecoder; the host tests in ios/PanelsTests read the same file.
test('the pushed content state matches the shared fixture', () => {
  const shared = JSON.parse(
    readFileSync(
      new URL('../../ios/PanelsTests/Fixtures/review-live-activity.json', import.meta.url),
      'utf8',
    ),
  );
  const cards = [card('a'), card('b'), card('c'), card('d', shared.update.arrivedTitle)];
  const update = plan_live_activity(running(['a', 'b', 'c']), cards, 2000)!;
  const body = JSON.parse(live_activity_payload(update, 2000).toString());
  expect(body.aps['content-state']).toEqual(shared.update);
  expect(body.aps).toMatchObject({ event: 'update', alert: { body: shared.update.arrivedTitle } });
  const clear = plan_live_activity(running(['a']), [], 2000)!;
  expect(JSON.parse(live_activity_payload(clear, 2000).toString()).aps).toEqual({
    timestamp: 2000,
    event: 'update',
    'content-state': shared.clear,
  });
  const start = JSON.parse(
    live_activity_payload({ kind: 'start', token: 'aa', content: { count: 3 } }, 2000).toString(),
  );
  expect(start.aps).toMatchObject({
    event: 'start',
    'attributes-type': 'ReviewActivityAttributes',
    attributes: {},
    alert: { body: '3 new.' },
  });
});

test('tokens are stored per registered device, and a new activity forgets the last push', () => {
  const database = fixture();
  const device = {
    device_token: 'ab12',
    bundle_id: 'com.example.panels',
    environment: 'production' as const,
  };
  database.write((sql) => {
    expect(data.register_live_activity(sql, device, { push_to_start_token: 'cc' }, 10)).toBe(false);
    apns_data.register_device(sql, { ...device, now: 10 });
    expect(data.register_live_activity(sql, device, { push_to_start_token: 'cc' }, 11)).toBe(true);
    data.register_live_activity(sql, device, { update_token: 'dd' }, 12);
    const [row] = data.live_activity_devices(sql);
    data.record_live_activity_sent(sql, row.device_id, { kind: 'update', cards: ['a'], now: 13 });
    expect(data.live_activity_devices(sql)[0]).toMatchObject({
      push_to_start_token: 'cc',
      update_token: 'dd',
      started_at: 12,
      last_cards: ['a'],
    });
    // The same token again changes nothing. A different token is a new activity.
    data.register_live_activity(sql, device, { update_token: 'dd' }, 14);
    expect(data.live_activity_devices(sql)[0].last_cards).toEqual(['a']);
    data.register_live_activity(sql, device, { update_token: 'ee' }, 15);
    expect(data.live_activity_devices(sql)[0]).toMatchObject({
      update_token: 'ee',
      last_cards: null,
    });
    // The app reports an ended activity with a null token: the update token and its start time go.
    data.register_live_activity(sql, device, { update_token: null }, 15);
    expect(data.live_activity_devices(sql)[0]).toMatchObject({
      update_token: null,
      started_at: null,
      last_cards: null,
    });
    data.register_live_activity(sql, device, { update_token: 'ee' }, 15);
    // A refused update token ends the activity but keeps the start token and the device.
    data.record_live_activity_dead_token(sql, row.device_id, 'update_token', 16);
    expect(data.live_activity_devices(sql)[0]).toMatchObject({
      push_to_start_token: 'cc',
      update_token: null,
      started_at: null,
    });
  });
});
