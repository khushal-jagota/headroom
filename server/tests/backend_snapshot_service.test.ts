/** Snapshot service ordering does not need a provider process or an HTTP host. */
import { expect, test, vi } from 'vitest';
import { writeFileSync, rmSync } from 'node:fs';
import { BackendSnapshotService } from '../conversation/snapshot/service.ts';
import type {
  BackendSnapshot,
  BackendProbeEnvironment,
  SnapshotProbeOptions,
} from '../conversation/snapshot/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { BackendStateStore } from '../conversation/backend_state.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { BackendUsageService } from '../conversation/backend_usage/service.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { createVoiceRoutes } from '../conversation/voice_transcription/routes.ts';
import { production_backend_providers } from '../conversation/snapshot/production.ts';
import { SubprocessBackendProbeEnvironment } from '../conversation/snapshot/environment.ts';
import { http_get_usage } from '../conversation/backend_usage/http.ts';

// Gap: successful HTTP comparisons cannot prove the shared shutdown seal retains
// admitted catalog/voice work and prevents either route from starting new I/O.
test('backend and voice HTTP work share admission and remain owned until both settle', async () => {
  const database = fixture();
  const writes = new ConversationHttpWriteScope();
  const catalog_entered = new Deferred<void>();
  const voice_entered = new Deferred<void>();
  const release = new Deferred<void>();
  let probes = 0;
  let trims = 0;
  let posts = 0;
  const snapshots = new BackendSnapshotService({
    environment: machine(),
    read_codex_login_standing: login,
    probe: async (key) => {
      probes++;
      catalog_entered.resolve();
      await release.promise;
      return { ...card(), backend_key: key };
    },
  });
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(database),
  });
  config.voice_transcription_api_key = 'synthetic-key';
  const app = await createApp(config, {
    database,
    areas: [
      {
        prefix: '/api/conversation',
        routes: createBackendRoutes(
          {
            settled: () => writes.drain(),
            database,
            backend_snapshots: snapshots,
            backend_usage: new BackendUsageService({}),
            backend_state: null,
            codex_sign_in: null,
            machine_catalog: () => null,
          },
          { available: true, writes },
        ),
      },
      {
        prefix: '/api/conversation',
        routes: createVoiceRoutes({
          writes,
          adapters: {
            async trim_surrounding_silence(audio) {
              trims++;
              voice_entered.resolve();
              await release.promise;
              return [audio, false];
            },
            async http_post() {
              posts++;
              return { status_code: 200, json: () => ({ text: 'Recorded words.' }) };
            },
          },
        }),
      },
    ],
  });
  const backend_request = app
    .inject({ method: 'GET', url: '/api/conversation/backends' })
    .then((value) => value);
  const voice_request = app
    .inject({
      method: 'POST',
      url: '/api/conversation/voice-transcriptions',
      payload: { audio: 'aGVsbG8=', media_type: 'audio/webm' },
    })
    .then((value) => value);
  try {
    await Promise.all([catalog_entered.promise, voice_entered.promise]);
    writes.seal();
    let drained = false;
    const drain = writes.drain().then(() => {
      drained = true;
    });
    await Promise.resolve();
    expect(drained).toBe(false);
    const already_started = probes;
    expect(
      (await app.inject({ method: 'GET', url: '/api/conversation/backends' })).statusCode,
    ).toBe(503);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/conversation/voice-transcriptions',
          payload: { audio: 'aGVsbG8=' },
        })
      ).statusCode,
    ).toBe(503);
    expect(probes).toBe(already_started);
    expect(trims).toBe(1);
    expect(posts).toBe(0);
    release.resolve();
    expect((await backend_request).statusCode).toBe(200);
    expect((await voice_request).statusCode).toBe(200);
    await drain;
    expect(drained).toBe(true);
    expect(posts).toBe(1);
  } finally {
    release.resolve();
    writes.seal();
    await writes.drain();
    await Promise.allSettled([backend_request, voice_request]);
    await app.close();
  }
});

