import type { Database } from '../core/database/contracts.ts';
import { seed_machine_lease } from '../machines/testing/lease.ts';
import { MacPresenceService } from '../executor/vps_presence.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { BackendSnapshotService } from '../conversation/snapshot/service.ts';
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import { decode_value } from '../executor/protocol.ts';
import type { BackendRequest } from '../executor/wire.generated.ts';
import { decode_machine_update } from '../conversation/snapshot/machine.ts';
/** Distinct gap: Home-only card tests cannot prove provider state stays with its machine. */
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import {
  BackendPickerResources,
  backend_resource_key,
} from '../conversation/snapshot/resource_versions.ts';
import { createRoutes as createResourceRoutes } from '../resource_versions/routes.ts';
import { VpsCatalogRelay } from '../executor/catalog_relay.ts';
import { BackendStateStore, save_machine_model_list } from '../conversation/backend_state.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import type { BackendHttpRuntime, BackendSnapshot } from '../conversation/snapshot/contracts.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { run_backend_request } from '../executor/backend_controls.ts';
import { decode_machine_usage } from '../conversation/backend_usage/machine.ts';
import type { BackendUsageAdapter } from '../conversation/backend_usage/contracts.ts';
import { usage_window } from '../conversation/backend_usage/logic/windows.ts';
import { VpsBackendControlRelay } from '../executor/backend_control_relay.ts';
import { Deferred } from '../core/deferred.ts';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createUsageRoutes } from '../conversation/usage/routes.ts';
import { AntigravityUsageAdapter } from '../drivers/antigravity/runtime/allowance_usage.ts';
import { ClaudeUsageAdapter } from '../drivers/claude/runtime/allowance_usage.ts';
const key = parse_backend_instance_id('codex');
const snapshot: BackendSnapshot = {
  backend_key: key,
  installed: true,
  executable_path: '/machine/codex',
  version: '1',
  identity: {
    status: 'authenticated',
    account_label: 'Machine account',
    detail: null,
    login_command: null,
  },
  available_models: [
    {
      model_id: 'native',
      display_name: null,
      detail: null,
      reasoning_effort_options: null,
      selected_variant: null,
      version: [],
      resolved_model_id: null,
    },
  ],
  reasoning_effort_options: [],
  default_model_id: null,
  default_reasoning_effort: null,
  update_advisory: null,
  diagnoses: [],
  models_complete: true,
};
const usage = (percent: number) => ({
  backend_key: key,
  outcome: 'succeeded' as const,
  detail: null,
  observed_at: new Date('2026-11-22T10:00:00Z'),
  windows: [
    usage_window({
      id: 'five_hour',
      kind: 'five_hour',
      model_scope: null,
      used_percent: percent,
      resets_at: new Date('2026-11-22T15:00:00Z'),
    }),
  ],
});

