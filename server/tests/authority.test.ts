import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { expect, test } from 'vitest';
import Fastify from 'fastify';
import { createDatabase } from '../core/database/index.ts';
import { registerArea } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
// Distinct risk: a new area can bypass rank admission or lose matched parameter names.
// Inject exercises Fastify's real plugin/hook boundary without a second server host.
test('registered area applies rank admission for owner, Worker, Manager, and Chief', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-authority-')),
    path = join(dir, 'db.sqlite');
  execFileSync('python3', [
    '-c',
    `import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);c.executescript("CREATE TABLE managers(id TEXT PRIMARY KEY); CREATE TABLE tickets(id TEXT PRIMARY KEY,manager_kind TEXT,manager_id TEXT); INSERT INTO managers VALUES ('m'); INSERT INTO managers VALUES ('other'); INSERT INTO tickets VALUES ('t','manager','m');");c.close()`,
    path,
  ]);
  const db = createDatabase(path),
    app = Fastify();
  app.setErrorHandler((error, _, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_FAKE_NOW: '2026-11-11T10:00:00+00:00',
  });
  registerArea(
    app,
    {
      prefix: '/api',
      routes: async (scope, services) => {
        scope.patch('/tickets/:ticket_id', async (request) => ({
          principal: services.identity(request).principal,
        }));
        scope.post('/projects', async () => ({ ok: true }));
        scope.post('/tickets/:ticket_id/request-help', async () => ({ ok: true }));
      },
    },
    { database: db, config, clock: buildClock(config) },
  );
  try {
    const owner = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      payload: { title: 'new' },
    });
    expect(owner.statusCode).toBe(200);
    const chief = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: { 'x-plan-actor': 'chief' },
      payload: { title: 'new' },
    });
    expect(chief.statusCode).toBe(200);
    const manager = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: { 'x-plan-actor': 'manager', 'x-plan-manager-id': 'm' },
      payload: { title: 'new' },
    });
    expect(manager.statusCode).toBe(200);
    const worker = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: { 'x-plan-actor': 'worker', 'x-plan-ticket-id': 't' },
      payload: { recap: 'mine' },
    });
    expect(worker.statusCode).toBe(200);
    const denied = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: { 'x-plan-actor': 'worker', 'x-plan-ticket-id': 't' },
      payload: { title: 'new' },
    });
    expect(denied.statusCode).toBe(400);
    expect(denied.json()).toEqual({
      error: {
        code: 'agent_forbidden',
        message: 'this principal does not stand above what it asked to act on',
        detail: {
          actor: 'worker',
          principal_kind: 'ticket',
          principal_id: 't',
          target_kind: 'ticket',
          target_id: 't',
          target_fields: [],
        },
      },
    });
    expect(
      (
        await app.inject({
          method: 'PATCH',
          url: '/api/tickets/t',
          headers: { 'x-plan-actor': 'manager', 'x-plan-manager-id': 'other' },
          payload: { title: 'new' },
        })
      ).statusCode,
    ).toBe(400);
    // Above the Ticket is not the Ticket itself: even owner and Chief cannot ask as it.
    for (const headers of [{}, { 'x-plan-actor': 'chief' }])
      expect(
        (
          await app.inject({
            method: 'POST',
            url: '/api/tickets/t/request-help',
            headers,
            payload: {},
          })
        ).statusCode,
      ).toBe(400);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/projects',
          headers: { 'x-plan-actor': 'worker', 'x-plan-ticket-id': 't' },
          payload: {},
        })
      ).statusCode,
    ).toBe(400);
  } finally {
    await app.close();
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