// Gap: injected services can answer without a core unless every backend route
// preserves Python's shared Runtime dependency before route-level validation.
test('all eight backend routes require the conversation runtime before validated work', async () => {
  const database = fixture();
  const writes = new ConversationHttpWriteScope();
  const probe = vi.fn(async () => card());
  const snapshots = new BackendSnapshotService({
    environment: machine(),
    read_codex_login_standing: login,
    probe,
  });
  const app = await createApp(
    loadConfig(undefined, {
      PLAN_TEST_MODE: '1',
      PLAN_DB_PATH: fixture_path(database),
    }),
    {
      database,
      areas: [
        {
          prefix: '/api/conversation',
          routes: createBackendRoutes(
            {
              settled: () => writes.drain(),
              database,
              backend_snapshots: snapshots,
              backend_usage: new BackendUsageService({}),
              backend_state: null,
              codex_sign_in: null,
              machine_catalog: () => null,
            },
            { available: false, writes },
          ),
        },
      ],
    },
  );
  try {
    const requests = [
      { method: 'GET', url: '/api/conversation/backends?refresh=bad' },
      { method: 'POST', url: '/api/conversation/backends', payload: {} },
      { method: 'POST', url: '/api/conversation/backends/refresh' },
      {
        method: 'PUT',
        url: '/api/conversation/backends/unknown/models/model/enablement',
        payload: {},
      },
      { method: 'POST', url: '/api/conversation/backends/unknown/update' },
      { method: 'POST', url: '/api/conversation/backends/unknown/sign-in' },
      { method: 'GET', url: '/api/conversation/backends/unknown/sign-in' },
      { method: 'DELETE', url: '/api/conversation/backends/unknown/sign-in/session' },
    ] as const;
    for (const request of requests) {
      const response = await app.inject(request);
      expect(response.statusCode).toBe(503);
      expect(response.json()).toEqual({ detail: 'the conversation system is unavailable' });
    }
    expect(probe).not.toHaveBeenCalled();
  } finally {
    await app.close();
  }
});
import { ClaudeUsageAdapter } from '../conversation/backend_usage/claude.ts';
const login = () => ({
  stored: {
    present: true,
    readable: true,
    account_label: 'Synthetic',
    access_token_expires_at: null,
    generation: null,
    access_token_has_expired: () => false,
  },
  rejected: false,
  generation: null,
  everything_a_card_reads: () => ['generation'],
});
const card = (version = '1.0.0'): BackendSnapshot => ({
  backend_key: 'codex',
  installed: true,
  executable_path: '/synthetic/codex',
  version,
  identity: {
    status: 'authenticated',
    account_label: 'Synthetic',
    detail: null,
    login_command: 'codex login',
  },
  available_models: [
    {
      model_id: 'gpt-6.1-sol',
      display_name: null,
      detail: null,
      reasoning_effort_options: ['high'],
      selected_variant: 'sol',
      version: [6, 1],
      resolved_model_id: 'gpt-6.1-sol',
    },
  ],
  reasoning_effort_options: ['high'],
  default_model_id: 'gpt-6.1-sol',
  default_reasoning_effort: 'high',
  update_advisory: null,
  diagnoses: [],
  models_complete: true,
});
function machine(): BackendProbeEnvironment & {
  calls: string[][];
  result: { exit_code: number; standard_output: string; standard_error: string };
} {
  const calls: string[][] = [];
  const environment = {
    calls,
    result: { exit_code: 0, standard_output: 'Recorded update', standard_error: '' },
    executable_path: () => '/synthetic/codex',
    configured_executable_path: () => null,
    real_path: (path: string) => path,
    user_local_npm_prefix: () => '/synthetic',
    prefix_is_owned_and_writable: () => true,
    async run(argv: readonly string[]) {
      calls.push([...argv]);
      return environment.result;
    },
    async latest_released_version() {
      return null;
    },
  };
  return environment;
}
test('ordinary readers keep the complete card while one forced refresh serves concurrent refreshes', async () => {
  let count = 0;
  let gate: Deferred<void> | null = null;
  const entered = new Deferred<void>();
  const service = new BackendSnapshotService({
    environment: machine(),
    read_codex_login_standing: login,
    probe: async (_backend, _environment, options) => {
      count++;
      if (gate !== null) {
        entered.resolve();
        await gate.promise;
      }
      return count === 1
        ? card()
        : {
            ...card(),
            available_models: [],
            models_complete: false,
            update_advisory: {
              install_method: 'native',
              update_command: ['recorded-update'],
              latest_version: '2.0.0',
              update_available: true,
              detail: 'Recorded',
            },
          };
    },
  });
  const initial = await service.snapshot('codex');
  gate = new Deferred<void>();
  const refresh = service.snapshot('codex', { refresh: true });
  await entered.promise;
  const shared = service.snapshot('codex', { refresh: true });
  expect(await service.snapshot('codex')).toBe(initial);
  gate.resolve();
  const updated = await refresh;
  expect(await shared).toBe(updated);
  expect(count).toBe(2);
  expect(updated.available_models).toEqual(initial.available_models);
  expect(updated.update_advisory?.update_available).toBe(true);
});
test('an update uses only its recording environment and distinguishes changed, unchanged, and failed commands', async () => {
  const environment = machine();
  let version = '1.0.0';
  const options: SnapshotProbeOptions[] = [];
  const service = new BackendSnapshotService({
    environment,
    read_codex_login_standing: login,
    probe: async (_backend, _environment, option) => {
      options.push(option);
      return {
        ...card(version),
        update_advisory: {
          install_method: 'native',
          update_command: ['synthetic-cli', 'update'],
          latest_version: null,
          update_available: false,
          detail: 'Recorded',
        },
      };
    },
  });
  expect((await service.update_backend('codex')).outcome).toBe('unchanged');
  environment.run = async (argv) => {
    environment.calls.push([...argv]);
    version = '2.0.0';
    return environment.result;
  };
  expect((await service.update_backend('codex')).outcome).toBe('succeeded');
  environment.result = { exit_code: 3, standard_output: '', standard_error: 'Recorded failure' };
  expect(await service.update_backend('codex')).toEqual({
    outcome: 'failed',
    detail: 'The update command exited with code 3.',
    output_tail: 'Recorded failure',
  });
  expect(environment.calls).toEqual(Array.from({ length: 3 }, () => ['synthetic-cli', 'update']));
  expect(options.every((option) => option.refresh && option.discover_update_advisory)).toBe(true);
});
test('a failed replacement of allowance windows preserves the previous successful observation', () => {
  const database = fixture();
  const store = new BackendStateStore(database);
  const window = {
    kind: 'five_hour' as const,
    model_scope: null,
    used_percent: 30,
    resets_at: new Date(200_000),
  };
  const result = {
    backend_key: 'codex' as const,
    outcome: 'succeeded' as const,
    detail: null,
    observed_at: new Date(100_000),
    windows: [window],
  };
  store.keep_successful_usage(result);
  expect(() =>
    store.keep_successful_usage({
      ...result,
      observed_at: new Date(150_000),
      windows: [window, window],
    }),
  ).toThrow();
  expect(store.read_usage('codex')).toEqual({ observed_at: result.observed_at, windows: [window] });
});
// Distinct gap: successful route comparisons cannot prove cleanup after catalog failure.
test('catalog failure cancels and joins the outstanding allowance read', async () => {
  const database = fixture();
  const writes = new ConversationHttpWriteScope();
  const entered = new Deferred<void>();
  let cancelled = false;
  const usage = new BackendUsageService({
    codex: {
      async refresh(options = {}) {
        entered.resolve();
        await new Promise<void>((_resolve, reject) => {
          options.signal!.addEventListener(
            'abort',
            () => {
              cancelled = true;
              reject(options.signal!.reason);
            },
            { once: true },
          );
        });
        throw new Error('unreachable');
      },
    },
  });
  const snapshots = new BackendSnapshotService({
    environment: machine(),
    read_codex_login_standing: login,
    probe: async () => {
      await entered.promise;
      throw new Error('Synthetic catalog failure');
    },
  });
  const app = await createApp(
    loadConfig(undefined, {
      PLAN_TEST_MODE: '1',
      PLAN_DB_PATH: fixture_path(database),
    }),
    {
      database,
      areas: [
        {
          prefix: '/api/conversation',
          routes: createBackendRoutes(
            {
              settled: () => writes.drain(),
              database,
              backend_snapshots: snapshots,
              backend_usage: usage,
              backend_state: null,
              codex_sign_in: null,
              machine_catalog: () => null,
            },
            { available: true, writes },
          ),
        },
      ],
    },
  );
  try {
    expect(
      (await app.inject({ method: 'POST', url: '/api/conversation/backends/refresh' })).statusCode,
    ).toBe(500);
    expect(cancelled).toBe(true);
  } finally {
    await app.close();
  }
});
// Distinct gap: a mistaken production composition must fail before process or network I/O.
test('test compositions cannot acquire production catalogs, login, updates, or HTTP usage', async () => {
  const providers = production_backend_providers(true);
  expect([providers.codex, providers.claude, providers.hermes, providers.codex_sign_in]).toEqual([
    null,
    null,
    null,
    null,
  ]);
  expect(providers.usage).toEqual({});
  expect(() => new SubprocessBackendProbeEnvironment()).toThrow('unavailable in tests');
  await expect(http_get_usage('https://invalid.example', {}, 1)).rejects.toThrow(
    'unavailable in tests',
  );
});
// Distinct gap: recorded response injection alone cannot prove the production redirect policy.
test('production usage and registry transports refuse redirects without another external request', async () => {
  const credential_path = fixture_path(fixture()) + '.credentials.json';
  writeFileSync(
    credential_path,
    JSON.stringify({ claudeAiOauth: { accessToken: 'synthetic-redirect-token' } }),
  );
  const fetch_recording = vi.fn<typeof fetch>().mockImplementation(
    async () =>
      new Response(null, {
        status: 302,
        headers: { location: 'https://redirect.invalid/never-requested' },
      }),
  );
  vi.stubGlobal('fetch', fetch_recording);
  let environment: SubprocessBackendProbeEnvironment | undefined;
  try {
    vi.stubEnv('VITEST', undefined);
    vi.stubEnv('PLAN_TEST_MODE', undefined);
    vi.stubEnv('PYTEST_CURRENT_TEST', undefined);
    environment = new SubprocessBackendProbeEnvironment();
    const adapter = new ClaudeUsageAdapter({
      credential_path,
      http_get: http_get_usage,
      now: () => new Date('2026-11-12T12:00:00Z'),
    });
    expect((await adapter.refresh()).outcome).toBe('failed');
    expect(
      await environment.latest_released_version('@anthropic-ai/claude-code', {
        timeout_seconds: 1,
      }),
    ).toBeNull();
    expect(fetch_recording).toHaveBeenCalledTimes(2);
    expect(fetch_recording).toHaveBeenNthCalledWith(
      1,
      'https://api.anthropic.com/api/oauth/usage',
      expect.objectContaining({
        redirect: 'manual',
        headers: expect.objectContaining({ authorization: 'Bearer synthetic-redirect-token' }),
      }),
    );
    expect(fetch_recording).toHaveBeenNthCalledWith(
      2,
      'https://registry.npmjs.org/@anthropic-ai/claude-code/latest',
      expect.objectContaining({ redirect: 'manual' }),
    );
  } finally {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    rmSync(credential_path, { force: true });
    await environment?.shutdown();
  }
});