test.each([HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID])(
  'machine %s cards and sign-in never borrow the server allowance or provider controller',
  async (machine_id) => {
    const database = fixture();
    database.write((tx) => {
      seed_machine_lease(tx, machine_id);
      save_machine_model_list(tx, machine_id, snapshot);
      // Legal older wire snapshots omit optional model fields. Versioned reads must
      // normalize them exactly as the ordinary catalog relay does.
      tx.run(
        'UPDATE machine_backend_snapshots SET snapshot_json=? WHERE machine_id=? AND backend_key=?',
        [
          JSON.stringify({ ...snapshot, available_models: [{ model_id: 'native' }] }),
          machine_id,
          key,
        ],
      );
    });
    const write = vi.fn();
    const backend_database: Database = {
      read: database.read,
      close: database.close,
      write(work, options) {
        write();
        return database.write(work, options);
      },
    };
    const state = new BackendStateStore(backend_database);
    state.keep_successful_usage(usage(91));
    state.keep_successful_usage(usage(17), machine_id);
    const home_sign_in = vi.fn(() => null);
    const operations: string[] = [];
    let available = true;
    let sign_in_state = 'pending';
    const catalog_snapshot = vi.fn(async (_key: unknown, _options: unknown) => snapshot);
    const runtime: BackendHttpRuntime = {
      database: backend_database,
      backend_state: state,
      backend_ids: [key],
      drivers: [],
      driver_of: () => null,
      reports_allowance_usage: () => false,
      backend_snapshots: {
        retained: () => snapshot,
        snapshot: async () => snapshot,
        snapshots: async () => [snapshot],
        update_backend: vi.fn(),
      },
      backend_usage: { refresh: vi.fn() },
      sign_in: home_sign_in,
      settled: async () => {},
      machine_catalog: () => ({
        retained: () => snapshot,
        snapshot: catalog_snapshot,
        identity: null,
        ready: true,
        owns_conversation: () => false,
      }),
      machine_backends: (selected) => {
        expect(selected).toBe(machine_id);
        return {
          get available() {
            return available;
          },
          async request(request) {
            operations.push(request.operation);
            if (!available) throw new Error('machine absent');
            if (request.operation === 'capabilities')
              return { can_sign_in: true, reports_allowance_usage: true };
            if (request.operation === 'refresh_usage') return JSON.parse(JSON.stringify(usage(23)));
            if (request.operation === 'sign_in_read') return { sign_in: { state: sign_in_state } };
            return { sign_in: null };
          },
        };
      },
    };
    const writes = new ConversationHttpWriteScope();
    const app = await createApp(
      loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: fixture_path(database) }),
      {
        database,
        areas: [
          { prefix: '/api', routes: createResourceRoutes([new BackendPickerResources(runtime)]) },
          {
            prefix: '/api/conversation',
            routes: createBackendRoutes(runtime, { available: true, writes }),
          },
        ],
      },
    );
    try {
      const path = `/api/conversation/backends?machine_id=${machine_id}`;
      const card = (await app.inject({ method: 'GET', url: path })).json().backends[0];
      expect(card.cached_usage.windows[0].used_percent).toBe(17);
      expect([card.can_sign_in, card.reports_allowance_usage]).toEqual([true, true]);
      // Gap: ordinary card checks did not prove a warm read is command/write free, or
      // that the versioned envelope shares its saved body and epoch with revision probes.
      operations.length = 0;
      write.mockClear();
      const versioned = (await app.inject({ method: 'GET', url: path + '&versioned=true' })).json();
      expect(versioned.body.backends[0]).toEqual(card);
      const resource_key = backend_resource_key({ machine_id });
      const probe = (
        await app.inject({
          method: 'GET',
          url: '/api/resource-versions?keys=' + encodeURIComponent(JSON.stringify([resource_key])),
        })
      ).json();
      expect(probe.epoch).toBe(versioned.epoch);
      expect(probe.resources).toEqual([{ key: resource_key, revision: versioned.revision }]);
      database.write((tx) =>
        tx.run('UPDATE machines SET name=? WHERE machine_id=?', ['Renamed', machine_id]),
      );
      const unchanged = (
        await app.inject({
          method: 'GET',
          url: '/api/resource-versions?keys=' + encodeURIComponent(JSON.stringify([resource_key])),
        })
      ).json();
      expect(unchanged.resources[0].revision).toBe(versioned.revision);
      write.mockClear();
      expect(operations).toEqual([]);
      expect(write).not.toHaveBeenCalled();
      await app.inject({ method: 'GET', url: path + '&refresh=true' });
      expect(operations).toEqual(['capabilities']);
      expect(write).not.toHaveBeenCalled();
      await app.inject({
        method: 'POST',
        url: `/api/conversation/backends/codex/sign-in?machine_id=${machine_id}`,
      });
      expect(operations).toContain('sign_in_start');
      const discovered = catalog_snapshot.mock.calls.length;
      const signin_path = `/api/conversation/backends/codex/sign-in?machine_id=${machine_id}`;
      await app.inject({ method: 'GET', url: signin_path });
      expect(catalog_snapshot.mock.calls.length).toBe(discovered);
      sign_in_state = 'confirmed';
      await app.inject({ method: 'GET', url: signin_path });
      expect(catalog_snapshot.mock.calls.length).toBe(discovered + 1);
      expect(catalog_snapshot.mock.calls.at(-1)?.[1]).toMatchObject({ refresh: true });
      await app.inject({ method: 'DELETE', url: signin_path });
      expect(catalog_snapshot.mock.calls.length).toBe(discovered + 2);
      expect(home_sign_in).not.toHaveBeenCalled();
      if (machine_id !== HOME_MACHINE_ID)
        expect(state.read_usage(key, HOME_MACHINE_ID)?.windows[0]?.used_percent).toBe(91);
      available = false;
      const cached = (await app.inject({ method: 'GET', url: path })).json().backends[0];
      expect(cached.cached_usage.windows[0].used_percent).toBe(17);
    } finally {
      await app.close();
    }
  },
);

