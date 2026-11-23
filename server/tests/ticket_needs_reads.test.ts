// Distinct risk: the list reads join need words, scope and paging in the route. A page cut before
// need filtering, a done Ticket counted as waiting, or a lost not_shown count each mislead every
// agent that reads them. Hand-written navigation records pin each Ticket's facts; no conversation.
import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { registerArea } from '../core/authority/register.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { installCompatibility } from '../core/http/compatibility.ts';
import { routes as tickets } from '../tickets/routes.ts';
import { routes as managers } from '../managers/routes.ts';
import { create, fixture, manager } from './ticket_fixture.ts';

const owner = { kind: 'owner', id: 'owner' };
async function setup() {
  const database = fixture();
  const held = manager(database, 'Held work');
  manager(database, 'Settled work');
  database.write((tx) => tx.run("UPDATE managers SET settled=1 WHERE title='Settled work'"));
  const facts: Record<string, Record<string, unknown>> = {
    approve: { ticket_status: 'awaiting_approval', updated_at: 100 },
    working: { ticket_status: 'agent', updated_at: 300 },
    ready_old: { ticket_status: 'empty', updated_at: 200 },
    ready_new: { ticket_status: 'empty', updated_at: 400 },
    backlog: { ticket_status: 'awaiting_approval', is_backlogged: true, updated_at: 500 },
    done: { ticket_status: 'awaiting_approval', terminal: true, stage: 'done', updated_at: 600 },
    both: { ticket_status: 'empty', is_backlogged: true, terminal: true, updated_at: 700 },
  };
  const ids: Record<string, string> = {};
  for (const [name, value] of Object.entries(facts)) {
    const ticket = create(database, { title: name, manager_id: held });
    ids[name] = ticket.id;
    database.write((tx) =>
      tx.run("INSERT OR REPLACE INTO navigation_records VALUES ('ticket',?,?)", [
        ticket.id,
        JSON.stringify({
          title: name,
          worker_type: 'coding',
          stage: 'needs_brief',
          manager_kind: 'manager',
          manager_id: held,
          ceiling_holder: owner,
          is_backlogged: false,
          terminal: false,
          blocked: false,
          stage_ownership: 'agent',
          created_at: value.updated_at,
          ...value,
        }),
      ]),
    );
  }
  const app = Fastify();
  installCompatibility(app);
  const config = loadConfig(undefined, { PLAN_TEST_MODE: '1' });
  for (const routes of [tickets, managers])
    registerArea(app, { prefix: '/api', routes }, { database, config, clock: buildClock(config) });
  await app.ready();
  const get = async (path: string) => (await app.inject(`/api${path}`)).json();
  const names = (body: { tickets: { id: string; needs: string }[] }) =>
    body.tickets.map((row) => `${Object.keys(ids).find((k) => ids[k] === row.id)}:${row.needs}`);
  return { app, held, ids, get, names };
}

test('the live list orders by need, pages after need filtering, and counts what it hides', async () => {
  const f = await setup();
  try {
    const live = await f.get('/tickets?detail=summary&scope=live');
    expect(f.names(live)).toEqual([
      'approve:owner_approval',
      'working:working',
      'ready_new:ready',
      'ready_old:ready',
    ]);
    expect(live.tickets[0]).toMatchObject({ ceiling_holder: owner, blocked: false });
    expect(live.page.not_shown).toEqual({ backlog: 2, done: 1 });

    const ready = await f.get('/tickets?detail=summary&needs=ready&limit=1');
    expect(f.names(ready)).toEqual(['ready_new:ready']);
    expect(ready.page).toMatchObject({ match_count: 2, next_offset: 1 });

    // A hidden Ticket counts only when the needs filter would show it. Backlog wins over done.
    const hidden = await f.get('/tickets?detail=summary&needs=backlog');
    expect(hidden.tickets).toEqual([]);
    expect(hidden.page.not_shown).toEqual({ backlog: 2, done: 0 });

    const done = await f.get('/tickets?detail=summary&scope=done');
    expect(f.names(done)).toEqual(['done:done']);
    expect(done.tickets[0]).toMatchObject({ awaiting_approval: false });
    expect(done.page.not_shown).toBeUndefined();
    const backlog = await f.get('/tickets?detail=summary&scope=backlog');
    expect(f.names(backlog)).toEqual(['both:backlog', 'backlog:backlog']);

    // Search reads the stored field values, here the brief every fixture Ticket shares.
    const found = await f.get('/tickets?detail=summary&scope=all&needs=done&search=agreed%20brief');
    expect(f.names(found)).toEqual(['done:done']);

    const mixed = await f.app.inject('/api/tickets?detail=summary&scope=all&is_backlogged=true');
    expect(mixed.statusCode).toBe(400);

    const bad = await f.app.inject('/api/tickets?detail=summary&needs=waiting');
    expect(bad.statusCode).toBe(400);
    expect(bad.json().error.detail).toMatchObject({
      needs: 'waiting',
      allowed: expect.arrayContaining(['owner_approval', 'done']),
    });
  } finally {
    await f.app.close();
  }
});

// Distinct risk: the installed iPhone app cannot change with the server. Its three exact list
// requests must keep their old filtering and created_at order.
test('the iPhone list requests without scope or needs keep the flag behaviour', async () => {
  const f = await setup();
  try {
    const base = `/tickets?detail=summary&limit=30&offset=0`;
    const holder = `manager_kind=manager&manager_id=${f.held}`;
    const active = await f.get(`${base}&is_backlogged=false&${holder}`);
    expect(f.names(active)).toEqual([
      'approve:owner_approval',
      'ready_old:ready',
      'working:working',
      'ready_new:ready',
    ]);
    expect(active.page.not_shown).toBeUndefined();
    expect(f.names(await f.get(`${base}&is_backlogged=true&${holder}`))).toEqual([
      'backlog:backlog',
    ]);
    expect(f.names(await f.get(`${base}&include_terminal=true&stage=done&${holder}`))).toEqual([
      'done:done',
    ]);
  } finally {
    await f.app.close();
  }
});

test('Manager reads count needs over every Ticket and leave done and backlog out of waiting words', async () => {
  const f = await setup();
  try {
    const list = await f.get('/managers?detail=summary&settled=false');
    expect(list.managers).toHaveLength(1);
    expect(list.managers[0].ticket_needs).toEqual({
      owner_approval: 1,
      working: 1,
      ready: 2,
      backlog: 2,
      done: 1,
    });
    expect(list.managers[0].ticket_rollup).toBeUndefined();
    expect(list.page.not_shown).toEqual({ settled: 1 });
    const one = await f.get(`/managers?detail=full&id=${f.held}`);
    expect(one).toMatchObject({ ticket_needs: list.managers[0].ticket_needs, goals: [] });
  } finally {
    await f.app.close();
  }
});
