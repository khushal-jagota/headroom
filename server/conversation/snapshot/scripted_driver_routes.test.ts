/** A driver outside the built-in list reaches the backend routes through the registry alone. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';
import { createApp } from '../../app.ts';
import { loadConfig } from '../../core/config.ts';
import { fixture, fixture_path } from '../../tests/ticket_fixture.ts';
import { ConversationHttpWriteScope } from '../http/writes.ts';
import { createBackendRoutes } from './routes.ts';
import { createDriverRoutes } from '../../drivers/http/routes.ts';
import { create_backend_runtime } from './composition.ts';
import type { AnyAgentDriver, DriverInstance } from '../../drivers/core/driver.ts';
import { NO_DRIVER_CONFIG } from '../../drivers/core/driver.ts';
import { parse_backend_instance_id, parse_driver_kind } from '../../drivers/core/identity.ts';
import { driver_registry_from_entries } from '../../drivers/core/registry.ts';
import type { RegistryEntry } from '../../drivers/core/registry.ts';
import type { DriverSignInController, SignInSession } from '../../drivers/core/sign_in.ts';
import {
  SignInReturnUrlRefused,
  SignInUnavailable,
  UnknownSignInSession,
} from '../../drivers/core/sign_in.ts';
import { scripted_manifest, scripted_status } from '../../drivers/testing/registry.ts';
import type { BackendUsageAdapter } from '../backend_usage/contracts.ts';
import { turn_scoped_selection_transition } from '../../drivers/core/selection.ts';

const scripted_icon = new URL('../../drivers/testing/icon.svg', import.meta.url);

function scripted_entry(
  key: string,
  members: Partial<Pick<DriverInstance, 'sign_in' | 'allowance_usage'>>,
): RegistryEntry {
  const driver_kind = parse_driver_kind(key);
  const driver: AnyAgentDriver = {
    driver_kind,
    home_instructions_file: 'AGENTS.md',
    client: {
      label: `Scripted ${key}`,
      icon: { file: scripted_icon, monochrome: true },
      accent_color: '#000000',
    },
    manifest: scripted_manifest(key),
    config: NO_DRIVER_CONFIG,
    create: async () => {
      throw new Error('Created by the test');
    },
    executor: async () => {
      throw new Error('Executed by the test');
    },
  };
  return {
    kind: 'live',
    driver,
    instance: {
      instance_id: parse_backend_instance_id(key),
      driver_kind,
      capabilities: {
        sessions: { supportsModelSwitchInSession: true },
        turns: { supportsActiveSteering: true },
      },
      plan_selection_transition: turn_scoped_selection_transition,
      child_factory: () => {
        throw new Error('No conversation starts in this proof');
      },
      status: scripted_status(key),
      dispose: async () => {},
      ...members,
    },
  };
}

// Gap: every other backend route test reaches only the built-in ids and the Codex sign-in, so a
// route or card list that still names drivers would pass them. This drives a scripted kind,
// through the cards, the sign-in routes and the driver list the apps draw marks and labels from.
// It also proves the sign-in error statuses: a refused return address is 422, and a sign-out
// that cannot reach the account is 503.
test('a scripted driver gets its card, sign-in routes, driver list entry, icon and allowance window', async () => {
  const calls: string[] = [];
  let session: SignInSession | null = null;
  let sign_out_reachable = false;
  const sign_in: DriverSignInController = {
    read: () => session,
    async start() {
      calls.push('start');
      session = {
        session_id: 'scripted-session',
        state: 'awaiting_return_url',
        verification_url: 'https://sign-in.invalid/start',
        user_code: null,
        started_at: new Date('2026-11-17T10:00:00Z'),
        deadline_at: new Date('2026-11-17T10:10:00Z'),
        detail: null,
        account_label: null,
      };
      return session;
    },
    async cancel(session_id) {
      calls.push(`cancel:${session_id}`);
      throw new UnknownSignInSession(session_id);
    },
    async submit_return_url(session_id, return_url) {
      calls.push(`return:${session_id}:${return_url}`);
      if (session?.session_id !== session_id) throw new UnknownSignInSession(session_id);
      if (return_url.includes('refused'))
        throw new SignInReturnUrlRefused('That is not the address the sign-in returns to.');
      session = { ...session, state: 'confirmed', account_label: 'scripted@example.invalid' };
      return session;
    },
    async sign_out() {
      calls.push('sign_out');
      if (!sign_out_reachable) throw new SignInUnavailable('The scripted account is unreachable.');
      session = null;
    },
  };
  // Stored usage rows need a built-in id until the Database stage, so the window is the
  // built-in id's; the scripted kind is checked through its card and sign-in routes.
  const allowance_usage: BackendUsageAdapter = {
    async refresh() {
      return {
        backend_key: parse_backend_instance_id('claude'),
        outcome: 'succeeded',
        detail: null,
        observed_at: new Date('2026-11-17T10:00:00Z'),
        windows: [
          {
            kind: 'seven_day',
            used_percent: 40,
            resets_at: new Date('2026-11-20T00:00:00Z'),
            model_scope: null,
            scope_label: 'Frontier models',
          },
        ],
      };
    },
  };
  const entries = [
    scripted_entry('claude', { allowance_usage }),
    scripted_entry('scripted', { sign_in }),
  ];
  const registry = driver_registry_from_entries(
    entries.map((entry) => entry.driver!),
    entries,
  );
  const database = fixture();
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(database),
  });
  const writes = new ConversationHttpWriteScope();
  const runtime = create_backend_runtime({ database, config, now: () => new Date(), registry });
  const app = await createApp(config, {
    database,
    areas: [
      {
        prefix: '/api/conversation',
        routes: createBackendRoutes(runtime, { available: true, writes }),
      },
      { prefix: '/api', routes: createDriverRoutes(runtime) },
    ],
  });
  const call = async (
    method: 'GET' | 'POST' | 'DELETE' | 'PUT',
    path: string,
    payload?: object,
  ) => {
    const response = await app.inject({
      method,
      url: `/api/conversation/backends${path}`,
      payload,
    });
    return { status: response.statusCode, body: response.json() as Record<string, unknown> };
  };
  try {
    const cards = await call('GET', '');
    expect(
      (cards.body.backends as Record<string, unknown>[]).map((card) => ({
        backend_key: card.backend_key,
        can_sign_in: card.can_sign_in,
        reports_allowance_usage: card.reports_allowance_usage,
      })),
    ).toEqual([
      { backend_key: 'claude', can_sign_in: false, reports_allowance_usage: true },
      { backend_key: 'scripted', can_sign_in: true, reports_allowance_usage: false },
    ]);
    expect((cards.body.backends as { diagnoses: string[] }[])[1].diagnoses).toEqual([
      '`scripted` is not installed.',
    ]);

    const icon_bytes = readFileSync(scripted_icon);
    const version = createHash('sha256').update(icon_bytes).digest('hex').slice(0, 12);
    const drivers = await app.inject({ method: 'GET', url: '/api/drivers' });
    expect(drivers.statusCode).toBe(200);
    expect(drivers.json()).toEqual({
      drivers: [
        expect.objectContaining({ driver_kind: 'claude' }),
        {
          driver_kind: 'scripted',
          label: 'Scripted scripted',
          icon: {
            url: `/api/drivers/scripted/icon?v=${version}`,
            media_type: 'image/svg+xml',
            monochrome: true,
          },
          accent_color: '#000000',
          checks_for_updates: false,
        },
      ],
      default_backend_instance_id: 'codex',
    });
    const icon = await app.inject({
      method: 'GET',
      url: `/api/drivers/scripted/icon?v=${version}`,
    });
    expect(icon.statusCode).toBe(200);
    expect(icon.rawPayload).toEqual(icon_bytes);
    expect(icon.headers).toMatchObject({
      'content-type': 'image/svg+xml',
      etag: `"${version}"`,
      'cache-control': 'private, max-age=31536000, immutable',
      'x-content-type-options': 'nosniff',
      'content-security-policy': 'sandbox',
    });
    expect((await app.inject({ method: 'GET', url: '/api/drivers/nope/icon' })).statusCode).toBe(
      404,
    );

    expect(await call('GET', '/scripted/sign-in')).toEqual({
      status: 200,
      body: { sign_in: null },
    });
    const started = await call('POST', '/scripted/sign-in');
    expect(started.body).toMatchObject({
      sign_in: { session_id: 'scripted-session', state: 'awaiting_return_url' },
    });
    expect(await call('DELETE', '/scripted/sign-in/other-session')).toEqual({
      status: 409,
      body: { detail: 'that sign-in is not the one in force any more' },
    });
    expect(
      (await call('POST', '/scripted/sign-in/other-session/return-url', { return_url: 'x' }))
        .status,
    ).toBe(409);
    expect(
      await call('POST', '/scripted/sign-in/scripted-session/return-url', {
        return_url: 'https://return.invalid/refused',
      }),
    ).toEqual({
      status: 422,
      body: { detail: 'That is not the address the sign-in returns to.' },
    });
    const returned = await call('POST', '/scripted/sign-in/scripted-session/return-url', {
      return_url: 'https://return.invalid/?code=1',
    });
    expect(returned).toMatchObject({
      status: 200,
      body: { sign_in: { state: 'confirmed', account_label: 'scripted@example.invalid' } },
    });
    expect(await call('DELETE', '/scripted/sign-in')).toEqual({
      status: 503,
      body: { detail: 'The scripted account is unreachable.' },
    });
    sign_out_reachable = true;
    expect(await call('DELETE', '/scripted/sign-in')).toEqual({
      status: 200,
      body: { sign_in: null },
    });
    expect(calls).toEqual([
      'start',
      'cancel:other-session',
      'return:other-session:x',
      'return:scripted-session:https://return.invalid/refused',
      'return:scripted-session:https://return.invalid/?code=1',
      'sign_out',
      'sign_out',
    ]);
    const refused = { status: 404, body: { detail: 'claude cannot be signed in from Panels' } };
    expect(await call('GET', '/claude/sign-in')).toEqual(refused);
    expect(await call('DELETE', '/claude/sign-in')).toEqual(refused);
    expect(await call('POST', '/claude/sign-in/s/return-url', { return_url: 'x' })).toEqual(
      refused,
    );

    const refreshed = await call('POST', '/refresh');
    expect(refreshed.body.usage_outcomes).toEqual([
      { backend_key: 'claude', outcome: 'succeeded', detail: null },
      {
        backend_key: 'scripted',
        outcome: 'unavailable',
        detail: 'Scripted scripted does not expose usage to Panels.',
      },
    ]);
    expect((refreshed.body.backends as { cached_usage: unknown }[])[0].cached_usage).toEqual({
      observed_at: '2026-11-17T10:00:00Z',
      windows: [
        {
          kind: 'seven_day',
          used_percent: 40,
          resets_at: '2026-11-20T00:00:00Z',
          model_id: 'Frontier models',
        },
      ],
    });

    const unknown = await call('PUT', '/nope/models/m/enablement', { enabled: true });
    expect(unknown).toEqual({
      status: 422,
      body: {
        detail: [
          {
            type: 'enum',
            loc: ['path', 'backend_key'],
            msg: "Input should be 'claude' or 'scripted'",
            input: 'nope',
            ctx: { expected: "'claude' or 'scripted'" },
          },
        ],
      },
    });
  } finally {
    writes.seal();
    await writes.drain();
    await app.close();
  }
});
