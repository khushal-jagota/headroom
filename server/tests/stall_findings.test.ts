/** The 14 existing findings tests, with their original parameter cases and simulated clock. */
import { expect, test } from 'vitest';
import * as configuration from '../stalls/configuration.ts';
import { PAUSED_WHILE_MAC_AWAY, TRACKED_WAIT_KINDS, finding_key } from '../stalls/contracts.ts';
import type { TrackedWaitKind, TrackedWait } from '../stalls/contracts.ts';
import type { StallLease, StallPresenceRecord } from '../executor/contracts.ts';
import {
  empty_observation,
  forget_present_time_before,
  overdue_waits,
  presence_waits,
} from '../stalls/logic/findings.ts';
import type { PresenceObservation } from '../stalls/logic/findings.ts';

const S = 1000;
const CHECK_MS = configuration.STALL_CHECK_INTERVAL_SECONDS * S;
// The existing executor lease timings are 10, 25, and 10 seconds. Unit 22 owns their TS port.
const LEASE_INTERVAL_SECONDS = 10;
const LEASE_CONTINUITY_SECONDS = 25;
const CONTACT_FLUSH_INTERVAL_SECONDS = 10;
const LEASE_MS = LEASE_INTERVAL_SECONDS * S;
const IDENTITY = {
  protocol_version: 22,
  application_revision: 'rev',
  spool_format_version: 5,
  connection_generation: 1,
  executor_incarnation: 'incarnation-1',
};
const WATCHDOG_TIMER_ACCURACY_SECONDS = 5;
function lease(
  waiting: readonly (readonly [string, number])[] = [],
  working: StallLease['working_turns'] = [],
): StallLease {
  return {
    waiting_conversations: waiting.map(([conversation_id, oldest_sequence]) => ({
      conversation_id,
      oldest_sequence,
    })),
    working_turns: working,
  };
}
function present(
  since_ms: number,
  last_lease_ms: number,
  value: StallLease = lease(),
): StallPresenceRecord {
  return {
    executor_incarnation: IDENTITY.executor_incarnation,
    identity: IDENTITY,
    last_contact_at_ms: last_lease_ms,
    last_lease_at_ms: last_lease_ms,
    continuous_since_ms: since_ms,
    going_to_sleep_at_ms: null,
    lease: value,
    refusal: null,
  };
}
function wait(kind: TrackedWaitKind, started_at_ms: number, conversation_id = 'c1'): TrackedWait {
  return {
    kind,
    wait_key: `${conversation_id}:w`,
    conversation_id,
    detail: 'detail',
    started_at_ms,
  };
}
function present_for(first_ms: number, last_ms: number): PresenceObservation {
  return { ...empty_observation(), present_stretches: [[first_ms, last_ms]] };
}
test('production limits with leases checker and watchdog stay within five minutes', () => {
  const watchdog = configuration.WATCHDOG_INTERVAL_SECONDS + WATCHDOG_TIMER_ACCURACY_SECONDS;
  const first_sighting: Partial<Record<TrackedWaitKind, number>> = {
    mac_items_not_moving: LEASE_INTERVAL_SECONDS + configuration.STALL_CHECK_INTERVAL_SECONDS,
    mac_turn_silent: LEASE_INTERVAL_SECONDS,
    mac_lease_failing: CONTACT_FLUSH_INTERVAL_SECONDS + configuration.STALL_CHECK_INTERVAL_SECONDS,
  };
  const worst = new Map(
    TRACKED_WAIT_KINDS.map((kind) => [
      kind,
      (first_sighting[kind] ?? 0) +
        configuration.stall_limit_seconds(kind) +
        (PAUSED_WHILE_MAC_AWAY.has(kind) ? LEASE_INTERVAL_SECONDS : 0) +
        configuration.STALL_CHECK_INTERVAL_SECONDS +
        watchdog,
    ]),
  );
  expect(Math.max(...worst.values())).toBe(265);
  expect(worst.get('mac_items_not_moving')).toBe(265);
  expect([...worst.values()].every((seconds) => seconds <= 300)).toBe(true);
  expect(configuration.CHECKER_HEARTBEAT_STALE_SECONDS + watchdog).toBeLessThanOrEqual(300);
});
test.each(TRACKED_WAIT_KINDS)('each kind alarms at its limit and not before: %s', (kind) => {
  const record = present(0, 0);
  const limit_ms = configuration.stall_limit_seconds(kind) * S;
  const item = wait(kind, 0);
  const before = limit_ms - 1;
  expect(overdue_waits([item], record, before, present_for(0, before))).toEqual([]);
  expect(overdue_waits([item], record, limit_ms, present_for(0, limit_ms))).toEqual([
    { wait: item, stalled_since_ms: 0 },
  ]);
});
test('Mac kinds count only present time and VPS kinds do not pause', () => {
  const long_ago = -3600 * S;
  const waits = TRACKED_WAIT_KINDS.map((kind) => wait(kind, long_ago, kind));
  const vps_only = new Set(TRACKED_WAIT_KINDS.filter((kind) => !PAUSED_WHILE_MAC_AWAY.has(kind)));
  const asleep = { ...present(long_ago, 0), going_to_sleep_at_ms: 0 };
  expect(
    new Set(
      overdue_waits(waits, asleep, 10 * S, empty_observation()).map((item) => item.wait.kind),
    ),
  ).toEqual(vps_only);
  expect(vps_only.has('vps_replacement_retiring')).toBe(true);
  const returned = present(0, 60 * S);
  expect(
    new Set(
      overdue_waits(waits, returned, 60 * S, present_for(0, 60 * S)).map((item) => item.wait.kind),
    ),
  ).toEqual(vps_only);
  const command = wait('mac_command_unfetched', long_ago);
  const at_limit = configuration.COMMAND_UNFETCHED_LIMIT_SECONDS * S;
  expect(overdue_waits([command], returned, at_limit, present_for(0, at_limit))).toEqual([
    { wait: command, stalled_since_ms: long_ago },
  ]);
});
test('the VPS retire step replaces its drained runs pending wait', () => {
  const pending: TrackedWait = {
    kind: 'mac_replacement_pending',
    wait_key: 'c1',
    conversation_id: 'c1',
    detail: 'rows',
    started_at_ms: 0,
  };
  const retiring: TrackedWait = {
    kind: 'vps_replacement_retiring',
    wait_key: 'c1',
    conversation_id: 'c1',
    detail: 'retire',
    started_at_ms: 5 * S,
  };
  const now_ms = 5 * S + configuration.VPS_REPLACEMENT_RETIRING_LIMIT_SECONDS * S;
  const back = present_for(0, now_ms);
  expect(overdue_waits([pending, retiring], null, now_ms, back)).toEqual([
    { wait: retiring, stalled_since_ms: 5 * S },
  ]);
  expect(overdue_waits([pending], null, now_ms, back)).toEqual([
    { wait: pending, stalled_since_ms: 0 },
  ]);
});
type Mac = (now_ms: number) => StallPresenceRecord | null;
type Waits = (now_ms: number) => readonly TrackedWait[];
interface Episode {
  finding_key: string;
  stalled_since_ms: number;
  opened_at_ms: number;
  cleared_at_ms: number | null;
}
function simulate(mac: Mac, until_ms: number, waits: Waits = () => []): Episode[] {
  let observation = empty_observation();
  const episodes = new Map<string, Episode>();
  const every: Episode[] = [];
  for (let now_ms = 0; now_ms <= until_ms; now_ms += CHECK_MS) {
    const record = mac(now_ms);
    const [lease_waits, next] = presence_waits(record, now_ms, observation);
    observation = next;
    const all_waits = [...waits(now_ms), ...lease_waits];
    const overdue = overdue_waits(all_waits, record, now_ms, observation);
    for (const item of overdue) {
      const key = finding_key(item.wait);
      const episode = episodes.get(key);
      if (!episode) {
        const created = {
          finding_key: key,
          stalled_since_ms: item.stalled_since_ms,
          opened_at_ms: now_ms,
          cleared_at_ms: null,
        };
        episodes.set(key, created);
        every.push(created);
      } else expect(episode.stalled_since_ms).toBe(item.stalled_since_ms);
    }
    const keys = new Set(overdue.map((item) => finding_key(item.wait)));
    for (const [key, episode] of episodes) {
      if (!keys.has(key)) {
        episode.cleared_at_ms = now_ms;
        episodes.delete(key);
      }
    }
    observation = {
      ...forget_present_time_before(observation, all_waits),
      open_findings: new Map(
        overdue.map((item) => [finding_key(item.wait), item.stalled_since_ms]),
      ),
    };
  }
  return every;
}
function leasing_mac(
  awake: (t: number) => boolean,
  oldest: (t: number) => number,
  every_ms = LEASE_MS,
): Mac {
  return (now_ms) => {
    const leases: number[] = [];
    for (let t = 0; t <= now_ms; t += every_ms) if (awake(t)) leases.push(t);
    if (!leases.length) return null;
    const last = leases.at(-1)!;
    let since = last;
    for (const earlier of leases.slice(0, -1).reverse()) {
      if (since - earlier > LEASE_CONTINUITY_SECONDS * S) break;
      since = earlier;
    }
    return present(since, last, lease([['c1', oldest(last)]]));
  };
}
function command_from(started_at_ms: number): Waits {
  const command: TrackedWait = {
    kind: 'mac_command_unfetched',
    wait_key: 'cmd',
    conversation_id: 'c1',
    detail: 'x',
    started_at_ms,
  };
  return (now_ms) => (now_ms >= started_at_ms ? [command] : []);
}
const ITEM = 'mac_items_not_moving:c1:7';
const COMMAND = 'mac_command_unfetched:cmd';
test('a night of dark wakes raises nothing', () => {
  const mac = leasing_mac(
    (t) => t % (120 * S) < 15 * S,
    () => 7,
  );
  expect(simulate(mac, 8 * 3600 * S, command_from(0))).toEqual([]);
});
test('a draining backlog raises nothing and a stuck one alarms', () => {
  const draining = leasing_mac(
    () => true,
    (t) => 1 + Math.floor(t / LEASE_MS),
  );
  expect(simulate(draining, 30 * 60 * S)).toEqual([]);
  const stuck = leasing_mac(
    () => true,
    () => 7,
  );
  const episodes = simulate(stuck, 30 * 60 * S);
  expect(episodes).toHaveLength(1);
  const episode = episodes[0];
  expect([episode.finding_key, episode.cleared_at_ms]).toEqual([ITEM, null]);
  const limit_ms = configuration.MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS * S;
  expect(episode.opened_at_ms).toBeGreaterThanOrEqual(limit_ms);
  expect(episode.opened_at_ms).toBeLessThanOrEqual(limit_ms + LEASE_MS + CHECK_MS);
});
const intermittent_cases: readonly [string, Mac, number][] = [
  [
    'lossy link',
    leasing_mac(
      () => true,
      () => 7,
      30 * S,
    ),
    0,
  ],
  [
    'awake 150 s of 180 s',
    leasing_mac(
      (t) => t % (180 * S) < 150 * S,
      () => 7,
    ),
    0,
  ],
  [
    'awake 100 s of 130 s',
    leasing_mac(
      (t) => t % (130 * S) < 100 * S,
      () => 7,
    ),
    0,
  ],
  [
    'awake 100 s of 400 s',
    leasing_mac(
      (t) => t % (400 * S) < 100 * S,
      () => 7,
    ),
    465,
  ],
];
test.each(intermittent_cases)(
  'a stuck command and item alarm on a lossy or intermittent link: %s',
  (_case, mac, alarm_by_s) => {
    const episodes = new Map(
      simulate(mac, 2 * 3600 * S, command_from(0)).map((episode) => [episode.finding_key, episode]),
    );
    expect(new Set(episodes.keys())).toEqual(new Set([COMMAND, ITEM]));
    expect([...episodes.values()].every((episode) => episode.cleared_at_ms === null)).toBe(true);
    expect(episodes.get(COMMAND)!.stalled_since_ms).toBe(0);
    if (alarm_by_s === 0) alarm_by_s = configuration.COMMAND_UNFETCHED_LIMIT_SECONDS + 30 + 15;
    expect(episodes.get(COMMAND)!.opened_at_ms).toBeLessThanOrEqual(alarm_by_s * S);
  },
);
test('an alarmed stall stays one finding while the Mac sleeps and returns', () => {
  const mac = leasing_mac(
    (t) => !(600 * S <= t && t < 4200 * S),
    (t) => (t < 5400 * S ? 7 : Math.floor(t / LEASE_MS)),
  );
  const command = command_from(0);
  const episodes = simulate(mac, 100 * 60 * S, (now_ms) => command(now_ms));
  expect(
    episodes.map((episode) => [
      episode.finding_key,
      episode.stalled_since_ms,
      episode.cleared_at_ms,
    ]),
  ).toEqual([
    [COMMAND, 0, null],
    [ITEM, 0, 5400 * S],
  ]);
});
function failing_mac(options: {
  last_accepted_ms: number | null;
  contact_from_ms: number;
  every_ms?: number;
  awake?: (t: number) => boolean;
  refusal?: string | null;
}): Mac {
  return (now_ms) => {
    const attempts: number[] = [];
    for (
      let t = options.contact_from_ms;
      t <= now_ms - CONTACT_FLUSH_INTERVAL_SECONDS * S;
      t += options.every_ms ?? LEASE_MS
    )
      if ((options.awake ?? (() => true))(t)) attempts.push(t);
    const contact = attempts.at(-1) ?? options.last_accepted_ms;
    const refusal = options.refusal === undefined ? 'Mac needs update' : options.refusal;
    return {
      executor_incarnation: IDENTITY.executor_incarnation,
      identity: IDENTITY,
      last_contact_at_ms: contact,
      last_lease_at_ms: options.last_accepted_ms,
      continuous_since_ms: options.last_accepted_ms,
      going_to_sleep_at_ms: null,
      lease: null,
      refusal:
        refusal === null
          ? null
          : {
              reason: refusal,
              server_identity: IDENTITY,
              peer_identity: IDENTITY,
              refused_at_ms: options.contact_from_ms,
            },
    };
  };
}
test.each([LEASE_MS, 30 * S])(
  'failing leases alarm within the limit even on a lossy link: %s',
  (every_ms) => {
    for (const refusal of ['Mac needs update', null]) {
      const mac = failing_mac({ last_accepted_ms: 0, contact_from_ms: 10 * S, every_ms, refusal });
      const episodes = simulate(mac, 10 * 60 * S);
      expect(episodes).toHaveLength(1);
      expect(episodes[0].finding_key).toBe('mac_lease_failing:mac');
      expect(episodes[0].opened_at_ms).toBeLessThanOrEqual(
        configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S + CHECK_MS,
      );
    }
  },
);
test('leases accepted only minutes apart alarm as failing', () => {
  const leasing = leasing_mac(
    (t) => t < 600 * S || t % (70 * S) === 0,
    () => 7,
  );
  const mac: Mac = (now_ms) => {
    const record = leasing(now_ms)!;
    const attempt = Math.floor((now_ms - CONTACT_FLUSH_INTERVAL_SECONDS * S) / LEASE_MS) * LEASE_MS;
    return { ...record, last_contact_at_ms: Math.max(attempt, record.last_lease_at_ms ?? 0) };
  };
  const failing = simulate(mac, 30 * 60 * S).filter(
    (episode) => episode.finding_key === 'mac_lease_failing:mac',
  );
  expect(failing).toHaveLength(1);
  const episode = failing[0];
  expect(episode.stalled_since_ms).toBeLessThanOrEqual(
    600 * S + configuration.MAC_PRESENT_GAP_SECONDS * S,
  );
  expect(episode.opened_at_ms).toBeLessThanOrEqual(
    episode.stalled_since_ms + configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S + CHECK_MS,
  );
});
function napping_mac(cycle_ms: number, leases_per_wake: number): Mac {
  return (now_ms) => {
    const leases: number[] = [];
    for (let start = 0; start <= now_ms; start += cycle_ms)
      for (let i = 0; i < leases_per_wake; i++)
        if (start + i * LEASE_MS <= now_ms) leases.push(start + i * LEASE_MS);
    const last = leases.at(-1)!;
    const since = Math.floor(last / cycle_ms) * cycle_ms;
    const contact = last + 8 * S <= now_ms ? last + 8 * S : last;
    return { ...present(since, last, lease([['c1', 7]])), last_contact_at_ms: contact };
  };
}
test.each([1, 2])('short accepted stretches between naps raise nothing: %s', (leases_per_wake) => {
  const mac = napping_mac((leases_per_wake === 1 ? 62 : 75) * S, leases_per_wake);
  expect(simulate(mac, 3600 * S, command_from(0))).toEqual([]);
});
test('one refused lease after a long sleep does not alarm at once', () => {
  const wake = 3600 * S;
  const mac = failing_mac({ last_accepted_ms: 0, contact_from_ms: wake });
  const episodes = simulate(mac, wake + 10 * 60 * S);
  expect(episodes).toHaveLength(1);
  expect(episodes[0].opened_at_ms).toBeGreaterThanOrEqual(
    wake + configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S,
  );
});
test('an alarmed lease failure stays one finding across a sleep', () => {
  const mac = failing_mac({
    last_accepted_ms: 0,
    contact_from_ms: 10 * S,
    awake: (t) => !(600 * S <= t && t < 4200 * S),
  });
  const episodes = simulate(mac, 80 * 60 * S);
  expect(episodes).toHaveLength(1);
  expect([episodes[0].finding_key, episodes[0].cleared_at_ms]).toEqual([
    'mac_lease_failing:mac',
    null,
  ]);
});
test('a silent turn alarms unless its tool is running', () => {
  const turn = { child: { conversation_id: 'c1' }, tool_running: false };
  const silent = wait('mac_turn_silent', 0);
  const now_ms = configuration.MAC_TURN_SILENT_LIMIT_SECONDS * S;
  const observation = present_for(0, now_ms);
  const thinking = present(0, now_ms, lease([], [turn]));
  expect(overdue_waits([silent], thinking, now_ms, observation).map((item) => item.wait)).toEqual([
    silent,
  ]);
  const building = present(0, now_ms, lease([], [{ ...turn, tool_running: true }]));
  expect(overdue_waits([silent], building, now_ms, observation)).toEqual([]);
});