test('machine driver capabilities and allowance failures retain distinct outcomes', async () => {
  expect(
    await run_backend_request({ backend_key: key, operation: 'capabilities' }, undefined),
  ).toEqual({ can_sign_in: false, reports_allowance_usage: false });
  expect(
    (await run_backend_request({ backend_key: key, operation: 'refresh_usage' }, undefined))
      .outcome,
  ).toBe('unavailable');
  const database = fixture();
  const state = new BackendStateStore(database);
  state.keep_successful_usage(usage(17), ORIGINAL_MAC_MACHINE_ID);
  const failure = decode_machine_usage(key, {
    backend_key: key,
    outcome: 'unauthenticated',
    detail: 'Sign in',
    observed_at: null,
    windows: [],
  });
  expect(failure.outcome).toBe('unauthenticated');
  expect(() => state.keep_successful_usage(failure, ORIGINAL_MAC_MACHINE_ID)).toThrow();
  expect(state.read_usage(key, ORIGINAL_MAC_MACHINE_ID)?.windows[0]?.used_percent).toBe(17);
  expect(() =>
    decode_machine_usage(key, { ...JSON.parse(JSON.stringify(usage(5))), backend_key: 'claude' }),
  ).toThrow();
});

test('relay stop retains a committed control until its mailbox withdrawal settles', async () => {
  const put = new Deferred<void>(),
    entered = new Deferred<void>(),
    withdrawal = new Deferred<void>();
  const withdraw = vi.fn(async () => {
    await withdrawal.promise;
    return true;
  });
  const relay = new VpsBackendControlRelay(
    {
      async put_backend_request() {
        entered.resolve();
        await put.promise;
        return {
          command_sequence: 1,
          command_id: 'control',
          created_at_vps_ms: 1,
          child_command: null,
          model_list_request: null,
        };
      },
      wait_result: vi.fn(),
      withdraw_waiting: withdraw,
    },
    { is_present: () => true, supports_backend_controls: () => true },
  );
  const request = relay.request({ backend_key: key, operation: 'sign_in_start' });
  const refused = expect(request).rejects.toThrow('stopped');
  await entered.promise;
  let stopped = false;
  const stop = relay.stop({ deadline: performance.now() / 1000 + 5 }).then(() => {
    stopped = true;
  });
  put.resolve();
  await vi.waitFor(() => expect(withdraw).toHaveBeenCalledWith('control', 'delivery_withdrawn'));
  expect(stopped).toBe(false);
  withdrawal.resolve();
  await Promise.all([refused, stop]);
  await expect(relay.request({ backend_key: key, operation: 'sign_in_start' })).rejects.toThrow(
    'stopped',
  );
});

// The return URL contains a one-time provider code. The durable command must contain only a reference.
test('browser return addresses cross a one-time owner channel and never enter the mailbox payload', async () => {
  let command: Record<string, unknown> | undefined;
  const waiting = new Deferred<void>();
  const release = new Deferred<void>();
  const relay = new VpsBackendControlRelay(
    {
      async put_backend_request(request) {
        command = { ...request };
        waiting.resolve();
        return {
          command_sequence: 1,
          command_id: 'secret',
          created_at_vps_ms: 1,
          child_command: null,
          model_list_request: null,
        };
      },
      async wait_result() {
        await release.promise;
        return {
          command_id: 'secret',
          child_outcome: null,
          model_list: null,
          backend_result: { sign_in: null },
        };
      },
      async withdraw_waiting() {
        return false;
      },
    },
    { is_present: () => true, supports_backend_controls: () => true },
  );
  const action = relay.submit_return_url(key, 'session', 'http://loopback/?code=one-time-secret');
  await waiting.promise;
  expect(JSON.stringify(command)).not.toContain('one-time-secret');
  const secret_id = command!.secret_id as string;
  const other = new VpsBackendControlRelay(
    { put_backend_request: vi.fn(), wait_result: vi.fn(), withdraw_waiting: vi.fn() },
    { is_present: () => true, supports_backend_controls: () => true },
  );
  expect(() => other.take_secret(secret_id)).toThrow('no longer available');
  expect(relay.take_secret(secret_id)).toBe('http://loopback/?code=one-time-secret');
  expect(() => relay.take_secret(secret_id)).toThrow('no longer available');
  release.resolve();
  await action;
  expect(() => relay.take_secret(secret_id)).toThrow('no longer available');
});

