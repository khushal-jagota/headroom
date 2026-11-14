/** Composition uses scripted native children; existing adapter suites own vendor protocols. */
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { create_mac_adapter_host } from '../adapters.ts';
import { MacNativeChildren } from '../runner/start_stop.ts';
import { MacChildAdmissionRefused } from '../runner/contracts.ts';
import { MacCatalog } from '../catalog/index.ts';
import { ExecutorPaths, FILE_CHUNK_BYTES } from '../configuration.ts';
import { FilesService } from '../files.ts';
import { decode_json } from '../../protocol.ts';
import type { BackendChild } from '../../../conversation/backends/contracts.ts';
import type { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { ChildReference, StartRun } from '../../wire.generated.ts';
import type { BackendSnapshot } from '../../../conversation/snapshot/contracts.ts';
import { gate, native_fixture_options } from './runner_fixture.ts';

const started = performance.now(),
  root = process.argv[2]!;
const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as { starts: unknown[] };
function normalize(value: unknown, capability?: string): unknown {
  if (typeof value === 'string')
    return value === capability ? '<capability>' : value.replaceAll(root, '<root>');
  if (Array.isArray(value)) return value.map((item) => normalize(item, capability));
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, normalize(item, capability)]),
    );
  return value;
}
const unexpected = (): never => {
  throw new Error('This composition proof must not spawn a provider.');
};
const children: OwnedChildProcesses = {
  seal() {},
  spawn: unexpected,
  retain_worker_unit: unexpected,
  settled: async () => {},
  force_kill: unexpected,
};
const one_shots: OwnedOneShotChildren = {
  spawn: unexpected,
  signal: new AbortController().signal,
  seal() {},
  settled: async () => {},
};
const environment = {
  HOME: join(root, 'home'),
  CODEX_HOME: join(root, 'native/codex'),
  CLAUDE_CONFIG_DIR: join(root, 'native/claude'),
  PLAN_HERMES_HOME: join(root, 'native/hermes'),
  PLAN_HERMES_PYTHON: join(root, 'hermes/venv/bin/python'),
  PLAN_APP_ROOT: join(root, 'application'),
};
const launches = [];
for (const [override, installed] of [
  [
    environment.PLAN_APP_ROOT +
      '/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
    true,
  ],
  [root + '/owner/../alternate-claude', true],
  [undefined, false],
] as const) {
  const host = create_mac_adapter_host({
    panels_server_url: 'http://127.0.0.1:9',
    child_processes: children,
    one_shot_children: one_shots,
    environment: { ...environment, PLAN_CLAUDE_EXECUTABLE: override },
    executable_path: (name) => (installed ? join(root, 'bin', name) : null),
  });
  launches.push({
    launches: host.launches,
    homes: Object.fromEntries(
      (['hermes', 'codex', 'claude'] as const).map((key) => [key, host.native_skill_home(key)]),
    ),
  });
}
mkdirSync(join(root, 'home/project'), { recursive: true });
mkdirSync(join(root, 'absolute'));
writeFileSync(join(root, 'not-directory'), 'regular file');
const paths = new ExecutorPaths(join(root, 'state'));
const calls: unknown[] = [],
  errors: string[] = [],
  faults: Error[] = [];
