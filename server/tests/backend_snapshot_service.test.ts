/** Snapshot service ordering does not need a provider process or an HTTP host. */
import { expect, test } from 'vitest';
import { BackendSnapshotService } from '../conversation/snapshot/service.ts';
import type {
  BackendSnapshot,
  BackendProbeEnvironment,
  SnapshotProbeOptions,
} from '../conversation/snapshot/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { BackendStateStore } from '../conversation/backend_state.ts';
import { fixture } from './ticket_fixture.ts';
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
