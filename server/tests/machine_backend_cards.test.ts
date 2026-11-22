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
import { BackendStateStore } from '../conversation/backend_state.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import type { BackendHttpRuntime, BackendSnapshot } from '../conversation/snapshot/contracts.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { run_backend_request } from '../executor/backend_controls.ts';
import { decode_machine_usage } from '../conversation/backend_usage/machine.ts';
import { VpsBackendControlRelay } from '../executor/backend_control_relay.ts';
import { Deferred } from '../core/deferred.ts';
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
  available_models: [],
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
    {
      kind: 'five_hour' as const,
      model_scope: null,
      scope_label: null,
      used_percent: percent,
      resets_at: new Date('2026-11-22T15:00:00Z'),
    },
  ],
});

test.each([HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID])(
  'machine %s cards and sign-in never borrow the server allowance or provider controller',
  async (machine_id) => {
    const database = fixture();
    const state = new BackendStateStore(database);
    state.keep_successful_usage(usage(91));
    state.keep_successful_usage(usage(17), machine_id);
    const home_sign_in = vi.fn(() => null);
    const operations: string[] = [];
    let available = true;
    const runtime: BackendHttpRuntime = {
      database,
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
        snapshot: async () => snapshot,
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
      await app.inject({
        method: 'POST',
        url: `/api/conversation/backends/codex/sign-in?machine_id=${machine_id}`,
      });
      expect(operations).toContain('sign_in_start');
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
// account command, retains the established result, or allows its longer command deadline.
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
  let wait_seconds = 0;
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
        wait_seconds = seconds ?? 0;
        return {
          command_id: 'update',
          child_outcome: null,
          model_list: null,
          backend_result: await run_backend_request(
            admitted,
            undefined,
            options?.signal,
            undefined,
            snapshots,
          ),
        };
      },
      withdraw_waiting: withdraw,
    },
    { is_present: () => true, supports_backend_controls: () => true },
  );
  const result = decode_machine_update(
    await relay.request({ backend_key: key, operation: 'update_backend' }),
  );
  expect(result).toEqual({
    outcome: 'succeeded',
    detail: 'Updated to 2.',
    output_tail: 'machine updated',
  });
  expect(environment.commands).toEqual([command]);
  expect(wait_seconds).toBeGreaterThan(600);
  expect(withdraw).toHaveBeenCalledWith('update', 'delivery_withdrawn');
});