const files = new FilesService();
files.on_failure((error) => faults.push(error));
const message_files = {} as ConversationMessageFiles;
let skill_stopped = false;
const options = {
  paths,
  workspace_home: join(root, 'home'),
  files,
  message_files,
  native_skill_home: (backend: string) => join(root, 'native', backend),
  event_sink: (_reference: ChildReference) => ({ report: async () => {} }),
  skills: {
    async install_native_skill_snapshot(
      snapshot: StartRun['canonical_skill_snapshot'],
      values: { root: string; native_home: string },
    ) {
      if (skill_stopped) throw new Error('Publication after stop.');
      calls.push(['skills', values.native_home, snapshot]);
      const generation = join(paths.skills, 'fixture-generation');
      mkdirSync(join(generation, 'skills'), { recursive: true });
      return generation;
    },
    async stop() {
      skill_stopped = true;
    },
    settled: async () => {},
  },
  factory(resolved: ResolvedConversationStart) {
    return (values: {
      resolved_start: ResolvedConversationStart;
      message_files: ConversationMessageFiles;
    }) => {
      if (values.resolved_start !== resolved || values.message_files !== message_files)
        throw new Error('Native factory lost its exact start/files dependencies.');
      const capability = new Map(resolved.role_materials?.identity_environment_variables ?? []).get(
        'PLAN_EXECUTOR_CAPABILITY',
      );
      calls.push(['factory', resolved.backend_key, normalize(resolved, capability)]);
      return {
        stop_outside_lock: false,
        async start(
          start: ResolvedConversationStart,
          values: { vendor_session_cursor: string | null },
        ) {
          calls.push([
            'start',
            resolved.backend_key,
            normalize(start, capability),
            values.vendor_session_cursor,
          ]);
          if (resolved.model === 'start-fail') throw new Error('scripted native start failed');
        },
        async stop() {
          calls.push(['stop', resolved.backend_key]);
        },
      } as BackendChild;
    };
  },
  async record_children() {
    calls.push(['record_children']);
  },
  on_failure: (error: Error) => faults.push(error),
};
const native = new MacNativeChildren(options);
let non_directory_workspace: unknown;
const shared_stop = { deadline: performance.now() / 1000 + 15 };
const deadline = () => shared_stop;
async function refusal(action: () => unknown): Promise<string> {
  try {
    await action();
  } catch (error) {
    if (error instanceof Error) return error.message;
    throw error;
  }
  throw new Error('Expected a native admission refusal.');
}
try {
  for (const [index, raw] of input.starts.entries()) {
    const start = decode_json(JSON.stringify(raw), 'StartRun');
    const reference = {
      executor_incarnation: 'inc',
      child_id: `child-${index}`,
      conversation_id: start.resolved_start.conversation_id,
    };
    const child = await native.start(reference, start, () => {});
    const normalized = normalize(calls, child.capability) as unknown[];
    calls.splice(0, calls.length, ...normalized);
    if (native.authenticate(reference.child_id, child.capability) !== child)
      throw new Error('Native child capability did not identify its exact child.');
    errors.push(await refusal(() => native.authenticate(reference.child_id, 'wrong')));
    errors.push(await refusal(() => native.authenticate(null, child.capability)));
    errors.push(await refusal(() => native.start(reference, start, () => {})));
    if (index === 0)
      errors.push(
        await refusal(() =>
          native.start({ ...reference, child_id: 'another-child' }, start, () => {}),
        ),
      );
    native.begin_retirement(reference);
    if (index === 0) {
      const withdrawn = new MacChildAdmissionRefused('scripted withdrawal');
      const before = calls.length;
      const rejected = await native
        .stop_child(reference, () => {
          throw withdrawn;
        })
        .catch((error: unknown) => error);
      if (rejected !== withdrawn || calls.length !== before)
        throw new Error('Withdrawn stop reached native cleanup or changed its refusal.');
    }
    await native.stop_child(reference, () => {});
    native.retire(reference);
    errors.push(await refusal(() => native.authenticate(reference.child_id, child.capability)));
  }
  const failed_start = decode_json(JSON.stringify(input.starts.at(-1)), 'StartRun');
  const failed = {
    ...failed_start,
    resolved_start: { ...failed_start.resolved_start, model: 'start-fail' },
  };
  const reference = {
    executor_incarnation: 'inc',
    child_id: 'failed-child',
    conversation_id: failed.resolved_start.conversation_id,
  };
  errors.push(await refusal(() => native.start(reference, failed, () => {})));
  if (native.get(reference.child_id) !== null)
    throw new Error('Failed native start remained addressable.');
  const missing = {
    ...failed_start,
    resolved_start: {
      ...failed_start.resolved_start,
      workspace_folder: join(root, 'missing/project'),
    },
  };
  const missing_reference = { ...reference, child_id: 'missing-workspace' };
  errors.push(await refusal(() => native.start(missing_reference, missing, () => {})));
  if (native.get(missing_reference.child_id) !== null)
    throw new Error('Unavailable workspace retained a child.');
  const not_directory = {
    ...failed,
    resolved_start: {
      ...failed.resolved_start,
      model: 'hermes-model',
      workspace_folder: root + '/not-directory/../absolute',
    },
  };
  const directory_reference = { ...reference, child_id: 'not-directory-workspace' };
  const before_directory = calls.length;
  const directory_error = await refusal(() =>
    native.start(directory_reference, not_directory, () => {}),
  );
  non_directory_workspace = normalize({
    error: directory_error,
    calls: calls.splice(before_directory),
  });
  if (native.get(directory_reference.child_id) !== null)
    throw new Error('Invalid directory traversal retained a child.');
} finally {
  try {
    await native.stop(deadline());
    await native.settled();
  } finally {
    await files.stop(deadline());
    await files.settled();
  }
}
const parity = normalize({ launches, calls, errors }) as Record<string, unknown>;
// A native start/stop can outlive a caller's deadline. No later factory may start.
const skill_gate = gate(),
  skill_entered = gate();
let actual = false,
  factory_calls = 0,
  invocations = 0;