// An adjacent older executor accepts the lease but cannot decode the new mailbox payload.
test('a machine without control support receives no backend command', async () => {
  const put_backend_request = vi.fn();
  const relay = new VpsBackendControlRelay(
    { put_backend_request, wait_result: vi.fn(), withdraw_waiting: vi.fn() },
    { is_present: () => true, supports_backend_controls: () => false },
  );
  expect(relay.available).toBe(false);
  await expect(relay.request({ backend_key: key, operation: 'sign_in_start' })).rejects.toThrow(
    'unavailable',
  );
  expect(put_backend_request).not.toHaveBeenCalled();
});

// A machine reply must not turn a failed action into an HTTP success or an invalid status.
test('backend relay admits only the statuses its provider control protocol defines', async () => {
  let status = 200;
  const relay = new VpsBackendControlRelay(
    {
      async put_backend_request() {
        return {
          command_sequence: 1,
          command_id: 'error',
          created_at_vps_ms: 1,
          child_command: null,
          model_list_request: null,
        };
      },
      async wait_result() {
        return {
          command_id: 'error',
          child_outcome: null,
          model_list: null,
          backend_result: { error: { status, message: 'Provider refused' } },
        };
      },
      async withdraw_waiting() {
        return false;
      },
    },
    { is_present: () => true, supports_backend_controls: () => true },
  );
  const action = () => relay.request({ backend_key: key, operation: 'sign_in_start' });
  await expect(action()).rejects.toMatchObject({ statusCode: 502 });
  status = 422;
  await expect(action()).rejects.toMatchObject({ statusCode: 422 });
});

// Gap: sign-in/allowance relay tests do not prove that an update reaches the machine's
// account command, retains the established result, or preserves its command deadline without an aggregate cutoff.
// This uses the existing relay boundary and a recording environment, with no browser or host.
test('provider update crosses the backend relay and executes in the machine account environment', async () => {
  const environment = new RecordingBackendProbeEnvironment();
  const command = ['/machine/account/bin/codex', 'update'];
  environment.executables.set('codex', command[0]);
  environment.outcomes.set(JSON.stringify(command), {
    exit_code: 0,
    standard_output: 'machine updated',
    standard_error: '',
  });
  const snapshots = new BackendSnapshotService({
    environment,
    sources: [
      {
        instance_id: key,
        executable_name: 'codex',
        status: {
          version_detects_upgrade: false,
          async probe() {
            return {
              ...snapshot,
              version: environment.commands.length ? '2' : '1',
              update_advisory: {
                install_method: 'native',
                update_command: command,
                latest_version: '2',
                update_available: true,
                detail: 'Update the machine CLI.',
              },
            };
          },
        },
      },
    ],
  });
  let admitted!: BackendRequest;
  const withdraw = vi.fn(async () => false);
  let wait_seconds: number | null | undefined;
  const database = fixture();
  const peer_at_version = (protocol_version: number) => {
    database.write((tx) =>
      tx.run(
        "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.protocol_version',?),lease_json=json_set(lease_json,'$.backend_controls',json('true'),'$.identity.protocol_version',?) WHERE machine_id=?",
        [protocol_version, protocol_version, HOME_MACHINE_ID],
      ),
    );
    return new MacPresenceService(database, {} as never, {
      machine_id: HOME_MACHINE_ID,
      changes: new ChangeSignal(),
    });
  };
  let peer = peer_at_version(26);
  const relay = new VpsBackendControlRelay(
    {
      async put_backend_request(request) {
        admitted = decode_value(request, 'BackendRequest');
        return {
          command_sequence: 1,
          command_id: 'update',
          created_at_vps_ms: 1,
          child_command: null,
          model_list_request: null,
          backend_request: admitted,
        };
      },
      async wait_result(_id, seconds, options) {
        wait_seconds = seconds;
        return {
          command_id: 'update',
          child_outcome: null,
          model_list: null,
          backend_result: await run_backend_request(admitted, undefined, {
            signal: options?.signal,
            snapshots,
          }),
        };
      },
      withdraw_waiting: withdraw,
    },
    {
      is_present: () => peer.is_present(),
      supports_backend_controls: (operation) => peer.supports_backend_controls(operation),
    },
  );
  await expect(
    relay.request({ backend_key: key, operation: 'update_backend' }),
  ).rejects.toMatchObject({
    statusCode: 503,
    message: 'Update machine code before you update a provider CLI.',
  });
  expect(admitted).toBeUndefined();
  await expect(
    relay.request({ backend_key: key, operation: 'capabilities' }),
  ).resolves.toMatchObject({ can_sign_in: false });
  expect(wait_seconds).toBe(60);
  expect(environment.commands).toEqual([]);
  peer = peer_at_version(27);
  const result = decode_machine_update(
    await relay.request({ backend_key: key, operation: 'update_backend' }),
  );
  expect(result).toEqual({
    outcome: 'succeeded',
    detail: 'Updated to 2.',
    output_tail: 'machine updated',
  });
  expect(environment.commands).toEqual([command]);
  expect(wait_seconds).toBeNull();
  expect(withdraw).toHaveBeenCalledWith('update', 'delivery_withdrawn');
});

