import { fixture } from '../../../tests/ticket_fixture.ts';
import { afterEach, expect, it, vi } from 'vitest';
import { text } from 'node:stream/consumers';
import { BackendSnapshotService } from '../../../conversation/snapshot/service.ts';
import { create_codex_status } from '../../../drivers/codex/runtime/status.ts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { load_claude_sdk } from '../../../drivers/claude/runtime/sdk.ts';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import {
  ClaudeModelCatalogUnavailable,
  probe_claude_model_catalog,
} from '../../../drivers/claude/runtime/model_catalog.ts';
import { ModelCatalogCleanupFailed } from '../../../conversation/backends/catalog_cleanup.ts';
import { probe_codex_model_catalog } from '../../../drivers/codex/runtime/model_catalog.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { create_worker_supervision_runtime } from '../../../worker_supervision/composition.ts';
import { RecordingBackendProbeEnvironment } from '../../../conversation/snapshot/recording_environment.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import { create_mac_adapter_host, load_executor_drivers } from '../adapters.ts';
import { MacCommandQueues } from '../mailbox/queues.ts';
import { MacCatalog } from '../catalog/index.ts';
import { gate } from './runner_fixture.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { QueueDependencies } from '../mailbox/contracts.ts';
import * as probe_environment from '../../../conversation/snapshot/environment.ts';
import * as codex_catalog from '../../../drivers/codex/runtime/model_catalog.ts';
import * as claude_catalog from '../../../drivers/claude/runtime/model_catalog.ts';
import { create_backend_runtime } from '../../../conversation/snapshot/composition.ts';
import { create_claude_instance } from '../../../drivers/claude/runtime/instance.ts';
import { create_codex_instance } from '../../../drivers/codex/runtime/instance.ts';
import { claude_driver } from '../../../drivers/claude/driver.ts';
import { codex_driver } from '../../../drivers/codex/driver.ts';
import type { DriverHost } from '../../../drivers/core/host.ts';
import { parse_backend_instance_id } from '../../../drivers/core/identity.ts';
import { driver_registry_from_entries } from '../../../drivers/core/registry.ts';

const { query } = vi.hoisted(() => ({
  query: vi.fn<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>(),
}));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query }));
vi.mock('../../../conversation/backends/cli_compatibility.ts', async (original) => ({
  ...(await original<typeof import('../../../conversation/backends/cli_compatibility.ts')>()),
  ensure_cli_compatible: vi.fn(),
}));
await load_claude_sdk();
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it('an explicit machine card refresh checks the backend registry and returns the advisory', async () => {
  const environment = new RecordingBackendProbeEnvironment();
  const executable = '/synthetic/.local/lib/node_modules/@openai/codex/bin/codex';
  environment.executables.set('codex', executable);
  environment.writable_prefixes.add('/synthetic/.local');
  environment.registry_versions.set('@openai/codex', '2.0.0');
  environment.outcomes.set(JSON.stringify([executable, '--version']), {
    exit_code: 0,
    standard_output: '1.0.0',
    standard_error: '',
  });
  const instance_id = parse_backend_instance_id('codex');
  const catalog = new MacCatalog({
    backend_ids: [instance_id],
    snapshots: new BackendSnapshotService({
      environment,
      machine_catalog: true,
      sources: [
        {
          instance_id,
          executable_name: 'codex',
          status: create_codex_status({ catalog: null, read_login_standing: null }),
        },
      ],
    }),
    stop_sources: async () => {},
    on_failure: (error) => {
      throw error;
    },
  });
  async function card(refresh: boolean) {
    const result = await catalog.read_result('card', { backend_key: instance_id, refresh });
    try {
      return JSON.parse(await text(await result.open())).model_list;
    } finally {
      await result.close();
    }
  }
  try {
    expect((await card(false)).update_advisory).toBeNull();
    expect(environment.registry_lookups).toEqual([]);
    expect((await card(true)).update_advisory).toMatchObject({
      latest_version: '2.0.0',
      update_available: true,
    });
    await card(false);
    expect(environment.registry_lookups).toEqual(['@openai/codex']);
  } finally {
    await catalog.stop({ deadline: performance.now() / 1000 + 2 });
  }
});