const blocked = new MacNativeChildren({
  ...options,
  skills: {
    ...options.skills,
    async install_native_skill_snapshot() {
      skill_entered.resolve();
      await skill_gate.promise;
      return join(paths.skills, 'fixture-generation');
    },
    async stop() {},
  },
  factory(start) {
    factory_calls++;
    return options.factory(start);
  },
});
const blocked_start = blocked.start(
  { executor_incarnation: 'inc', child_id: 'blocked', conversation_id: 'conversation-codex' },
  decode_json(JSON.stringify(input.starts[0]), 'StartRun'),
  () => {
    invocations++;
  },
);
void blocked_start.catch(() => {});
await skill_entered.promise;
const joined = blocked.settled().then(() => {
  actual = true;
});
const expired = await blocked
  .stop({ deadline: performance.now() / 1000 - 1 })
  .catch((error: unknown) => error);
const actual_waited = !actual;
skill_gate.resolve();
await blocked_start.catch(() => {});
await joined;
if (invocations !== 0) throw new Error('Stopped skill preparation reached native invocation.');
const lifecycle = {
  deadline_is_lifecycle_error:
    expired instanceof ShutdownDeadlineExceeded && expired instanceof ServerLifecycleError,
  actual_waited,
  actual_join_completed: actual,
  no_factory_after_stop: factory_calls === 0,
  stopped_start_refused:
    (await refusal(() =>
      blocked.start(
        { executor_incarnation: 'inc', child_id: 'later', conversation_id: 'conversation-codex' },
        decode_json(JSON.stringify(input.starts[0]), 'StartRun'),
        () => {},
      ),
    )) === 'executor is quiescing',
  no_faults: faults.length === 0,
};
const publisher_failure = new Error('scripted publisher cleanup failed after actual exit');
const cleanup_faults: Error[] = [];
let failed_native_stops = 0;
const failed_publisher = new MacNativeChildren({
  ...native_fixture_options({
    ...options,
    snapshot_directory: join(paths.skills, 'fixture-generation'),
  }),
  event_sink: options.event_sink,
  factory: () => () =>
    ({
      async start() {
        throw publisher_failure;
      },
      async stop() {
        if (++failed_native_stops === 1) throw publisher_failure;
      },
    }) as unknown as BackendChild,
  skills: {
    ...options.skills,
    install_native_skill_snapshot: async () => join(paths.skills, 'fixture-generation'),
    stop: async () => {
      throw publisher_failure;
    },
    settled: async () => {
      throw publisher_failure;
    },
  },
  on_failure: (error) => cleanup_faults.push(error),
});
const failed_reference = {
  executor_incarnation: 'inc',
  child_id: 'failed-cleanup',
  conversation_id: 'conversation-codex',
};
await failed_publisher
  .start(failed_reference, decode_json(JSON.stringify(input.starts[0]), 'StartRun'), () => {})
  .catch(() => {});
const failed_child = failed_publisher.get(failed_reference.child_id);
const failed_authentication = await refusal(() =>
  failed_publisher.authenticate(failed_reference.child_id, failed_child?.capability ?? null),
);
const retained_failed_child =
  failed_child !== null && failed_child.capability !== '' && failed_native_stops === 1;
const failed_stop = await failed_publisher.stop(deadline()).catch((error: unknown) => error);
const failed_actual = await observe_shutdown(
  failed_publisher.settled(),
  deadline(),
  'Native cleanup actual join stalled',
).catch((error: unknown) => error);
Object.assign(lifecycle, {
  failed_start_capability_retired:
    retained_failed_child && failed_authentication === 'unknown executor child capability',
  publisher_stop_failure_retained:
    failed_stop instanceof ServerLifecycleError &&
    (failed_stop.cause === publisher_failure ||
      (failed_stop.cause instanceof AggregateError &&
        failed_stop.cause.errors.includes(publisher_failure))),
  publisher_cleanup_reached_actual:
    failed_actual instanceof AggregateError && failed_actual.errors.includes(publisher_failure),
  publisher_fault_once: cleanup_faults.length === 1 && cleanup_faults[0] === publisher_failure,
});
const native_entered = gate(),
  native_wait = gate();
