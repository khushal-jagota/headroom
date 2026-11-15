/** No engine existed to prove pending-start fairness or durable admission. */
import { expect, test } from 'vitest';
import { fixture } from '../../tests/ticket_fixture.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import {
  AutomaticStartAdmission,
  explicit_start,
  manual_start_exempt,
  write_bindings,
} from './index.ts';
import type { StartRequest } from './contracts.ts';

function setup(limit = 4) {
  let now = 0;
  const admission = new AutomaticStartAdmission({ limit, clock: () => now });
  const request = (
    identity: string,
    destination = HOME_MACHINE_ID,
    work_class: StartRequest['work_class'] = 'worker',
  ): StartRequest => ({ identity, destination, work_class });
  return {
    admission,
    request,
    tick: () => {
      now += 1;
    },
  };
}

test('destinations pace independently and Home retains supervisor capacity', () => {
  const { admission: a, request: r, tick } = setup();
  a.configure_destinations(() => true);
  expect(a.try_start(r('w1'))).toBe(true);
  expect(a.try_start(r('w2'))).toBe(false);
  expect(a.try_start(r('remote', 'away'))).toBe(true);
  tick();
  expect(a.try_start(r('w2'))).toBe(true);
  tick();
  expect(a.try_start(r('w3'))).toBe(true);
  tick();
  expect(a.try_start(r('w4'))).toBe(false);
  expect(a.try_start(r('supervisor', HOME_MACHINE_ID, 'supervisor'))).toBe(true);
  tick();
  expect(a.try_start(r('w4'))).toBe(false);
  a.finish('w1');
  expect(a.try_start(r('w4'))).toBe(true);
});

test('reachable eligible order and supervisor priority prevent scan-order starvation', () => {
  const { admission: a, request: r, tick } = setup();
  let reachable = false;
  a.configure_destinations((destination) => destination === HOME_MACHINE_ID || reachable);
  expect(a.try_start(r('absent', 'away'))).toBe(false);
  expect(a.try_start(r('first'))).toBe(true);
  expect(a.try_start(r('older'))).toBe(false);
  expect(a.try_start(r('younger'))).toBe(false);
  expect(a.try_start(r('chief', HOME_MACHINE_ID, 'supervisor'))).toBe(false);
  tick();
  expect(a.try_start(r('older'))).toBe(false);
  expect(a.try_start(r('chief', HOME_MACHINE_ID, 'supervisor'))).toBe(true);
  tick();
  expect(a.try_start(r('younger'))).toBe(false);
  expect(a.try_start(r('older'))).toBe(true);
  reachable = true;
  expect(a.try_start(r('absent', 'away'))).toBe(true);
});

test('queueing refunds only unattempted latest grants and a batch shares one permit', () => {
  const { admission: a, request: r } = setup(2);
  expect(a.try_start(r('first'))).toBe(true);
  a.queue('first');
  expect(a.try_start(r('second'))).toBe(true);
  a.queue('second');
  a.mark_eligible(['first', 'second']);
  expect(a.begin_queued(['first', 'second'])).toBe(true);
  expect(new Set(a.snapshots().map((entry) => entry.permit_identity))).toEqual(new Set(['first']));
  a.bind(['first', 'second'], { conversation_id: 'c', turn_number: 1 });
  a.bind_source({ conversation_id: 'c', turn_number: 1 }, {});
  a.queue('first');
  a.queue('second');
  expect(a.try_start(r('third'))).toBe(false);
});

test('claim transfer retains one pacing charge and exact-turn acceptance frees its group', () => {
  const { admission: a, request: r } = setup();
  expect(a.try_start(r('preparation'))).toBe(true);
  expect(a.transfer('preparation', r('claim:1'))).toBe(true);
  expect(a.has_requests(['preparation'])).toBe(false);
  const turn = { conversation_id: 'c', turn_number: 1 };
  a.bind(['claim:1'], turn);
  a.observe({ ...turn, turn_number: 2 });
  expect(a.has_requests(['claim:1'])).toBe(true);
  a.observe({ ...turn });
  expect(a.has_requests(['claim:1'])).toBe(false);
  expect(a.try_start(r('next'))).toBe(false);
});

test('failed preparation retries while attempted delivery stays uncertain until exact settlement', () => {
  const db = fixture();
  const { admission: a, request: r, tick } = setup();
  const turn = { conversation_id: 'c', turn_number: 1 };
  expect(a.try_start(r('prepare'))).toBe(true);
  a.bind(['prepare'], turn);
  db.write((tx) => write_bindings(tx, turn, a.requests_for(['prepare'])));
  a.failed_attempt(db, { ...turn });
  expect(a.try_start(r('prepare'))).toBe(false);
  tick();
  expect(a.begin_queued(['prepare'])).toBe(true);
  a.bind(['prepare'], turn);
  a.persist_bindings(db, turn);
  const source = {};
  a.bind_source({ ...turn }, source);
  a.failed_attempt(db, { ...turn });
  expect(a.snapshots()[0].state).toBe('uncertain');
  a.retain_deferred('prepare', new Set());
  expect(a.has_requests(['prepare'])).toBe(true);
  a.retire_source(db, {});
  expect(a.has_requests(['prepare'])).toBe(true);
  a.retire_source(db, source);
  expect(a.snapshots()).toEqual([]);
});

test('explicit start exemption remains scoped through async work', async () => {
  expect(manual_start_exempt()).toBe(false);
  await explicit_start(async () => {
    expect(manual_start_exempt()).toBe(true);
    await Promise.resolve();
    expect(manual_start_exempt()).toBe(true);
  });
  expect(manual_start_exempt()).toBe(false);
});