it('retains catalog reads before reentrant stop and refuses sources after immediate stop', async () => {
  for (const reentrant of [true, false]) {
    let stopping: Promise<void> | undefined;
    const options = { deadline: performance.now() / 1000 + 2 };
    const snapshot = vi.fn(() => {
      stopping = catalog.stop(options);
      throw new TypeError('optional catalog absence');
    });
    const failure = vi.fn();
    const catalog = new MacCatalog({
      snapshots: { snapshot, retained: () => null },
      backend_ids: ['codex'],
      stop_sources: async () => {},
      on_failure: failure,
    });
    const reading = catalog.read_result('reentry', { backend_key: 'codex', refresh: true });
    if (reentrant) {
      const result = await reading,
        closing = result.close();
      expect(result.close()).toBe(closing);
      await closing;
    } else {
      stopping = catalog.stop(options);
      await expect(reading).rejects.toBeInstanceOf(ServerLifecycleError);
    }
    await stopping;
    await catalog.settled();
    expect(snapshot).toHaveBeenCalledTimes(reentrant ? 1 : 0);
    expect(failure).not.toHaveBeenCalled();
  }
});

it('joins failing siblings once and retains all history across a failed native cleanup retry', async () => {
  vi.spyOn(probe_environment, 'backend_io_is_test').mockReturnValue(false);
  const sibling = gate();
  const environment = Object.assign(new RecordingBackendProbeEnvironment(), {
    shutdown: vi.fn(async () => {
      await sibling.promise;
      throw errors[4];
    }),
  });
  const provider_environment = Object.assign(new RecordingBackendProbeEnvironment(), {
    shutdown: vi.fn(async () => {}),
  });
  vi.spyOn(probe_environment, 'SubprocessBackendProbeEnvironment').mockImplementation(function () {
    return provider_environment as never;
  });
  const errors = [
    'Codex probe',
    'Codex close',
    'Claude probe',
    'Claude close',
    'Environment stop',
    'Retry close',
  ].map((value, index) => (index === 4 ? new AggregateError([], value) : new Error(value)));
  const invocation = invocation_children();
  const stop = vi
    .fn()
    .mockRejectedValueOnce(errors[1])
    .mockRejectedValueOnce(errors[5])
    .mockResolvedValue(undefined);
  const failure = await probe_codex_model_catalog('/synthetic/codex', {
    child_processes: invocation,
    client_factory: (options) => {
      expect(options.child_processes).toBe(invocation);
      return {
        start: async () => {
          throw errors[0];
        },
        stop,
        request: vi.fn(),
        notify: vi.fn(),
      } as never;
    },
  }).catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(ModelCatalogCleanupFailed);
  expect((failure as ModelCatalogCleanupFailed).primary).toBe(errors[0]);
  expect((failure as ModelCatalogCleanupFailed).cleanup_errors).toContain(errors[1]);
  expect(stop).toHaveBeenCalledOnce();
  const retry = vi.fn(async () => {});
  const claude_failure = new ModelCatalogCleanupFailed(
    errors[2],
    [new AggregateError([errors[3]])],
    retry,
  );
  vi.spyOn(codex_catalog, 'probe_codex_model_catalog').mockRejectedValue(failure);
  vi.spyOn(claude_catalog, 'probe_claude_model_catalog').mockRejectedValue(claude_failure);
  const host = {
    database: fixture(),
    environment: {},
    panels_server_url: 'http://127.0.0.1:1',
    worker_process_root: '/synthetic',
    now: () => new Date(),
    child_processes: invocation,
    one_shot_children: { seal() {}, settled: async () => {} },
    worker_supervision: {},
    test_mode: false,
    executable_on_path: (name: string) => name,
  } as unknown as DriverHost;
  const codex = parse_backend_instance_id('codex'),
    claude = parse_backend_instance_id('claude');
  const registry = driver_registry_from_entries(
    [codex_driver, claude_driver],
    [
      {
        kind: 'live',
        driver: codex_driver,
        instance: await create_codex_instance({ instance_id: codex, config: null }, host),
      },
      {
        kind: 'live',
        driver: claude_driver,
        instance: await create_claude_instance({ instance_id: claude, config: null }, host),
      },
    ],
  );
  environment.executables.set('codex', '/synthetic/codex');
  environment.executables.set('claude', '/synthetic/claude');
  const runtime = create_backend_runtime({
    database: host.database,
    config: { test_mode: true } as never,
    now: host.now,
    registry,
    environment,
  });
  await expect(runtime.backend_snapshots.snapshot(codex, { refresh: true })).rejects.toBe(failure);
  await expect(runtime.backend_snapshots.snapshot(claude, { refresh: true })).rejects.toBe(
    claude_failure,
  );
  const options = { deadline: performance.now() / 1000 + 1 };
  const first = runtime.shutdown!(options);
  const finished = vi.fn();
  void first.then(finished, finished);
  await new Promise((resolve) => setImmediate(resolve));
  expect(finished).not.toHaveBeenCalled();
  sibling.resolve();
  await expect(first).rejects.toBeInstanceOf(AggregateError);
  await expect(runtime.shutdown!(options)).rejects.toBeInstanceOf(AggregateError);
  const actual = await runtime.settled().catch((error: unknown) => error);
  expect((actual as AggregateError).errors).toEqual(expect.arrayContaining(errors));
  expect(stop).toHaveBeenCalledTimes(3);
  expect(retry).toHaveBeenCalledOnce();
  expect(invocation.spawn).not.toHaveBeenCalled();
  expect(environment.shutdown).toHaveBeenCalledOnce();
  expect(provider_environment.shutdown).toHaveBeenCalledOnce();
  await expect(runtime.shutdown!(options)).rejects.toBe(actual);
  // Shut-down catalogs answer without another native read.
  const probe_calls = [
    vi.mocked(codex_catalog.probe_codex_model_catalog).mock.calls.length,
    vi.mocked(claude_catalog.probe_claude_model_catalog).mock.calls.length,
  ];
  const after_shutdown = await Promise.all(
    [codex, claude].map((key) => runtime.backend_snapshots.snapshot(key, { refresh: true })),
  );
  // A stopped catalog source refuses, and the card reports that its catalog did not answer.
  expect(
    after_shutdown.map((card) => ({
      complete: card.models_complete,
      catalog: card.diagnoses.at(-1),
    })),
  ).toEqual([
    {
      complete: false,
      catalog:
        'Codex is installed but did not answer when asked what it can run, so no models are listed. Check that `codex app-server` starts from a terminal.',
    },
    {
      complete: false,
      catalog:
        'Claude is installed but did not answer when asked what it can run, so no models are listed. Check that `claude` starts from a terminal.',
    },
  ]);
  expect([
    vi.mocked(codex_catalog.probe_codex_model_catalog).mock.calls.length,
    vi.mocked(claude_catalog.probe_claude_model_catalog).mock.calls.length,
  ]).toEqual(probe_calls);
});
function invocation_children(): OwnedChildProcesses {
  return {
    seal: vi.fn(),
    spawn: vi.fn(() => {
      throw new Error('No provider spawn in this proof.');
    }),
    retain_worker_unit: vi.fn(() => {
      throw new Error('No provider unit in this proof.');
    }),
    settled: async () => {},
    force_kill: vi.fn(),
  } as OwnedChildProcesses;
}

