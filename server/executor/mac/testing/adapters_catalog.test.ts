import { afterEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { load_claude_sdk } from '../../../conversation/backends/claude_sdk.ts';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import {
  ClaudeModelCatalogUnavailable,
  probe_claude_model_catalog,
} from '../../../conversation/backends/claude_model_catalog.ts';
import { ModelCatalogCleanupFailed } from '../../../conversation/backends/catalog_cleanup.ts';
import { probe_codex_model_catalog } from '../../../conversation/backends/codex_app_server/model_catalog.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { RecordingBackendProbeEnvironment } from '../../../conversation/snapshot/recording_environment.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import { create_mac_adapter_host } from '../adapters.ts';
import { MacCommandQueues } from '../mailbox/queues.ts';
import { MacCatalog } from '../catalog/index.ts';
import { gate } from './runner_fixture.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { QueueDependencies } from '../mailbox/contracts.ts';
import * as probe_environment from '../../../conversation/snapshot/environment.ts';
import * as codex_catalog from '../../../conversation/backends/codex_app_server/model_catalog.ts';
import * as claude_catalog from '../../../conversation/backends/claude_model_catalog.ts';
import { production_backend_providers } from '../../../conversation/snapshot/production.ts';
import { create_backend_runtime } from '../../../conversation/snapshot/composition.ts';
import type { CodexProviderContext } from '../../../conversation/backends/codex_app_server/providers.ts';

const { query } = vi.hoisted(() => ({
  query: vi.fn<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>(),
}));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query }));
vi.mock('../../../conversation/backends/cli_compatibility.ts', () => ({
  ensure_cli_compatible: vi.fn(),
}));
await load_claude_sdk();
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
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
  const context = {
    database: {},
    now: () => new Date(),
    child_processes: invocation,
    one_shot_children: { seal() {}, settled: async () => {} },
    read_codex_snapshot: async () => ({ executable_path: '/synthetic/codex' }),
  } as unknown as CodexProviderContext;
  const providers = production_backend_providers(false, context);
  const runtime = create_backend_runtime({
    ...context,
    config: { test_mode: true } as never,
    providers,
    environment,
  });
  await expect(providers.codex!('/synthetic/codex')).rejects.toBe(failure);
  await expect(providers.claude!('/synthetic/claude')).rejects.toBe(claude_failure);
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
  await expect(providers.codex!('/synthetic/codex')).rejects.toThrow('shut down');
  await expect(providers.claude!('/synthetic/claude')).rejects.toThrow('shut down');
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
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('claude', '/synthetic/claude');
  environment.outcomes.set(JSON.stringify(['/synthetic/claude', '--version']), {
    exit_code: 0,
    standard_output: '2.1.219',
    standard_error: '',
  });
  const notify = vi.fn();
  const catalog = create_mac_catalog({
    child_processes,
    one_shot_children,
    environment,
    on_failure: notify,
    host: create_mac_adapter_host({
      panels_server_url: 'http://127.0.0.1:9',
      child_processes,
      one_shot_children,
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
