import { launch_headers } from '../testing/launch_identity.ts';
import {
  authenticate_launch_token,
  issue_launch_token,
  revoke_launch_tokens,
} from '../agent_identity/data.ts';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import { expect, test } from 'vitest';
import Fastify from 'fastify';
import { createDatabase } from '../core/database/index.ts';
import { registerArea } from '../core/authority/register.ts';
import { conversation_routes } from '../tickets/conversation_routes.ts';
import { decision_routes } from '../tickets/decision_routes.ts';
import { installCompatibility } from '../core/http/compatibility.ts';
import { DESKTOP_APP_ORIGIN, installMiddleware } from '../core/http/middleware.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { send_message, sender_label } from '../message_delivery/service.ts';
import { PromptDeliveryStarted, type ConversationSystem } from '../conversation/contracts.ts';
// Distinct risk: a new area can bypass rank admission or lose matched parameter names.
// Inject exercises Fastify's real plugin/hook boundary without a second server host.
test('registered area applies rank admission for owner, Worker, Manager, and Chief', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-authority-')),
    path = join(dir, 'db.sqlite');
  const setup = new BetterSqlite3(path);
  setup.exec(
    `CREATE TABLE recipient_lifecycles(recipient_kind TEXT,recipient_id TEXT,generation INTEGER,retiring INTEGER); CREATE TABLE managers(id TEXT PRIMARY KEY); CREATE TABLE tickets(id TEXT PRIMARY KEY,manager_kind TEXT,manager_id TEXT,conversation_id TEXT); CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT); CREATE TABLE agent_launch_tokens(token_hash TEXT PRIMARY KEY,conversation_id TEXT UNIQUE); INSERT INTO managers VALUES ('m'); INSERT INTO managers VALUES ('other'); INSERT INTO tickets VALUES ('t','manager','m','c_worker'); INSERT INTO agents VALUES ('chief_of_staff','c_chief'),('manager_m','c_manager'),('manager_other','c_other');`,
  );
  setup.close();
  const db = createDatabase(path),
    app = Fastify();
  installCompatibility(app);
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_FAKE_NOW: '2026-11-11T10:00:00+00:00',
  });
  installMiddleware(app, config);
  registerArea(
    app,
    {
      prefix: '/api',
      routes: async (scope, services) => {
        scope.patch('/tickets/:ticket_id', async (request) => ({
          principal: services.identity(request).principal,
        }));
        scope.post('/projects', async () => ({ ok: true }));
        scope.get('/identity-proof', async (request) => services.identity(request));
        scope.post('/measures/read', async () => ({ ok: true }));
        scope.post('/datasets/read', async () => ({ ok: true }));
        scope.post('/messages/send', async (request) => services.identity(request));
        scope.get('/updates/identity-proof', async (request) => services.identity(request));
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
    // The same mounted admission host now exercises the hosted configuration.
    // Existing rank cases do not prove that an unnamed hosted request is nobody.
    config.trusted_ingress_provider = 'tailscale';
    config.trusted_ingress_allowed_login = 'owner@example.test';
    config.trusted_ingress_canonical_origin = 'https://panels.example';
    const unidentified = { kind: 'unidentified' as const, id: 'unidentified' };
    const context = { principal: unidentified, conversationId: null };
    expect(sender_label(context)).toBe('Unidentified caller');
    const conversations = {} as ConversationSystem;
    await expect(
      send_message(
        conversations,
        db,
        buildClock(config),
        context,
        { kind: 'owner', id: 'owner' },
        'Help',
      ),
    ).rejects.toMatchObject({ code: 'validation', message: 'unsupported message principal kind' });
    // A cached admission exercises the service exception without another host or a provider.
    const chief_recipient = { kind: 'chief' as const, id: 'chief' };
    const prior = {
      recipient: chief_recipient,
      conversation_id: null,
      fate: new PromptDeliveryStarted(),
    };
    expect(
      await send_message(
        conversations,
        db,
        buildClock(config),
        context,
        chief_recipient,
        'Help',
        'queue',
        {
          admission: { preflight: async () => prior, finish() {}, abort_before_dispatch() {} },
        },
      ),
    ).toBe(prior);
    expect((await app.inject('/api/identity-proof')).json().principal).toEqual(unidentified);
    const refused = await app.inject({ method: 'POST', url: '/api/projects', payload: {} });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.code).toBe('agent_forbidden');
    for (const url of ['/api/measures/read', '/api/datasets/read'])
      expect((await app.inject({ method: 'POST', url, payload: {} })).statusCode).toBe(200);
    const chief_message = await app.inject({
      method: 'POST',
      url: '/api/messages/send',
      payload: { target: { kind: 'chief', id: 'chief' }, message: 'Help' },
    });
    expect(chief_message.statusCode).toBe(200);
    expect(chief_message.json().principal).toEqual(unidentified);
    const foreign_message = await app.inject({
      method: 'POST',
      url: '/api/messages/send',
      payload: { target: { kind: 'ticket', id: 't' }, message: 'Help' },
    });
    expect(foreign_message.statusCode).toBe(400);
    expect(foreign_message.json().error.code).toBe('agent_forbidden');
    const malformed = {
      method: 'POST' as const,
      url: '/api/messages/send',
      headers: { 'content-type': 'application/json' },
      payload: '{',
    };
    expect((await app.inject(malformed)).json().error.code).toBe('agent_forbidden');
    const authenticated_malformed = await app.inject({
      ...malformed,
      headers: { ...malformed.headers, ...managerHeaders },
    });
    expect(authenticated_malformed.statusCode).toBe(422);
    expect(authenticated_malformed.json().detail[0].type).toBe('json_invalid');
    const invalid_malformed = await app.inject({
      ...malformed,
      headers: { ...malformed.headers, authorization: 'Bearer invalid' },
    });
    expect(invalid_malformed.statusCode).toBe(400);
    expect(invalid_malformed.json().error.code).toBe('agent_forbidden');
    const named = await app.inject({
      url: '/api/identity-proof',
      headers: {
        ...managerHeaders,
        'x-plan-actor': 'chief',
        'x-plan-conversation-id': 'spoofed',
      },
    });
    expect(named.json()).toEqual({
      principal: { kind: 'manager', id: 'm' },
      conversationId: 'c_manager',
    });
    const owner_headers = {
      'tailscale-user-login': 'owner@example.test',
      'x-plan-actor': 'manager',
      'x-plan-manager-id': 'm',
      'x-plan-conversation-id': 'spoofed',
    };
    const trusted_owner = await app.inject({ url: '/api/identity-proof', headers: owner_headers });
    expect(trusted_owner.json()).toEqual({
      principal: { kind: 'owner', id: 'owner' },
      conversationId: null,
    });
    // Owner ingress wins legacy role claims on update routes too.
    const update = await app.inject({ url: '/api/updates/identity-proof', headers: owner_headers });
    expect(update.json()).toEqual({
      principal: { kind: 'owner', id: 'owner' },
      conversationId: null,
    });
    const owner_malformed = await app.inject({
      ...malformed,
      headers: { ...malformed.headers, 'tailscale-user-login': 'owner@example.test' },
    });
    expect(owner_malformed.statusCode).toBe(422);
    expect(owner_malformed.json().detail[0].type).toBe('json_invalid');
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

// Distinct risk: the desktop app cannot write or stream, or another origin gains CORS access.
// Existing cases send no Origin. Inject covers the real hook, including a hijacked stream.
test('the desktop app origin may write and stream; other origins stay refused', async () => {
  const app = Fastify();
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: join(tmpdir(), 'panels-desktop-origin-unused.sqlite'),
    PLAN_TEST_MODE: '1',
  });
  installMiddleware(app, config);
  app.post('/api/write', async () => ({ ok: true }));
  app.get('/api/stream', async (_request, reply) => {
    reply.hijack();
    reply.raw.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8' });
    reply.raw.end(': keep-alive\n\n');
  });
  const app_origin = { origin: DESKTOP_APP_ORIGIN };
  const allowed = (response: { headers: Record<string, unknown> }) =>
    response.headers['access-control-allow-origin'];
  try {
    for (const hosted of [false, true]) {
      config.trusted_ingress_provider = hosted ? 'tailscale' : null;
      config.trusted_ingress_allowed_login = hosted ? 'owner@example.test' : null;
      config.trusted_ingress_canonical_origin = hosted ? 'https://panels.example' : null;
      const write = await app.inject({
        method: 'POST',
        url: '/api/write',
        headers: app_origin,
        payload: {},
      });
      expect(write.statusCode).toBe(200);
      expect(allowed(write)).toBe(DESKTOP_APP_ORIGIN);
      const stream = await app.inject({ url: '/api/stream', headers: app_origin });
      expect(allowed(stream)).toBe(DESKTOP_APP_ORIGIN);
      const preflight = await app.inject({
        method: 'OPTIONS',
        url: '/api/write',
        headers: {
          ...app_origin,
          'access-control-request-method': 'POST',
          'access-control-request-headers': 'content-type,x-panels-artifact-root',
        },
      });
      expect(preflight.statusCode).toBe(204);
      expect(preflight.headers).toMatchObject({
        'access-control-allow-origin': DESKTOP_APP_ORIGIN,
        'access-control-allow-methods': 'POST',
        'access-control-allow-headers': 'content-type,x-panels-artifact-root',
      });
      const foreign_preflight = await app.inject({
        method: 'OPTIONS',
        url: '/api/write',
        headers: { origin: 'https://other.example', 'access-control-request-method': 'POST' },
      });
      expect(foreign_preflight.statusCode).toBe(404);
      expect(allowed(foreign_preflight)).toBeUndefined();
    }
    const foreign = await app.inject({
      method: 'POST',
      url: '/api/write',
      headers: { origin: 'https://other.example' },
      payload: {},
    });
    expect(foreign.statusCode).toBe(403);
    expect(allowed(foreign)).toBeUndefined();
    const browser = await app.inject({
      method: 'POST',
      url: '/api/write',
      headers: { origin: 'https://panels.example' },
      payload: {},
    });
    expect(browser.statusCode).toBe(200);
    expect(allowed(browser)).toBeUndefined();
  } finally {
    await app.close();
  }
});