it('distinguishes an unavailable handshake from failed query cleanup and retains both errors', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const primary = new Error('scripted handshake refusal'),
    cleanup = new Error('scripted query close failure');
  query.mockReturnValueOnce({
    initializationResult: async () => {
      throw primary;
    },
    close: () => {},
  } as never);
  const unavailable = await probe_claude_model_catalog('/synthetic/claude').catch(
    (error: unknown) => error,
  );
  expect(unavailable).toBeInstanceOf(ClaudeModelCatalogUnavailable);
  expect(unavailable).not.toBeInstanceOf(ModelCatalogCleanupFailed);
  const close = vi.fn().mockImplementationOnce(() => {
    throw cleanup;
  });
  query.mockReturnValueOnce({
    initializationResult: async () => {
      throw primary;
    },
    close,
  } as never);
  const child_processes = invocation_children(),
    one_shot_children = new OneShotChildren(child_processes);
  const worker_supervision = create_worker_supervision_runtime({ one_shot_children });
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('claude', '/synthetic/claude');
  environment.outcomes.set(JSON.stringify(['/synthetic/claude', '--version']), {
    exit_code: 0,
    standard_output: '2.1.259',
    standard_error: '',
  });
  const notify = vi.fn();
  const catalog = create_mac_catalog({
    child_processes,
    one_shot_children,
    environment,
    on_failure: notify,
    host: create_mac_adapter_host({
      drivers: await load_executor_drivers(),
      panels_server_url: 'http://127.0.0.1:9',
      child_processes,
      one_shot_children,
      worker_supervision,
      environment: { HOME: '/synthetic', CODEX_HOME: '/synthetic/codex' },
      executable_path: () => '/synthetic/claude',
    }),
  });
  const failed = await catalog
    .read_result('cleanup', { backend_key: 'claude', refresh: true })
    .catch((error: unknown) => error);
  expect(failed).toBeInstanceOf(ServerLifecycleError);
  const retained = (failed as Error).cause;
  expect(retained).toBeInstanceOf(ModelCatalogCleanupFailed);
  expect((retained as ModelCatalogCleanupFailed).primary).toBe(primary);
  expect((retained as ModelCatalogCleanupFailed).cleanup_errors).toContain(cleanup);
  const finish = vi.fn(),
    healthy = vi.fn(),
    queue_failure = vi.fn();
  const queues = new MacCommandQueues({
    runner: { admission_stopped: false },
    catalog,
    journal: { finish, finish_stream: finish, assert_healthy: healthy },
    result_finished: vi.fn(),
    on_failure: queue_failure,
  } as unknown as QueueDependencies);
  queues.submit({
    command_sequence: 1,
    command_id: 'catalog',
    created_at_vps_ms: 1,
    child_command: null,
    model_list_request: { backend_key: 'claude', refresh: true },
  });
  await expect(queues.settled()).rejects.toBeInstanceOf(AggregateError);
  expect(queue_failure).toHaveBeenCalledExactlyOnceWith(failed);
  expect(finish).not.toHaveBeenCalled();
  expect(healthy).not.toHaveBeenCalled();
  expect(query).toHaveBeenCalledTimes(2);
  await expect(queues.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBeInstanceOf(
    AggregateError,
  );
  await expect(catalog.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBeInstanceOf(
    AggregateError,
  );
  await expect(catalog.settled()).rejects.toMatchObject({ errors: [primary, cleanup] });
  expect(close).toHaveBeenCalledTimes(2);
  expect(notify).toHaveBeenCalledExactlyOnceWith(failed);
  expect(child_processes.spawn).not.toHaveBeenCalled();
  one_shot_children.seal();
  await one_shot_children.settled();
  await worker_supervision.close();
});

it('joins Claude direct exit without waiting for a child close event or signaling its reaped group', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const cancellation = new AbortController(),
    reason = new Error('scripted catalog stop');
  const stop_options = { deadline: performance.now() / 1000 + 1 };
  let reject_start!: (error: unknown) => void;
  const signal = vi.spyOn(process, 'kill').mockReturnValue(true);
  const child = Object.assign(new EventEmitter(), {
    pid: 424242,
    exitCode: null as number | null,
    signalCode: null,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  const close_event = vi.fn();
  child.once('close', close_event);
  const children = {
    signal: new AbortController().signal,
    spawn: vi.fn(() => child),
    seal() {},
    settled: async () => {},
  } as unknown as OwnedOneShotChildren;
  query.mockImplementationOnce((options) => {
    options.options!.spawnClaudeCodeProcess!({
      command: '/synthetic/claude',
      args: [],
      cwd: '/synthetic',
      env: {},
    } as never);
    return {
      initializationResult: () =>
        new Promise((_resolve, reject) => {
          reject_start = reject;
          cancellation.abort(reason);
        }),
      close: () => {
        child.exitCode = 0;
        child.emit('exit', 0, null);
        reject_start(reason);
      },
    } as never;
  });
  await expect(
    probe_claude_model_catalog('/synthetic/claude', children, {
      signal: cancellation.signal,
      stop_options: () => stop_options,
    }),
  ).rejects.toMatchObject({ cause: reason });
  expect(close_event).not.toHaveBeenCalled();
  expect(signal).not.toHaveBeenCalled();
  expect(child.stdout.destroyed && child.stderr.destroyed && child.stdin.destroyed).toBe(true);
});