let native_stops = 0;
const native_failure = new ShutdownDeadlineExceeded(['Actual native stop failed.']);
const native_faults: Error[] = [];
const starting_native = new MacNativeChildren({
  ...native_fixture_options({
    paths,
    workspace_home: options.workspace_home,
    native_skill_home: options.native_skill_home,
    snapshot_directory: join(paths.skills, 'fixture-generation'),
    on_failure: (error) => native_faults.push(error),
    factory: () => () =>
      ({
        stop_outside_lock: false,
        async start() {
          native_entered.resolve();
          await native_wait.promise;
        },
        async stop() {
          if (++native_stops === 1) throw native_failure;
          native_wait.resolve();
        },
      }) as unknown as BackendChild,
  }),
  message_files: options.message_files,
  event_sink: options.event_sink,
});
const native_start = starting_native.start(
  {
    executor_incarnation: 'inc',
    child_id: 'native-blocked',
    conversation_id: 'conversation-codex',
  },
  decode_json(JSON.stringify(input.starts[0]), 'StartRun'),
  () => {},
);
void native_start.catch(() => {});
await native_entered.promise;
const native_stop_failure = await starting_native.stop(deadline()).catch((error: unknown) => error);
await starting_native.stop(deadline()).catch(() => {});
const native_retry_actual = await observe_shutdown(
  starting_native.settled(),
  deadline(),
  'Native retry actual join stalled',
).catch((error: unknown) => error);
await native_start.catch(() => {});
Object.assign(lifecycle, {
  native_stop_releases_blocked_start: native_stops === 2,
  native_retry_succeeds_with_failure_history:
    native_stop_failure === native_failure &&
    native_retry_actual === undefined &&
    native_faults.length === 1 &&
    native_faults[0] === native_failure,
});
const catalog_calls: unknown[] = [],
  catalogs: unknown[] = [];
const catalog = new MacCatalog({
  snapshots: {
    retained: () => null,
    async snapshot(backend, values = {}) {
      catalog_calls.push([
        backend,
        { refresh: values.refresh, discover_update_advisory: values.discover_update_advisory },
      ]);
      if (backend === 'hermes')
        throw Object.assign(new Error('synthetic optional catalog unavailable'), {
          code: 'ECONNREFUSED',
        });
      const snapshot = {
        backend_key: backend,
        installed: true,
        executable_path: '/synthetic/catalog/' + backend,
        version: '1.2.3',
        identity: null,
        available_models: [
          {
            model_id: backend + '-model',
            display_name: backend === 'codex' ? '🦉'.repeat(270_000) : null,
            detail: null,
            reasoning_effort_options: ['high'],
            selected_variant: null,
            version: backend === 'codex' ? [123456799] : [],
            resolved_model_id: null,
          },
        ],
        reasoning_effort_options: ['high'],
        default_model_id: backend + '-model',
        default_reasoning_effort: 'high',
        update_advisory: null,
        diagnoses: [],
        models_complete: true,
      };
      // The snapshot domain uses number[], while the authoritative wire allows exact integers.
      // Validate that wider fixture at the shared wire door before the domain source seam.
      return decode_json(JSON.stringify(snapshot), 'BackendSnapshot') as unknown as BackendSnapshot;
    },
  },
  stop_sources: async () => {},
  on_failure: (error) => faults.push(error),
});
const catalog_started = performance.now();
try {
  for (const [backend_key, refresh] of [
    ['codex', false],
    ['claude', true],
    ['hermes', true],
  ] as const) {
    const owner = await catalog.read_result('model-' + backend_key, { backend_key, refresh });
    try {
      const fingerprints: [number, string][] = [];
      for (let attempt = 0; attempt < 2; attempt++) {
        const body = await owner.open(),
          hash = createHash('sha256');
        let count = 0;
        try {
          for await (const chunk of body) {
            if (!chunk.byteLength || chunk.byteLength > FILE_CHUNK_BYTES)
              throw new Error('Catalog chunk exceeded its bound.');
            count += chunk.byteLength;
            hash.update(chunk);
          }
        } finally {
          await body.close();
        }
        if (count !== body.byte_count) throw new Error('Catalog body byte count differed.');
        fingerprints.push([count, hash.digest('hex')]);
      }
      if (JSON.stringify(fingerprints[0]) !== JSON.stringify(fingerprints[1]))
        throw new Error('Catalog retry changed immutable bytes.');
      if (backend_key === 'codex' && fingerprints[0]![0] <= 1024 * 1024)
        throw new Error('Catalog precision proof must exceed one MiB.');
      catalogs.push(fingerprints[0]);
    } finally {
      await owner.close();
    }
  }
} finally {
  await catalog.stop(deadline());
  await catalog.settled();
}
if (faults.length) throw new AggregateError(faults, 'Composition proof resource failure.');
process.stdout.write(
  JSON.stringify({
    parity: normalize({
      ...parity,
      catalog_calls,
      catalogs,
    }),
    lifecycle,
    non_directory_workspace,
    catalog_seconds: (performance.now() - catalog_started) / 1000,
    elapsed_seconds: (performance.now() - started) / 1000,
  }),
);