// The old retained path silently started a background model command. A route-only fake
// catalog cannot detect that, so this proves the real relay with the existing SQL fixture.
test('warm catalog reads admit no command while explicit refresh does', async () => {
  const database = fixture();
  const put = vi.fn(async () => ({ command_id: 'catalog' }) as never);
  const relay = new VpsCatalogRelay(
    database,
    { owns_conversation: () => false },
    {
      machine_id: HOME_MACHINE_ID,
      current_identity: () => null,
      is_present: () => true,
    },
    {
      put_model_list_request: put,
      wait_result: async () => ({ model_list: snapshot }) as never,
      withdraw_waiting: vi.fn(),
    },
  );
  expect(await relay.snapshot(key)).toMatchObject(snapshot);
  expect(put).toHaveBeenCalledOnce();
  put.mockClear();
  expect(await relay.snapshot(key)).toMatchObject(snapshot);
  expect(put).not.toHaveBeenCalled();
  expect(await relay.snapshot(key, { refresh: true })).toMatchObject(snapshot);
  expect(put).toHaveBeenCalledOnce();
  await relay.stop({ deadline: performance.now() / 1000 + 5 });
});

// Gap: saved false values, provider changes, and old-incarnation replies are invisible
// to the ordinary positive card fixture. The existing database fixture proves each boundary.
test('capability facts distinguish missing and false and reject stale machine replies', () => {
  const database = fixture();
  const state = new BackendStateStore(database);
  const value = { can_sign_in: false, reports_allowance_usage: false };
  expect(state.read_machine_capabilities(HOME_MACHINE_ID, key)).toBeNull();
  const initial = state.capability_source(HOME_MACHINE_ID, key);
  expect(state.keep_machine_capabilities(HOME_MACHINE_ID, key, value, initial)).toBe(true);
  expect(state.read_machine_capabilities(HOME_MACHINE_ID, key)).toEqual(value);
  expect(state.read_machine_capabilities(ORIGINAL_MAC_MACHINE_ID, key)).toBeNull();
  database.write((tx) => save_machine_model_list(tx, HOME_MACHINE_ID, snapshot));
  const provider = state.capability_source(HOME_MACHINE_ID, key);
  expect(provider).not.toBe(initial);
  expect(state.capabilities_current(HOME_MACHINE_ID, key, provider)).toBe(false);
  expect(state.keep_machine_capabilities(HOME_MACHINE_ID, key, value, provider)).toBe(true);
  database.write((tx) =>
    tx.run('UPDATE mac_executor_presence SET continuous_since_ms=123 WHERE machine_id=?', [
      HOME_MACHINE_ID,
    ]),
  );
  const reconnected = state.capability_source(HOME_MACHINE_ID, key);
  expect(reconnected).not.toBe(provider);
  expect(
    state.keep_machine_capabilities(
      HOME_MACHINE_ID,
      key,
      { can_sign_in: true, reports_allowance_usage: true },
      provider,
    ),
  ).toBe(false);
  expect(state.read_machine_capabilities(HOME_MACHINE_ID, key)).toEqual(value);
  expect(state.capabilities_current(HOME_MACHINE_ID, key, reconnected)).toBe(false);
});

