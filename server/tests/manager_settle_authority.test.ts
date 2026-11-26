import { expect, test } from 'vitest';
import { CHIEF, OWNER, type Principal } from '../core/contracts.ts';
import { set_manager_settled } from '../managers/data.ts';
import { read_manager } from '../managers/data.ts';
import { fixture, manager, now, worker } from './ticket_fixture.ts';

// Distinct risk: the settle gate is one function. It must pass the owner and the Chief,
// and refuse a Manager and a Worker. No other test reaches this function with those actors.
test('the owner and the Chief settle and reopen a Manager; a Manager and a Worker cannot', () => {
  const db = fixture(),
    id = manager(db, 'Target'),
    other = manager(db, 'Other'),
    clock = { nowUnix: () => now } as never;
  const settle = (principal: Principal, settled: boolean, target = id) =>
    db.write((tx) => set_manager_settled(tx, target, { settled, principal, clock }).settled);
  const state = () => db.read((sql) => read_manager(sql, id).item.settled);

  expect(settle(CHIEF, true)).toBe(true);
  expect(state()).toBe(true);
  expect(settle(CHIEF, false)).toBe(false);
  expect(settle(OWNER, true)).toBe(true);
  expect(settle(OWNER, false)).toBe(false);

  for (const principal of [{ kind: 'manager', id: other } as Principal, worker('t_any')])
    expect(() => settle(principal, true), principal.kind).toThrow(
      expect.objectContaining({ code: 'agent_forbidden' }),
    );
  expect(() => settle({ kind: 'manager', id }, true)).toThrow(
    expect.objectContaining({ code: 'agent_forbidden' }),
  );
  expect(state()).toBe(false);
  expect(() => settle(CHIEF, true, 'chief')).toThrow(
    expect.objectContaining({ code: 'validation' }),
  );
});
