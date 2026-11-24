/** New boundary: Settings must not issue credentials before owner/config admission.
 * Reuse the synthetic Ticket database and Fastify injection, with no listener or provider.
 */
import Fastify from 'fastify';
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { OWNER, CHIEF, PlannerError, type Principal } from '../core/contracts.ts';
import type { VpsExecutorRegistry } from '../executor/registry.ts';
import { routes } from './routes.ts';
import { authenticate_executor } from './executor_credentials.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';

test('join command refuses invalid admission before issuance and registers after the grant commits', async () => {
  const database = fixture();
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(database),
  });
  const home = 'https://panels.example';
  const revision = 'a'.repeat(40);
  config.trusted_ingress_canonical_origin = home;
  config.app_sha = revision;
  let principal: Principal = OWNER;
  const app = Fastify();
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  const ensure = vi.fn(async (machine_id: string) => {
    expect(
      database.read((sql) =>
        sql.get<{ platform: string }>(
          'SELECT platform FROM machine_executor_credentials WHERE machine_id=?',
          [machine_id],
        ),
      )?.platform,
    ).toBe('darwin');
  });
  // This route awaits admission but does not consume the executor it returns.
  app.decorate('executor_registry', { ensure } as unknown as VpsExecutorRegistry);
  await app.register(routes, {
    database,
    config,
    clock: buildClock(config),
    identity: () => ({ principal, conversationId: null }),
  });
  const count = () =>
    database.read(
      (sql) => sql.get<{ count: number }>('SELECT count(*) AS count FROM machines')!.count,
    );
  const before = count();
  const request = (payload: Record<string, unknown> = { name: 'Build Mac' }) =>
    app.inject({ method: 'POST', url: '/machines/join-command', payload });
  try {
    principal = CHIEF;
    const forbidden = await request();
    expect(forbidden.statusCode).toBe(400);
    expect(forbidden.json().error.code).toBe('agent_forbidden');
    principal = OWNER;
    for (const payload of [{}, { name: 1 }, { name: 'Mac', platform: 'linux' }, { name: '' }])
      expect((await request(payload)).statusCode).toBe(400);
    for (const origin of [null, 'http://panels.example']) {
      config.trusted_ingress_canonical_origin = origin;
      expect((await request()).statusCode).toBe(400);
    }
    config.trusted_ingress_canonical_origin = home;
    for (const sha of [null, 'short']) {
      config.app_sha = sha;
      expect((await request()).statusCode).toBe(400);
    }
    expect(count()).toBe(before);
    expect(ensure).not.toHaveBeenCalled();
    config.app_sha = revision;
    const response = await request();
    expect(response.statusCode).toBe(200);
    const result = response.json();
    expect(Object.keys(result).sort()).toEqual(['command', 'credential', 'home_url', 'machine_id']);
    expect(result.home_url).toBe(home);
    expect(result.credential).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(result.command).toContain(`--machine-id ${result.machine_id}`);
    expect(result.command).toContain(`--revision ${revision}`);
    expect(result.command).toContain(`--home ${home}`);
    expect(result.command).not.toContain(result.credential);
    expect(ensure).toHaveBeenCalledExactlyOnceWith(result.machine_id);
    expect(count()).toBe(before + 1);
  } finally {
    await app.close();
  }
});

// The switch hinges on this: machine code on the server's machine takes the home identity.
test('the home identity gets a credential that the server accepts, and a new one replaces it', async () => {
  const database = fixture();
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(database),
  });
  const app = Fastify();
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  const ensure = vi.fn(async () => {});
  app.decorate('executor_registry', { ensure } as unknown as VpsExecutorRegistry);
  await app.register(routes, {
    database,
    config,
    clock: buildClock(config),
    identity: () => ({ principal: OWNER, conversationId: null }),
  });
  const issue = async () => {
    const reply = await app.inject({
      method: 'POST',
      url: `/machines/${HOME_MACHINE_ID}/credential`,
    });
    expect(reply.statusCode).toBe(200);
    return reply.json() as { machine_id: string; credential: string };
  };
  const authenticate = (credential: string) =>
    database.write((tx) => authenticate_executor(tx, HOME_MACHINE_ID, credential, null));
  try {
    const first = await issue();
    expect(first.machine_id).toBe(HOME_MACHINE_ID);
    expect(ensure).toHaveBeenCalledWith(HOME_MACHINE_ID);
    expect(authenticate(first.credential)).toBe(HOME_MACHINE_ID);
    const second = await issue();
    expect(authenticate(second.credential)).toBe(HOME_MACHINE_ID);
    expect(() => authenticate(first.credential)).toThrow(
      'Executor credential is required or invalid',
    );
  } finally {
    await app.close();
  }
});