// Risk: the 22 Nov regression. Antigravity's family buckets collided on one stored key (HTTP 500
// on VPS 1, duplicate ids on Mac 1), an untouched bucket without a reset was dropped, the Claude
// Fable window was dropped against three Fable catalog models, and one backend a machine could
// not answer for failed the whole refresh. The adapters run for real, behind the executor's own
// serialization, so the proof covers adapter, wire, store, Limits and cards on both machines.
// A family window must not cover a model another window names exactly (Gemini and Gemini 4 Argon).
test.each([HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID])(
  'machine %s refresh keeps every Antigravity bucket and the Fable window when Codex fails',
  async (machine_id) => {
    const claude_key = parse_backend_instance_id('claude');
    const antigravity_key = parse_backend_instance_id('antigravity');
    const folder = mkdtempSync(join(tmpdir(), 'machine-usage-'));
    const reset = '2026-11-30T21:02:00Z';
    try {
      const claude_login = join(folder, '.credentials.json');
      writeFileSync(claude_login, JSON.stringify({ claudeAiOauth: { accessToken: 'claude' } }));
      const antigravity_login = join(folder, 'antigravity-oauth-token');
      writeFileSync(
        antigravity_login,
        JSON.stringify({ token: { access_token: 'agy', expiry: '2026-12-01T00:00:00Z' } }),
      );
      const bucket = (bucketId: string, window: string, remaining: number, at?: string) => ({
        bucketId,
        window,
        remainingFraction: remaining,
        ...(at === undefined ? {} : { resetTime: at }),
      });
      // As on 26 Nov: the daily host names no reset for the untouched Claude and GPT buckets.
      const quota = (daily: boolean) => ({
        groups: [
          {
            displayName: 'Gemini Models',
            buckets: [
              bucket('gemini-weekly', 'weekly', 0.62, reset),
              bucket('gemini-5h', '5h', 0.65, reset),
            ],
          },
          {
            displayName: 'Claude and GPT models',
            buckets: [
              bucket('3p-weekly', 'weekly', 1, daily ? undefined : reset),
              bucket('3p-5h', '5h', 1, daily ? undefined : reset),
            ],
          },
          {
            displayName: 'Gemini 4 Argon',
            buckets: [bucket('gemini-argon-wtu-weekly', 'weekly', 0.25, reset)],
          },
        ],
      });
      const sources = new Map<string, BackendUsageAdapter>([
        [
          antigravity_key,
          new AntigravityUsageAdapter({
            login_path: antigravity_login,
            http_post: async (url) => ({ status_code: 200, body: quota(url.includes('daily')) }),
            refresh_login: async () => {},
            now: () => new Date('2026-11-26T00:30:00Z'),
          }),
        ],
        [
          claude_key,
          new ClaudeUsageAdapter({
            credential_path: claude_login,
            http_get: async () => ({
              status_code: 200,
              body: {
                limits: [
                  { group: 'weekly', utilization: 79, resets_at: '2026-11-28T15:00:00Z' },
                  {
                    scope: { model: { display_name: 'Fable' } },
                    utilization: 23,
                    resets_at: '2026-11-28T15:00:00Z',
                  },
                ],
              },
            }),
            now: () => new Date('2026-11-26T00:30:00Z'),
          }),
        ],
      ]);
      const model = (model_id: string, display_name: string) => ({
        ...snapshot.available_models[0]!,
        model_id,
        display_name,
      });
      const cards = new Map<string, BackendSnapshot>([
        [key, snapshot],
        [
          claude_key,
          {
            ...snapshot,
            backend_key: claude_key,
            available_models: [
              model('claude-fable-5', 'Fable 5'),
              model('claude-fable-5-1', 'Fable 5.1'),
              model('claude-fable-5-2', 'Fable 5.2'),
              model('claude-opus-5-5', 'Opus 5.5'),
            ],
          },
        ],
        [
          antigravity_key,
          {
            ...snapshot,
            backend_key: antigravity_key,
            available_models: [
              model('gemini-4-neon', 'Gemini 4 Neon'),
              model('gemini-4-argon', 'Gemini 4 Argon'),
            ],
          },
        ],
      ]);
      const database = fixture();
      database.write((tx) => seed_machine_lease(tx, machine_id));
      const state = new BackendStateStore(database);
      const backend_ids = [key, claude_key, antigravity_key];
      const runtime: BackendHttpRuntime = {
        database,
        backend_state: state,
        backend_ids,
        drivers: [],
        driver_of: () => null,
        reports_allowance_usage: () => true,
        backend_snapshots: {
          retained: () => snapshot,
          snapshot: async () => snapshot,
          snapshots: async () => [snapshot],
          update_backend: vi.fn(),
        },
        backend_usage: { refresh: vi.fn() },
        sign_in: () => null,
        settled: async () => {},
        machine_catalog: () => ({
          retained: (backend_key) => cards.get(backend_key)!,
          snapshot: async (backend_key) => cards.get(backend_key)!,
          identity: null,
          ready: true,
          owns_conversation: () => false,
        }),
        machine_backends: () => ({
          available: true,
          async request(request) {
            if (request.operation === 'capabilities')
              return { can_sign_in: false, reports_allowance_usage: true };
            if (request.operation !== 'refresh_usage') return {};
            if (request.backend_key === key) throw new Error('Codex did not answer');
            const allowance_usage = sources.get(request.backend_key)!;
            return run_backend_request(request, { allowance_usage, status: {} as never });
          },
        }),
      };
      const app = await createApp(
        loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: fixture_path(database) }),
        {
          database,
          areas: [
            {
              prefix: '/api/conversation',
              routes: createBackendRoutes(runtime, {
                available: true,
                writes: new ConversationHttpWriteScope(),
              }),
            },
            {
              prefix: '/api/conversation/usage',
              routes: createUsageRoutes({
                conversation_runtime_available: true,
                backend_ids,
                backend_state: state,
                recorded_usage: {} as never,
              }),
            },
          ],
        },
      );
      try {
        const refreshed = await app.inject({
          method: 'POST',
          url: `/api/conversation/backends/refresh?machine_id=${machine_id}`,
        });
        expect(refreshed.statusCode).toBe(200);
        expect(refreshed.json().usage_outcomes).toEqual([
          { backend_key: 'codex', outcome: 'failed', detail: expect.any(String) },
          { backend_key: 'claude', outcome: 'succeeded', detail: null },
          { backend_key: 'antigravity', outcome: 'succeeded', detail: null },
        ]);
        const limits = (
          await app.inject({
            method: 'GET',
            url: `/api/conversation/usage/limits?machine_id=${machine_id}`,
          })
        ).json();
        expect(Object.keys(limits)).toEqual(['claude', 'antigravity']);
        const rows = (backend: string) =>
          limits[backend].windows.map((window: Record<string, unknown>) => [
            window.id,
            window.label,
            window.usedPercent,
            window.resetsAt ?? null,
          ]);
        expect(rows('antigravity')).toEqual([
          ['gemini-weekly', '7 days · Gemini', 38, '2026-11-30T21:02:00Z'],
          ['gemini-5h', '5 hours · Gemini', 35, '2026-11-30T21:02:00Z'],
          ['3p-weekly', '7 days · Claude and GPT', 0, null],
          ['3p-5h', '5 hours · Claude and GPT', 0, null],
          ['gemini-argon-wtu-weekly', '7 days · Gemini 4 Argon', 75, '2026-11-30T21:02:00Z'],
        ]);
        expect(rows('claude')).toEqual([
          ['seven_day', '7 days', 79, '2026-11-28T15:00:00Z'],
          ['seven_day_fable', '7 days · Fable', 23, '2026-11-28T15:00:00Z'],
        ]);
        // A family window covers each model of its family that no window names exactly, and
        // cards leave out a window with no reset time.
        const backends = (
          await app.inject({
            method: 'GET',
            url: `/api/conversation/backends?machine_id=${machine_id}`,
          })
        ).json().backends;
        const covered = (backend: string) =>
          backends
            .find((value: { backend_key: string }) => value.backend_key === backend)
            .cached_usage.windows.map((window: { model_id: string; model_ids: string[] }) => [
              window.model_id,
              window.model_ids,
            ]);
        expect(covered('claude')).toEqual([
          [null, []],
          ['Fable', ['claude-fable-5', 'claude-fable-5-1', 'claude-fable-5-2']],
        ]);
        expect(covered('antigravity')).toEqual([
          ['Gemini', ['gemini-4-neon']],
          ['Gemini', ['gemini-4-neon']],
          ['Gemini 4 Argon', ['gemini-4-argon']],
        ]);
      } finally {
        await app.close();
      }
    } finally {
      rmSync(folder, { recursive: true, force: true });
    }
  },
);

