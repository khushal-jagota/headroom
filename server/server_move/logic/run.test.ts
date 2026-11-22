/**
 * New rules, no existing coverage: which moves may start, when the old server commits, and
 * what a restart does. A wrong answer here moves a server that should not move, or splits it.
 */
import { expect, test } from 'vitest';
import { validate_server_address } from '../../server_link/logic.ts';
import { SERVER_MOVE_PAUSE_LIMIT_SECONDS } from '../contracts.ts';
import {
  held_commit_state,
  new_attempt,
  new_server_took_over,
  parse_move_request,
  report_decision,
  start_refusal,
  startup_recovery,
  transition,
} from './run.ts';

const revision = 'a'.repeat(40);
const ready = {
  update_running: false,
  move_running: false,
  target_known: true,
  target_runs_server: false,
  target_present: true,
  target_revision: revision,
  target_platform: 'darwin',
  server_revision: revision,
  server_active: true,
  requested_url: 'https://new.example',
  own_addresses: ['https://old.example', null],
};

test('a move starts only for a present, current, non-server machine while nothing else runs', () => {
  expect(start_refusal(ready)).toBeNull();
  for (const [change, refusal] of [
    [{ update_running: true }, /update is running/],
    [{ move_running: true }, /Another server move/],
    [{ target_runs_server: true }, /already runs on this machine/],
    [{ target_present: false }, /away/],
    [{ target_revision: 'b'.repeat(40) }, /out of date/],
    [{ target_revision: null }, /out of date/],
    [{ target_platform: 'windows' }, /no server package/],
    [{ server_revision: null }, /no released revision/],
    [{ requested_url: 'https://old.example' }, /address of this server/],
  ] as const)
    expect(start_refusal({ ...ready, ...change })).toMatch(refusal);
  expect(
    parse_move_request({ server_url: 'https://new.example/', port: 8767 }, validate_server_address),
  ).toEqual({
    server_url: 'https://new.example',
    port: 8767,
  });
  for (const body of [
    { server_url: 'http://new.example', port: 8767 },
    { server_url: '/tmp/server.sock', port: 8767 },
    { server_url: 'https://new.example', port: 0 },
    { server_url: 'https://new.example', port: 65536 },
    { server_url: 'https://new.example', port: 80, extra: true },
  ])
    expect(() => parse_move_request(body, validate_server_address)).toThrow();
});

test('the old server commits only on the same identity at N within the pause limit, and never fails after', () => {
  const location = {
    server_id: 'server',
    move_number: 4,
    server_url: null,
    state: 'active' as const,
  };
  const copying = transition(
    new_attempt({
      move_id: 'move',
      target_machine_id: 'target',
      request: { server_url: 'https://new.example', port: 8767 },
      platform: 'darwin',
      revision,
      location,
      trusted_ingress: null,
      now_ms: 0,
    }),
    'copying',
    1,
  );
  const paused = transition(copying, 'paused', 2, { paused_at_ms: 1000 });
  const answer = { ...location, server_url: 'https://new.example' };
  const held = { move_id: 'move' };
  expect(report_decision(paused, answer, held, 2000)).toEqual({ commit: true });
  expect(report_decision(copying, answer, held, 2000).commit).toBe(false);
  expect(report_decision(paused, null, held, 2000).commit).toBe(false);
  // The old server answers its own link the same way; only the held identity tells them apart.
  expect(report_decision(paused, answer, null, 2000).commit).toBe(false);
  expect(report_decision(paused, answer, { move_id: 'other' }, 2000).commit).toBe(false);
  expect(report_decision(paused, { ...answer, server_id: 'other' }, held, 2000).commit).toBe(false);
  expect(report_decision(paused, { ...answer, move_number: 5 }, held, 2000).commit).toBe(false);
  expect(report_decision(paused, { ...answer, state: 'moved' }, held, 2000).commit).toBe(false);
  expect(
    report_decision(paused, answer, held, 1000 + SERVER_MOVE_PAUSE_LIMIT_SECONDS * 1000).commit,
  ).toBe(false);
  const committing = transition(paused, 'committing', 3);
  expect(() => transition(committing, 'failed', 4)).toThrow();
  expect(new_server_took_over(committing, answer)).toBe(false);
  expect(new_server_took_over(committing, { ...answer, move_number: 5 })).toBe(true);
  expect(
    [copying, paused, committing, transition(committing, 'moved', 4)].map(startup_recovery),
  ).toEqual(['fail', 'fail', 'probe', 'none']);
  const marker = {
    move_id: 'move',
    target_machine_id: 'target',
    server_url: 'https://new.example',
    server_id: 'server',
    move_number: 4,
  };
  expect(held_commit_state(marker, location)).toBe('commit');
  expect(held_commit_state(marker, { ...answer, move_number: 5 })).toBe('done');
  expect(() => held_commit_state(marker, { ...location, server_id: 'other' })).toThrow();
  expect(() => held_commit_state(marker, { ...location, move_number: 3 })).toThrow();
});
