import { launch_headers } from '../testing/launch_identity.ts';
import {
  authenticate_launch_token,
  issue_launch_token,
  revoke_launch_tokens,
} from '../agent_identity/data.ts';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { expect, test } from 'vitest';
import Fastify from 'fastify';
import { createDatabase } from '../core/database/index.ts';
import { registerArea } from '../core/authority/register.ts';
import { conversation_routes } from '../tickets/conversation_routes.ts';
import { decision_routes } from '../tickets/decision_routes.ts';
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
c=sqlite3.connect(sys.argv[1]);c.executescript("CREATE TABLE managers(id TEXT PRIMARY KEY); CREATE TABLE tickets(id TEXT PRIMARY KEY,manager_kind TEXT,manager_id TEXT,conversation_id TEXT); CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT); CREATE TABLE agent_launch_tokens(token_hash TEXT PRIMARY KEY,conversation_id TEXT UNIQUE); INSERT INTO managers VALUES ('m'); INSERT INTO managers VALUES ('other'); INSERT INTO tickets VALUES ('t','manager','m','c_worker'); INSERT INTO agents VALUES ('chief_of_staff','c_chief'),('manager_m','c_manager'),('manager_other','c_other');");c.close()`,
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
        await scope.register(conversation_routes, services);
        await scope.register(decision_routes, services);
      },
    },
    { database: db, config, clock: buildClock(config) },
  );
  const chiefHeaders = launch_headers(db, 'c_chief');
  const managerHeaders = launch_headers(db, 'c_manager');
  const workerHeaders = launch_headers(db, 'c_worker');
  const otherHeaders = launch_headers(db, 'c_other');
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
      headers: chiefHeaders,
      payload: { title: 'new' },
    });
    expect(chief.statusCode).toBe(200);
    const manager = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: managerHeaders,
      payload: { title: 'new' },
    });
    expect(manager.statusCode).toBe(200);
    const worker = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: workerHeaders,
      payload: { recap: 'mine' },
    });
    expect(worker.statusCode).toBe(200);
    // The actual operation plugin retains float token spelling before admission.
    const float_revision = await app.inject({
      method: 'POST',
      url: '/api/tickets/t/decision-operations/numeric',
      headers: { 'content-type': 'application/json' },
      payload: '{"decision":"approve","field":"brief","expected_proposal_revision":1.0}',
    });
    expect(float_revision.statusCode).toBe(400);
    expect(float_revision.json().error.message).toBe(
      'expected_proposal_revision must be a positive integer',
    );
    const denied = await app.inject({
      method: 'PATCH',
      url: '/api/tickets/t',
      headers: workerHeaders,
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
          headers: otherHeaders,
          payload: { title: 'new' },
        })
      ).statusCode,
    ).toBe(400);
    // Distinct credential risk: hashes persist, rotation revokes, and current associations govern authority.
    const original = chiefHeaders.authorization.slice('Bearer '.length);
    expect(
      db.read((sql) =>
        sql.get<{ token_hash: string }>(
          'SELECT token_hash FROM agent_launch_tokens WHERE conversation_id=?',
          ['c_chief'],
        ),
      )?.token_hash,
    ).not.toBe(original);
    const replacement = issue_launch_token(db, 'c_chief');
    expect(db.read((sql) => authenticate_launch_token(sql, original))).toBeNull();
    expect(db.read((sql) => authenticate_launch_token(sql, replacement))?.principal.kind).toBe(
      'chief',
    );
    db.write((sql) =>
      sql.run("UPDATE agents SET conversation_id='detached' WHERE agent_key='chief_of_staff'"),
    );
    expect(db.read((sql) => authenticate_launch_token(sql, replacement))).toBeNull();
    expect(() => issue_launch_token(db, 'c_chief')).toThrow(
      'conversation has no current agent identity',
    );
    revoke_launch_tokens(db, 'c_chief');
    expect(
      db.read((sql) =>
        sql.get('SELECT 1 FROM agent_launch_tokens WHERE conversation_id=?', ['c_chief']),
      ),
    ).toBeUndefined();
    for (const headers of [
      { 'x-plan-actor': 'chief' },
      { authorization: 'Bearer invalid' },
      { authorization: 'Basic invalid' },
    ]) {
      const response = await app.inject({
        method: 'PATCH',
        url: '/api/tickets/t',
        headers,
        payload: {},
      });
      expect(response.statusCode).toBe(400);
      expect(response.json().error.code).toBe('agent_forbidden');
    }
    // Register the real conversation plugin: the retired endpoint must stay absent.
    for (const headers of [{}, { 'x-plan-actor': 'worker', 'x-plan-ticket-id': 't' }])
      expect(
        (
          await app.inject({
            method: 'POST',
            url: '/api/tickets/t/request-help',
            headers,
            payload: {},
          })
        ).statusCode,
      ).toBe(404);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/projects',
          headers: workerHeaders,
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