// Gap: allowance relay tests do not prove that a joined machine's recorded usage crosses the
// relay under the wire schema, or that older machine code is refused before any command exists.
// This reuses the SQL presence fixture and the real relay; the machine's scan is a stub.
test('a joined machine answers its recorded usage summary through the backend relay', async () => {
  const database = fixture();
  database.write((tx) => seed_machine_lease(tx, ORIGINAL_MAC_MACHINE_ID));
  // The presence service reads its record once, as each lease replaces it.
  const advertise = (recorded_usage: boolean) => {
    database.write((tx) =>
      tx.run(
        "UPDATE mac_executor_presence SET lease_json=json_set(lease_json,'$.backend_controls',json('true'),'$.recorded_usage',json(?)) WHERE machine_id=?",
        [String(recorded_usage), ORIGINAL_MAC_MACHINE_ID],
      ),
    );
    return new MacPresenceService(database, {} as never, {
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      changes: new ChangeSignal(),
    });
  };
  let peer = advertise(false);
  const summary = (hostId: string) => ({
    contractVersion: 5,
    readAt: '2026-11-26T10:00:00.000Z',
    timeZone: 'UTC',
    sinceDay: '2026-11-20',
    untilDay: '2026-11-26',
    buckets: [],
    sources: [
      {
        fingerprint: { hostId, provider: 'claude', resolvedHomePath: '/projects', volumeId: '1:2' },
        status: 'ok',
        scannedFiles: 1,
        skippedFiles: 0,
        malformedRecords: 0,
        distinctSessions: 1,
        message: null,
      },
    ],
    pricing: { status: 'unavailable', source: 'none', fetchedAt: null, knownModels: 0 },
    scanDurationMs: 3,
  });
  const machine_scan = vi.fn(async () => summary('mac-1') as never);
  let admitted: BackendRequest | undefined;
  let wait_seconds: number | null | undefined;
  const relay = new VpsBackendControlRelay(
    {
      async put_backend_request(request) {
        admitted = decode_value(request, 'BackendRequest');
        return {
          command_sequence: 1,
          command_id: 'usage',
          created_at_vps_ms: 1,
          child_command: null,
          model_list_request: null,
          backend_request: admitted,
        };
      },
      async wait_result(_id, seconds) {
        wait_seconds = seconds;
        return {
          command_id: 'usage',
          child_outcome: null,
          model_list: null,
          backend_result: await run_backend_request(admitted!, undefined, {
            recorded_usage: { readSummary: machine_scan },
          }),
        };
      },
      withdraw_waiting: vi.fn(async () => false),
    },
    {
      is_present: () => peer.is_present(),
      supports_backend_controls: (operation) => peer.supports_backend_controls(operation),
    },
  );
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: fixture_path(database) }),
    {
      database,
      areas: [
        {
          prefix: '/api/conversation/usage',
          routes: createUsageRoutes({
            conversation_runtime_available: true,
            backend_ids: [],
            backend_state: null,
            recorded_usage: { readSummary: async () => summary('vps-1') } as never,
            machine_backends: (id) => (id === ORIGINAL_MAC_MACHINE_ID ? relay : null),
          }),
        },
      ],
    },
  );
  const window = '/api/conversation/usage/summary?sinceDay=2026-11-20&untilDay=2026-11-26';
  try {
    const old_code = await app.inject({
      url: `${window}&machine_id=${ORIGINAL_MAC_MACHINE_ID}`,
    });
    expect(old_code.statusCode).toBe(503);
    expect(old_code.body).toContain('Update machine code before you read its recorded usage.');
    expect(admitted).toBeUndefined();
    peer = advertise(true);
    const machine = await app.inject({ url: `${window}&machine_id=${ORIGINAL_MAC_MACHINE_ID}` });
    expect(machine.statusCode).toBe(200);
    expect(machine.json().sources[0].fingerprint.hostId).toBe('mac-1');
    // A first scan builds the machine's cache, so it waits longer than other controls.
    expect(wait_seconds).toBe(110);
    expect(machine_scan).toHaveBeenCalledWith({
      sinceDay: '2026-11-20',
      untilDay: '2026-11-26',
      timeZone: 'UTC',
      resolution: 'day',
    });
    const home = await app.inject({ url: window });
    expect(home.json().sources[0].fingerprint.hostId).toBe('vps-1');
  } finally {
    await app.close();
  }
});
