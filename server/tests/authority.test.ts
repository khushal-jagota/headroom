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
import { installCompatibility } from '../core/http/compatibility.ts';
import { installMiddleware } from '../core/http/middleware.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { send_message, sender_label } from '../message_delivery/service.ts';
import { PromptDeliveryStarted, type ConversationSystem } from '../conversation/contracts.ts';
// Distinct risk: a new area can bypass rank admission or lose matched parameter names.
// Inject exercises Fastify's real plugin/hook boundary without a second server host.
test('registered area applies rank admission for owner, Worker, Manager, and Chief', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-authority-')),
    path = join(dir, 'db.sqlite');
  execFileSync('python3', [
    '-c',
    `import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);c.executescript("CREATE TABLE managers(id TEXT PRIMARY KEY); CREATE TABLE tickets(id TEXT PRIMARY KEY,manager_kind TEXT,manager_id TEXT); CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT); INSERT INTO managers VALUES ('m'); INSERT INTO managers VALUES ('other'); INSERT INTO tickets VALUES ('t','manager','m');");c.close()`,
    path,
  ]);
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
    const named = await app.inject({
      url: '/api/identity-proof',
      headers: {
        'x-plan-actor': 'manager',
        'x-plan-manager-id': 'm',
        'x-plan-conversation-id': 'agent',
      },
    });
    expect(named.json()).toEqual({
      principal: { kind: 'manager', id: 'm' },
      conversationId: 'agent',
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
    // The existing update transport retains its explicit identity headers.
    const update = await app.inject({ url: '/api/updates/identity-proof', headers: owner_headers });
    expect(update.json()).toEqual({
      principal: { kind: 'manager', id: 'm' },
      conversationId: 'spoofed',
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
