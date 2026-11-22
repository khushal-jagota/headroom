/** Snapshot service ordering does not need a provider process or an HTTP host. */
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { expect, test, vi } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BackendSnapshotService } from '../conversation/snapshot/service.ts';
import type {
  BackendCardSource,
  BackendSnapshot,
  BackendProbeEnvironment,
} from '../conversation/snapshot/contracts.ts';
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import type { StatusProbeContext } from '../drivers/core/status.ts';
import {
  BUILT_IN_MANIFESTS,
  KNOWN_BACKEND_INSTANCE_IDS,
  built_in_driver_of,
} from '../drivers/built_in_manifests.ts';
import { Deferred } from '../core/deferred.ts';
import { BackendStateStore } from '../conversation/backend_state.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { BackendUsageService } from '../conversation/backend_usage/service.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { createVoiceRoutes } from '../conversation/voice_transcription/routes.ts';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';
import type { DriverHost } from '../drivers/core/host.ts';
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
    sources: sources_of(async (key) => {
      probes++;
      catalog_entered.resolve();
      await release.promise;
      return { ...card(), backend_key: key };
    }),
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
            backend_usage: new BackendUsageService(new Map()),
            backend_state: null,
            backend_ids: KNOWN_BACKEND_INSTANCE_IDS,
            sign_in: () => null,
            machine_catalog: () => null,
            drivers: BUILT_IN_MANIFESTS,
            driver_of: () => null,
            reports_allowance_usage: () => false,
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
    sources: sources_of(probe),
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
              backend_usage: new BackendUsageService(new Map()),
              backend_state: null,
              backend_ids: KNOWN_BACKEND_INSTANCE_IDS,
              sign_in: () => null,
              machine_catalog: () => null,
              drivers: BUILT_IN_MANIFESTS,
              driver_of: () => null,
              reports_allowance_usage: () => false,
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
import { ClaudeUsageAdapter } from '../drivers/claude/runtime/allowance_usage.ts';
/** One card source per built-in id, each answering with the scripted probe's card. */
function sources_of(
  probe: (key: BackendInstanceId, context: StatusProbeContext) => Promise<BackendSnapshot>,
): BackendCardSource[] {
  return KNOWN_BACKEND_INSTANCE_IDS.map((key) => ({
    instance_id: key,
    executable_name: key,
    status: {
      version_detects_upgrade: true,
      async probe(context) {
        const { backend_key: _key, ...draft } = await probe(key, context);
        return draft;
      },
    },
  }));
}
const card = (version = '1.0.0'): BackendSnapshot => ({
  backend_key: parse_backend_instance_id('codex'),
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
    sources: sources_of(async (_backend, options) => {
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
    }),
  });
  const initial = await service.snapshot(parse_backend_instance_id('codex'));
  gate = new Deferred<void>();
  const refresh = service.snapshot(parse_backend_instance_id('codex'), { refresh: true });
  await entered.promise;
  const shared = service.snapshot(parse_backend_instance_id('codex'), { refresh: true });
  expect(await service.snapshot(parse_backend_instance_id('codex'))).toBe(initial);
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
  const options: StatusProbeContext[] = [];
  const service = new BackendSnapshotService({
    environment,
    sources: sources_of(async (_backend, option) => {
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
    }),
  });
  expect((await service.update_backend(parse_backend_instance_id('codex'))).outcome).toBe(
    'unchanged',
  );
  environment.run = async (argv) => {
    environment.calls.push([...argv]);
    version = '2.0.0';
    return environment.result;
  };
  expect((await service.update_backend(parse_backend_instance_id('codex'))).outcome).toBe(
    'succeeded',
  );
  environment.result = { exit_code: 3, standard_output: '', standard_error: 'Recorded failure' };
  expect(await service.update_backend(parse_backend_instance_id('codex'))).toEqual({
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
    scope_label: null,
    used_percent: 30,
    resets_at: new Date(200_000),
  };
  const result = {
    backend_key: parse_backend_instance_id('codex'),
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
  expect(store.read_usage(parse_backend_instance_id('codex'))).toEqual({
    observed_at: result.observed_at,
    windows: [window],
  });
});
// Distinct gap: successful route comparisons cannot prove cleanup after catalog failure.
test('catalog failure cancels and joins the outstanding allowance read', async () => {
  const database = fixture();
  const writes = new ConversationHttpWriteScope();
  const entered = new Deferred<void>();
  let cancelled = false;
  const usage = new BackendUsageService(
    new Map([
      [
        parse_backend_instance_id('codex'),
        {
          label: 'Codex',
          adapter: {
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
        },
      ],
    ]),
  );
  const snapshots = new BackendSnapshotService({
    environment: machine(),
    sources: sources_of(async () => {
      await entered.promise;
      throw new Error('Synthetic catalog failure');
    }),
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
              backend_ids: KNOWN_BACKEND_INSTANCE_IDS,
              sign_in: () => null,
              machine_catalog: () => null,
              drivers: BUILT_IN_MANIFESTS,
              driver_of: () => null,
              reports_allowance_usage: () => false,
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
  const environment = new RecordingBackendProbeEnvironment();
  for (const { manifest } of BUILT_IN_DRIVERS)
    environment.executables.set(manifest.executable.name, `/synthetic/${manifest.executable.name}`);
  const host = {
    test_mode: true,
    environment: {},
    database: {},
    child_processes: {},
    one_shot_children: {},
    worker_supervision: {},
    panels_server_url: 'http://127.0.0.1:1',
    worker_process_root: '/synthetic',
    now: () => new Date(0),
    executable_on_path: (name: string) => name,
  } as unknown as DriverHost;
  for (const driver of BUILT_IN_DRIVERS) {
    const instance = await driver.create(
      { instance_id: parse_backend_instance_id(driver.driver_kind), config: null },
      host,
      new AbortController().signal,
    );
    expect([instance.sign_in, instance.allowance_usage]).toEqual([undefined, undefined]);
    const card = await instance.status.probe({
      environment,
      refresh: true,
      discover_update_advisory: false,
      now: new Date(0),
      model_version: () => [],
    });
    expect([card.installed, card.available_models, card.models_complete]).toEqual([
      true,
      [],
      false,
    ]);
  }
  expect(() => new SubprocessBackendProbeEnvironment(new OneShotChildren())).toThrow(
    'unavailable in tests',
  );
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
    environment = new SubprocessBackendProbeEnvironment(new OneShotChildren());
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
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';

import { readFileSync } from 'node:fs';
import { run as run_recorded_probes } from '../conversation/snapshot/testing/probe_comparison.ts';
import { snapshot_json } from '../conversation/snapshot/presentation.ts';
/** A home card with its built-in driver's rules and no sign-in or allowance usage. */
const card_json = (card: BackendSnapshot) =>
  snapshot_json(card, null, {
    driver: built_in_driver_of(card.backend_key) ?? null,
    can_sign_in: false,
    reports_allowance_usage: false,
  });
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import type { CommandOutcome } from '../conversation/snapshot/contracts.ts';
import {
  claude_auth_paths,
  create_claude_status,
  read_claude_auth_freshness,
} from '../drivers/claude/runtime/status.ts';
import type { CodexLoginStanding, CodexModelCatalog } from '../drivers/codex/runtime/contracts.ts';
import { create_codex_status } from '../drivers/codex/runtime/status.ts';

const recorded = (standard_output: string, exit_code = 0): CommandOutcome => ({
  exit_code,
  standard_output,
  standard_error: '',
});
const recorded_codex_catalog = (complete: boolean): CodexModelCatalog => ({
  models: [
    {
      model_id: 'gpt-6.1-sol',
      display_name: 'Sol',
      reasoning_effort_options: ['medium', 'high'],
      default_reasoning_effort: 'medium',
      is_default: true,
    },
    ...(complete
      ? [
          {
            model_id: 'gpt-6-luna',
            display_name: 'Luna',
            reasoning_effort_options: ['medium'],
            default_reasoning_effort: 'medium',
            is_default: false,
          },
        ]
      : []),
  ],
  reasoning_effort_options: ['medium', 'high'],
  default_model_id: 'gpt-6.1-sol',
  default_reasoning_effort: 'medium',
  complete,
});
/** The probe_comparison machines, as the Python comparison describes them. */
function recorded_probe_cases() {
  const codex = {
    backend: parse_backend_instance_id('codex'),
    executable: '/synthetic/bin/codex',
    real_path: '/synthetic/.codex/packages/standalone/codex',
    version: recorded('codex-cli 0.145.0'),
    identity: recorded('Logged in using ChatGPT\n'),
    writable: false,
    latest: null,
    login: {
      present: true,
      readable: true,
      account_label: 'synthetic@example.invalid',
      access_token_expires_at: '2026-11-13T12:00:00Z',
      generation: { account_id: 'synthetic', last_refresh: 'refresh-one' },
      rejected: false,
    },
  };
  const claude = {
    backend: parse_backend_instance_id('claude'),
    executable: '/synthetic/.local/bin/claude',
    real_path: '/synthetic/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude',
    version: recorded('claude 2.1.0'),
    identity: recorded(
      JSON.stringify({
        loggedIn: true,
        email: ' synthetic@example.invalid ',
        subscriptionType: ' Max ',
        authMethod: ' oauth ',
      }),
    ),
    writable: true,
    latest: '2.2.0',
    login: null,
  };
  return [
    { ...codex, name: 'codex-native-saved-login', codex_catalog: recorded_codex_catalog(true) },
    { ...codex, name: 'codex-rejected-unavailable', login: { ...codex.login, rejected: true } },
    {
      ...claude,
      name: 'claude-npm-aliases',
      claude_catalog: {
        models: [
          {
            model_id: 'opus',
            display_name: 'Opus 5 (1M)',
            resolved_model_id: 'claude-opus-5[1m]',
            reasoning_effort_options: ['high', 'max'],
          },
          {
            model_id: 'haiku',
            display_name: 'Haiku 4.5',
            resolved_model_id: 'claude-haiku-4-5-20251001',
            reasoning_effort_options: [],
          },
        ],
        reasoning_effort_options: [],
        default_model_id: 'claude-opus-5[1m]',
      },
    },
    {
      ...claude,
      name: 'claude-manual-unavailable',
      executable: '/opt/homebrew/bin/claude',
      real_path: '/opt/homebrew/Cellar/claude-code/2/bin/claude',
      writable: false,
      version: recorded('unrecognized version'),
      identity: recorded('invalid JSON'),
    },
  ];
}
function recorded_login(account_label: string, last_refresh: string): CodexLoginStanding {
  const generation = { account_id: 'synthetic', last_refresh };
  return {
    stored: {
      present: true,
      readable: true,
      account_label,
      access_token_expires_at: null,
      generation,
      access_token_has_expired: () => false,
    },
    generation,
    rejected: false,
    everything_a_card_reads: () => [generation, false, true, true, false],
  };
}
/** The one construction a pipeline change may rewrite: recorded machine, login and catalogs. */
function recorded_card_service(options: {
  readonly environment: RecordingBackendProbeEnvironment;
  readonly login: () => CodexLoginStanding | null;
  readonly codex_catalog: () => Promise<CodexModelCatalog>;
  readonly machine_catalog?: boolean;
}) {
  return new BackendSnapshotService({
    environment: options.environment,
    now: () => new Date('2026-11-12T12:00:00Z'),
    machine_catalog: options.machine_catalog,
    sources: [
      {
        instance_id: parse_backend_instance_id('codex'),
        executable_name: 'codex',
        status: create_codex_status({
          catalog: options.codex_catalog,
          read_login_standing: options.login,
        }),
      },
    ],
  });
}
function recorded_machine(): RecordingBackendProbeEnvironment {
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('codex', '/synthetic/bin/codex');
  environment.outcomes.set(
    JSON.stringify(['/synthetic/bin/codex', '--version']),
    recorded('codex-cli 0.150.0'),
  );
  environment.outcomes.set(
    JSON.stringify(['/synthetic/bin/codex', 'login', 'status']),
    recorded('Logged in using ChatGPT\n'),
  );
  return environment;
}
async function codex_login_sequence() {
  const environment = recorded_machine();
  const codex = parse_backend_instance_id('codex');
  let login: CodexLoginStanding | null = recorded_login('first@example.invalid', 'one');
  let complete = true;
  let during_catalog: (() => void) | null = null;
  const service = recorded_card_service({
    environment,
    login: () => login,
    codex_catalog: async () => {
      during_catalog?.();
      during_catalog = null;
      return recorded_codex_catalog(complete);
    },
  });
  const steps: unknown[] = [];
  const step = async (name: string, refresh = false) => {
    const card = await service.snapshot(codex, { refresh });
    steps.push({
      name,
      card: card_json(card),
      kept: card_json(service.retained(codex)!),
      commands: environment.commands.length,
    });
    return card;
  };
  const first = await step('first probe');
  expect(await step('unchanged login reuses the card')).toBe(first);
  login = recorded_login('second@example.invalid', 'two');
  complete = false;
  await step('changed login re-probes without the kept complete list');
  during_catalog = () => {
    login = recorded_login('third@example.invalid', 'three');
  };
  await step('login moved during the probe blanks that card', true);
  await step('the next read re-probes the moved login');
  login = null;
  await step('signed out re-probes');
  await step('signed out stays stale');
  return steps;
}
async function codex_machine_sequence() {
  const environment = recorded_machine();
  const service = recorded_card_service({
    environment,
    login: () => null,
    codex_catalog: async () => recorded_codex_catalog(true),
    machine_catalog: true,
  });
  const steps: unknown[] = [];
  for (const name of ['first read', 'kept read']) {
    const cards = [card_json(await service.snapshot(parse_backend_instance_id('codex')))];
    steps.push({ name, cards, commands: environment.commands.map((argv) => [...argv]) });
  }
  return steps;
}
// Gap: no test records the exact card JSON, so a rebuilt card pipeline could change a card,
// the Codex login staleness rules or the machine catalog reads without any test failing.
test('backend card JSON stays the same for every recorded machine and login sequence', async () => {
  const probes = await run_recorded_probes(recorded_probe_cases());
  const actual = JSON.parse(
    JSON.stringify({
      probes: probes.cards.map((value) => ({
        name: value.name,
        card: card_json(value.card),
        commands: value.commands,
        catalog_calls: value.catalog_calls,
        registry_lookups: value.registry_lookups,
      })),
      codex_login: await codex_login_sequence(),
      codex_machine: await codex_machine_sequence(),
    }),
  );
  expect(actual).toEqual(
    JSON.parse(readFileSync(new URL('./backend_card_json.json', import.meta.url), 'utf8')),
  );
});

// Distinct gap: existing refresh route tests only check 503 unavailability and failure cleanup,
// not that POST /api/conversation/backends/refresh passes refresh: true to update version and identity.
test('POST /api/conversation/backends/refresh re-probes version and identity without freshness_key movement', async () => {
  const database = fixture();
  const writes = new ConversationHttpWriteScope();
  let version = '1.5.10';
  let account_label = 'first@example.invalid';
  let probe_calls = 0;
  const snapshots = new BackendSnapshotService({
    environment: machine(),
    sources: sources_of(async (key) => {
      probe_calls++;
      return {
        ...card(version),
        backend_key: key,
        identity: {
          status: 'authenticated',
          account_label,
          detail: null,
          login_command: null,
        },
      };
    }),
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
              backend_usage: new BackendUsageService(new Map()),
              backend_state: null,
              backend_ids: KNOWN_BACKEND_INSTANCE_IDS,
              sign_in: () => null,
              machine_catalog: () => null,
              drivers: BUILT_IN_MANIFESTS,
              driver_of: () => null,
              reports_allowance_usage: () => false,
            },
            { available: true, writes },
          ),
        },
      ],
    },
  );
  try {
    const initial = await app.inject({ method: 'GET', url: '/api/conversation/backends' });
    expect(initial.statusCode).toBe(200);
    expect(initial.json().backends[0]).toMatchObject({
      version: '1.5.10',
      identity: { account_label: 'first@example.invalid' },
    });
    const probes_before_refresh = probe_calls;
    version = '1.5.11';
    account_label = 'second@example.invalid';
    const refreshed = await app.inject({
      method: 'POST',
      url: '/api/conversation/backends/refresh',
    });
    expect(refreshed.statusCode).toBe(200);
    expect(refreshed.json().backends[0]).toMatchObject({
      version: '1.5.11',
      identity: { account_label: 'second@example.invalid' },
    });
    expect(probe_calls).toBeGreaterThan(probes_before_refresh);
    const after = await app.inject({ method: 'GET', url: '/api/conversation/backends' });
    expect(after.statusCode).toBe(200);
    expect(after.json().backends[0]).toMatchObject({
      version: '1.5.11',
      identity: { account_label: 'second@example.invalid' },
    });
  } finally {
    await app.close();
  }
});

// Distinct gap: Codex freshness tests do not exercise Claude's non-secret metadata key across
// VPS (.credentials.json mtime/size + ~/.claude.json) and Mac (Keychain-backed ~/.claude.json only).
test('Claude freshness_key invalidates on credentials metadata and oauthAccount without reading secrets', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-claude-freshness-'));
  try {
    const claude = parse_backend_instance_id('claude');
    const account_home = join(root, 'home');
    const claude_home = join(account_home, '.claude');
    mkdirSync(claude_home, { recursive: true });

    const configured = claude_auth_paths(
      join(root, 'isolated'),
      { CLAUDE_CONFIG_DIR: join(root, 'isolated'), HOME: account_home },
      account_home,
    );
    expect(configured.claude_json_path).toBe(join(root, 'isolated', '.claude.json'));

    const vps_paths = claude_auth_paths(claude_home, { HOME: account_home }, account_home);
    expect(vps_paths.claude_json_path).toBe(join(account_home, '.claude.json'));
    const vps_env = new RecordingBackendProbeEnvironment();
    vps_env.executables.set('claude', '/synthetic/claude');
    vps_env.outcomes.set(JSON.stringify(['/synthetic/claude', '--version']), recorded('2.1.259'));
    let vps_identity = recorded(JSON.stringify({ loggedIn: false }));
    let vps_probes = 0;
    const vps_service = new BackendSnapshotService({
      environment: vps_env,
      sources: [
        {
          instance_id: claude,
          executable_name: 'claude',
          status: {
            ...create_claude_status({
              catalog: async () => ({
                models: [],
                reasoning_effort_options: ['high'],
                default_model_id: null,
              }),
              read_freshness: () => read_claude_auth_freshness(vps_paths),
            }),
            probe: async (context) => {
              vps_probes++;
              vps_env.outcomes.set(
                JSON.stringify(['/synthetic/claude', 'auth', 'status', '--json']),
                vps_identity,
              );
              return create_claude_status({
                catalog: async () => ({
                  models: [],
                  reasoning_effort_options: ['high'],
                  default_model_id: null,
                }),
                read_freshness: () => read_claude_auth_freshness(vps_paths),
              }).probe(context);
            },
          },
        },
      ],
    });

    expect((await vps_service.snapshot(claude)).identity?.status).toBe('unauthenticated');
    expect(vps_probes).toBe(1);
    await vps_service.snapshot(claude);
    expect(vps_probes).toBe(1);

    writeFileSync(
      vps_paths.credentials_path,
      JSON.stringify({ claudeAiOauth: { accessToken: 'secret-token-never-in-key' } }),
    );
    writeFileSync(
      vps_paths.claude_json_path,
      JSON.stringify({
        numStartups: 1,
        oauthAccount: {
          accountUuid: 'acct-1',
          emailAddress: 'first@example.invalid',
          organizationUuid: 'org-1',
          profileFetchedAt: '2026-11-19T10:00:00Z',
        },
      }),
    );
    expect(read_claude_auth_freshness(vps_paths)).not.toContain('secret-token-never-in-key');
    vps_identity = recorded(
      JSON.stringify({
        loggedIn: true,
        email: 'first@example.invalid',
        subscriptionType: 'max',
        authMethod: 'claude.ai',
      }),
    );
    expect((await vps_service.snapshot(claude)).identity?.account_label).toBe(
      'first@example.invalid',
    );
    expect(vps_probes).toBe(2);

    // Unrelated .claude.json churn and profileFetchedAt do not re-probe.
    writeFileSync(
      vps_paths.claude_json_path,
      JSON.stringify({
        numStartups: 42,
        oauthAccount: {
          accountUuid: 'acct-1',
          emailAddress: 'first@example.invalid',
          organizationUuid: 'org-1',
          profileFetchedAt: '2026-11-19T12:00:00Z',
        },
      }),
    );
    await vps_service.snapshot(claude);
    expect(vps_probes).toBe(2);

    // Changing .credentials.json metadata invalidates without reading contents.
    utimesSync(vps_paths.credentials_path, new Date(2_000_000), new Date(2_000_000));
    await vps_service.snapshot(claude);
    expect(vps_probes).toBe(3);

    // Mac layout: no .credentials.json exists; Keychain login switches update ~/.claude.json.
    const mac_home = join(root, 'mac-home');
    const mac_claude_home = join(mac_home, '.claude');
    mkdirSync(mac_claude_home, { recursive: true });
    const mac_paths = claude_auth_paths(mac_claude_home, { HOME: mac_home }, mac_home);
    writeFileSync(
      mac_paths.claude_json_path,
      JSON.stringify({
        oauthAccount: { accountUuid: 'mac-1', emailAddress: 'mac-one@example.invalid' },
      }),
    );
    let mac_probes = 0;
    const mac_status = create_claude_status({
      catalog: null,
      read_freshness: () => read_claude_auth_freshness(mac_paths),
    });
    const mac_service = new BackendSnapshotService({
      environment: vps_env,
      machine_catalog: true,
      sources: [
        {
          instance_id: claude,
          executable_name: 'claude',
          status: {
            ...mac_status,
            probe: async (context) => {
              mac_probes++;
              return mac_status.probe(context);
            },
          },
        },
      ],
    });
    await mac_service.snapshot(claude);
    expect(mac_probes).toBe(1);
    writeFileSync(
      mac_paths.claude_json_path,
      JSON.stringify({
        oauthAccount: { accountUuid: 'mac-2', emailAddress: 'mac-two@example.invalid' },
      }),
    );
    await mac_service.snapshot(claude);
    expect(mac_probes).toBe(2);
    writeFileSync(mac_paths.claude_json_path, JSON.stringify({ numStartups: 5 }));
    await mac_service.snapshot(claude);
    expect(mac_probes).toBe(3);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// Distinct gap: codex_machine_sequence only exercises machine_catalog with login: () => null,
// leaving freshness_moved discard in machine_catalog: true unguarded.
test('machine_catalog preserves freshness_moved before the 12-hour backstop even when version is unchanged', async () => {
  const codex = parse_backend_instance_id('codex');
  const environment = recorded_machine();
  let key = 'account-one';
  let probes = 0;
  const service = new BackendSnapshotService({
    environment,
    now: () => new Date('2026-11-19T12:00:00Z'),
    machine_catalog: true,
    sources: [
      {
        instance_id: codex,
        executable_name: 'codex',
        status: {
          version_detects_upgrade: true,
          freshness_key: () => key,
          probe: async () => {
            probes++;
            const { backend_key: _key, ...draft } = card('0.150.0');
            return {
              ...draft,
              identity: {
                status: 'authenticated',
                account_label: key,
                detail: null,
                login_command: 'codex login',
              },
            };
          },
        },
      },
    ],
  });
  expect((await service.snapshot(codex)).identity?.account_label).toBe('account-one');
  expect(probes).toBe(1);
  expect((await service.snapshot(codex)).identity?.account_label).toBe('account-one');
  expect(probes).toBe(1);
  key = 'account-two';
  expect((await service.snapshot(codex)).identity?.account_label).toBe('account-two');
  expect(probes).toBe(2);
});

// Distinct gap: version_detects_upgrade had no home-mode test proving real_path and mtimeMs
// invalidate kept cards without running `--version` on ordinary GET reads.
test('home BackendSnapshotService invalidates kept cards when executable real_path or mtimeMs changes', async () => {
  const antigravity = parse_backend_instance_id('antigravity');
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('antigravity', '/synthetic/bin/agy');
  environment.real_paths.set('/synthetic/bin/agy', '/synthetic/versions/1.5.10/agy');
  environment.modified_times.set('/synthetic/versions/1.5.10/agy', 1000);
  let version = '1.5.10';
  let probes = 0;
  const service = new BackendSnapshotService({
    environment,
    sources: [
      {
        instance_id: antigravity,
        executable_name: 'antigravity',
        status: {
          version_detects_upgrade: true,
          probe: async () => {
            probes++;
            const { backend_key: _key, ...draft } = card(version);
            return draft;
          },
        },
      },
    ],
  });

  expect((await service.snapshot(antigravity)).version).toBe('1.5.10');
  expect(probes).toBe(1);
  expect((await service.snapshot(antigravity)).version).toBe('1.5.10');
  expect(probes).toBe(1);
  expect(environment.commands).toEqual([]);

  // In-place binary update changes mtimeMs.
  version = '1.5.11';
  environment.modified_times.set('/synthetic/versions/1.5.10/agy', 2000);
  expect((await service.snapshot(antigravity)).version).toBe('1.5.11');
  expect(probes).toBe(2);
  expect(environment.commands).toEqual([]);

  // Symlink swap changes real_path.
  version = '1.5.12';
  environment.real_paths.set('/synthetic/bin/agy', '/synthetic/versions/1.5.12/agy');
  environment.modified_times.set('/synthetic/versions/1.5.12/agy', 2000);
  expect((await service.snapshot(antigravity)).version).toBe('1.5.12');
  expect(probes).toBe(3);
  expect(environment.commands).toEqual([]);
});
