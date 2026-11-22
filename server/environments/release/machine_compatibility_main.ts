/** Hosted native cross-version proof. No publication, service installation, or account login. */
import assert from 'node:assert/strict';
import { createWriteStream } from 'node:fs';
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { isAbsolute, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { createServer } from 'node:https';
import { request as http_request } from 'node:http';
import { pipeline } from 'node:stream/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { extract } from 'tar';
import { with_zip_member } from '../updates/zip.ts';
import { sha256_file } from '../backup/evidence.ts';
import { is_record } from '../../core/records.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { load_machine_baselines } from './machine_baselines.ts';
import { check_machine_wire } from './machine_wire_compatibility.ts';
import { source_run } from './publication_identity.ts';
import type {
  MachineBaseline,
  MachineCompatibilityProof,
  MachinePlatform,
} from './machine_compatibility_contracts.ts';

assert.equal(
  process.env.GITHUB_ACTIONS,
  'true',
  'Native compatibility proof requires a disposable hosted runner',
);
assert.ok(process.platform === 'linux' || process.platform === 'darwin');
const platform: MachinePlatform = process.platform;
const source = fileURLToPath(new URL('../../../', import.meta.url));
const request = JSON.parse(await readFile(process.argv[2]!, 'utf8'));
assert.ok(is_record(request));
for (const field of ['candidate_zip', 'output_directory', 'cache_directory'])
  assert.ok(typeof request[field] === 'string' && isAbsolute(request[field]));
const root = request.output_directory as string;
assert.ok(
  process.env.TMPDIR && root.startsWith(process.env.TMPDIR + '/'),
  'Proof state must be private temporary state',
);
await mkdir(root, { mode: 0o700 });
const owner = new ChildProcesses();
const lifetime = AbortSignal.timeout(15 * 60_000);
const execution = {
  child_processes: owner,
  signal: lifetime,
  deadline: performance.now() / 1000 + 900,
};
const run = await source_run(process.env.GITHUB_SHA!, source, execution);
const started = performance.now();
const baseline = await load_machine_baselines(
  platform,
  root,
  request.cache_directory as string,
  run.repository,
  process.env.GITHUB_TOKEN!,
  lifetime,
);
const archive_name =
  platform === 'linux' ? 'panels-linux-executor.tar.gz' : 'panels-mac-runtime.tar.gz';
const evidence_name =
  platform === 'linux' ? 'linux-executor-publication.json' : 'mac-runtime-publication.json';
const candidate_archive = join(root, archive_name);
const evidence_path = join(root, evidence_name);
for (const [name, destination] of [
  [archive_name, candidate_archive],
  [evidence_name, evidence_path],
])
  await with_zip_member(request.candidate_zip as string, name!, (stream) =>
    pipeline(stream, createWriteStream(destination!, { flags: 'wx', mode: 0o600 })),
  );
const evidence = JSON.parse(await readFile(evidence_path, 'utf8'));
assert.equal(evidence.application_revision, run.source_sha);
assert.equal(evidence.archive_sha256, await sha256_file(candidate_archive));
assert.equal(evidence.archive_size, (await stat(candidate_archive)).size);
assert.match(evidence.build_id, /^[0-9a-f]{64}$/);
assert.equal(evidence.repeat_build_id, evidence.build_id, 'Independent machine builds differ');
const candidate = join(root, 'candidate');
await mkdir(candidate);
await extract({ file: candidate_archive, cwd: candidate, strict: true });
const candidate_schema = JSON.parse(
  await readFile(join(candidate, 'server/executor/wire.schema.json'), 'utf8'),
);
const oldest = join(root, 'oldest-runtime'),
  previous = join(root, 'previous-runtime');
for (const [path, value] of [
  [oldest, baseline.oldest],
  [previous, baseline.previous],
] as const) {
  await mkdir(path);
  await extract({ file: value.archive_path, cwd: path, strict: true });
  const marker = JSON.parse(await readFile(join(path, 'mac-runtime.json'), 'utf8'));
  assert.equal(marker.application_revision, value.source_sha);
  assert.equal(marker.platform, platform);
  check_machine_wire(
    JSON.parse(await readFile(join(path, 'server/executor/wire.schema.json'), 'utf8')),
    candidate_schema,
    1,
  );
}
const channel_configuration = await readFile(
  join(source, 'server/executor/configuration.ts'),
  'utf8',
);
assert.match(channel_configuration, /MAC_UPDATE_SCHEMA_VERSION\s*=\s*1\s*;/);
const server_channel_configuration = await readFile(
  join(source, 'server/environments/updates/mac/configuration.ts'),
  'utf8',
);
assert.match(server_channel_configuration, /MAC_UPDATE_SCHEMA_VERSION\s*=\s*1\s*;/);
const tls = join(root, 'tls');
await mkdir(tls);
const openssl = owner.spawn(
  'openssl',
  [
    'req',
    '-x509',
    '-newkey',
    'rsa:2048',
    '-nodes',
    '-keyout',
    join(tls, 'key.pem'),
    '-out',
    join(tls, 'cert.pem'),
    '-days',
    '1',
    '-subj',
    '/CN=localhost',
    '-addext',
    'subjectAltName=IP:127.0.0.1,DNS:localhost',
  ],
  { stdio: 'ignore' },
);
await once(openssl, 'close');
assert.equal(openssl.exitCode, 0);
let target = '';
const proxy = createServer(
  { key: await readFile(join(tls, 'key.pem')), cert: await readFile(join(tls, 'cert.pem')) },
  (incoming, outgoing) => {
    if (!target) {
      outgoing.writeHead(503).end();
      return;
    }
    const peer = http_request(
      new URL(incoming.url!, target),
      {
        method: incoming.method,
        headers: { ...incoming.headers, 'tailscale-user-login': 'compatibility@example.invalid' },
      },
      (response) => {
        outgoing.writeHead(response.statusCode!, response.headers);
        response.pipe(outgoing);
      },
    );
    peer.on('error', (error) => outgoing.destroy(error));
    incoming.on('error', (error) => peer.destroy(error));
    outgoing.on('close', () => peer.destroy());
    incoming.pipe(peer);
  },
);
let service:
  | ReturnType<
      typeof import('../updates/mac/composition.ts').create_mac_update_composition
    >['service']
  | undefined;
let server:
  | Awaited<
      ReturnType<typeof import('../../executor/mac/testing/native_server.ts').start_native_server>
    >
  | undefined;
let active: ReturnType<ChildProcesses['spawn']> | undefined;
let active_closed: Promise<unknown> | undefined;
const wait = async (read: () => unknown | Promise<unknown>) => {
  for (;;) {
    lifetime.throwIfAborted();
    if (active && (active.exitCode !== null || active.signalCode !== null))
      throw new Error('Historical machine exited before the proof completed. Read its stderr log.');
    if (await read()) return;
    await delay(100, undefined, { signal: lifetime });
  }
};
const stop_machine = async () => {
  if (!active) return;
  if (active.exitCode === null && active.signalCode === null) active.kill('SIGTERM');
  await observe_shutdown(
    active_closed!,
    { deadline: performance.now() / 1000 + 45 },
    'Native machine did not stop',
  );
  active = undefined;
};
try {
  proxy.listen(0, '127.0.0.1');
  await once(proxy, 'listening');
  const address = proxy.address();
  assert.ok(address && typeof address !== 'string');
  const origin = `https://127.0.0.1:${address.port}`;
  const home = join(root, 'home');
  for (const name of [
    'home',
    'server/data',
    'server/logs',
    'server/workspace',
    'server/runtime',
    'server/backups',
    'server/files',
  ])
    await mkdir(join(root, name), { recursive: true });
  await writeFile(join(root, 'server/config.json'), '{}');
  Object.assign(process.env, {
    PLAN_CONFIG_PATH: join(root, 'server/config.json'),
    PLAN_DB_PATH: join(root, 'server/data/planner.sqlite'),
    PLAN_DATA_DIR: join(root, 'server/data'),
    PLAN_APP_ROOT: source,
    PLAN_LOGS_DIR: join(root, 'server/logs'),
    PLAN_WORKSPACE_ROOT: join(root, 'server/workspace'),
    PLAN_BACKUP_DIR: join(root, 'server/backups'),
    PLAN_TICKET_FILES_DIR: join(root, 'server/files'),
    PLAN_TICKET_SCRATCH_ROOT: join(root, 'server/scratch'),
    PLAN_TICKET_SCRATCH_DATABASE: join(root, 'server/data/planner.sqlite'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'server/data/key'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'server/runtime/dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'server/runtime/control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'server/runtime/lifecycle.lock'),
    PLAN_TEST_MODE: '1',
    PLAN_PORT: '0',
    PLAN_HOST: '127.0.0.1',
    PLAN_SERVER_URL: origin,
    PLAN_APP_SHA: run.source_sha,
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'compatibility@example.invalid',
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: origin,
  });
  const { createEnvironmentRuntime } = await import('../runtime.ts');
  const { create_test_environment_operations } = await import('../testing/composition.ts');
  const { create_mac_update_composition } = await import('../updates/mac/composition.ts');
  const { SUPPORTED_UPDATE_SCHEMA_FLOOR } = await import('../updates/configuration.ts');
  const { start_native_server } = await import('../../executor/mac/testing/native_server.ts');
  const { write_credential, credential_path } = await import('../../executor/credential_file.ts');
  const { attempt_path, FileUpdateAttemptStore } = await import('../updates/attempts.ts');
  const publication = {
    publication_run_id: Number(run.run_id),
    artifact_id: 1,
    proof_artifact_id: 1,
    manifest: {
      app_sha: run.source_sha,
      source_digest: '0'.repeat(64),
      artifact_digest: '0'.repeat(64),
      runtime_contract: null,
    },
    archive_sha256: '0'.repeat(64),
    schema_floor: SUPPORTED_UPDATE_SCHEMA_FLOOR,
    expires_at: null,
    mac: {
      artifact_id: 1,
      archive_sha256: evidence.archive_sha256,
      archive_size: evidence.archive_size,
    },
    linux_executor: {
      artifact_id: 1,
      archive_sha256: evidence.archive_sha256,
      archive_size: evidence.archive_size,
    },
    parts: {
      server: { app_sha: run.source_sha, artifact_digest: '0'.repeat(64) },
      screens: { sha256: '0'.repeat(64) },
      machines: {
        [platform === 'linux' ? 'linux-x86_64' : 'darwin-arm64']: {
          archive_sha256: evidence.archive_sha256,
          build_id: evidence.build_id,
        },
      },
    },
  };
  server = await start_native_server(undefined, {
    platform,
    expected_protocol: JSON.parse(
      await readFile(join(previous, 'server/executor/wire.schema.json'), 'utf8'),
    ).manifest.protocol_version,
    create_environments: (context) =>
      createEnvironmentRuntime(context, {
        ...create_test_environment_operations(context),
        create_mac: (composition) => {
          const result = create_mac_update_composition(
            {
              ...composition,
              platform,
              linux_registered: () => platform === 'linux',
              current_app_sha: run.source_sha,
              ready_release: () => null,
              provider: {
                available: async () => publication,
                download_artifact: async (_id, path) => {
                  await cp(request.candidate_zip as string, path);
                },
              },
            },
            null,
          );
          service = result.service;
          return result;
        },
      }),
  });
  target = server.address;
  const start_machine = async (runtime: string, revision: string, label: string) => {
    const prior_incarnation = server!.endpoint.presence.current_identity()?.executor_incarnation;
    const state = join(root, label + '-state');
    await mkdir(state, { recursive: true });
    await write_credential(credential_path(state), server!.credential);
    const bin = join(home, '.local/bin');
    await mkdir(bin, { recursive: true });
    const node = platform === 'darwin' ? join(runtime, 'node/bin/node') : process.execPath;
    for (const [name, script, extra] of [
      [
        'claude',
        'claude/testing/scripted_cli.ts',
        {
          UNIT18_TRACE: join(root, label + '-claude.jsonl'),
          UNIT18_WRAPPED_FILE: join(root, label + '-claude-launch.json'),
        },
      ],
      [
        'codex',
        'codex/testing/scripted_app_server.ts',
        { UNIT17_TRACE: join(root, label + '-codex.jsonl'), UNIT17_CORE_PROOF: '1' },
      ],
    ] as const)
      await writeFile(
        join(bin, name),
        `#!${node}\nObject.assign(process.env, ${JSON.stringify(extra)});\nawait import(${JSON.stringify(join(source, 'server/drivers', script))});\n`,
        { mode: 0o755 },
      );
    await writeFile(join(root, label + '-claude-launch.json'), '{}');
    const environment = {
      PATH: `${bin}:${dirname(node)}:${process.env.PATH}`,
      HOME: home,
      TMPDIR: process.env.TMPDIR,
      LANG: 'en_US.UTF-8',
      PLAN_EXECUTOR_ROOT: state,
      PLAN_CLAUDE_EXECUTABLE: join(bin, 'claude'),
      PLAN_SERVER_URL: origin,
      PLAN_TEST_MODE: '0',
      NODE_EXTRA_CA_CERTS: join(tls, 'cert.pem'),
      CODEX_HOME: join(home, '.codex'),
      CLAUDE_CONFIG_DIR: join(home, '.claude'),
    };
    for (const path of [environment.CODEX_HOME, environment.CLAUDE_CONFIG_DIR])
      await mkdir(join(path, 'skills'), { recursive: true });
    active = owner.spawn(
      node,
      [
        join(runtime, 'server/executor/mac/entrypoint.ts'),
        'executor',
        'run',
        '--application-revision',
        revision,
        '--executor-id',
        'test-executor',
        '--panels-server-url',
        origin,
      ],
      { env: environment, stdio: ['ignore', 'pipe', 'pipe'] },
    );
    active.stdout!.pipe(createWriteStream(join(root, label + '-stdout.log')));
    active.stderr!.pipe(createWriteStream(join(root, label + '-stderr.log')));
    active_closed = once(active, 'close');
    void active_closed.catch(() => {});
    await wait(
      () =>
        server!.endpoint.presence.current_identity()?.application_revision === revision &&
        server!.endpoint.presence.current_identity()?.executor_incarnation !== prior_incarnation &&
        server!.endpoint.presence.is_present(),
    );
    return state;
  };
  await start_machine(previous, baseline.previous.source_sha, 'previous');
  const turn = await server.create_turn('codex');
  await server.turn_result(turn.conversation_id);
  await server.stop_turn(turn.conversation_id);
  await stop_machine();
  const state = await start_machine(oldest, baseline.oldest.source_sha, 'oldest');
  await service!.prepare(publication);
  const attempts = new FileUpdateAttemptStore(attempt_path(join(root, 'server/data')), () => {});
  const now = new Date().toISOString();
  attempts.create({
    attempt_id: 'compatibility-candidate',
    from_sha: baseline.oldest.source_sha,
    to_sha: run.source_sha,
    mode: 'now',
    confirm_activity: true,
    state: 'done',
    created_at: now,
    updated_at: now,
    release: publication,
    detail: null,
    deadline_at: null,
    backup: null,
    snapshot_required: false,
    steps: {},
    mac_attempt_id: 'compatibility-machine',
    extra: {},
  });
  await wait(async () => {
    const identity = server!.endpoint.presence.current_identity();
    if (identity?.application_revision !== run.source_sha) return false;
    try {
      const selection = JSON.parse(await readFile(join(state, 'updates/selection.json'), 'utf8'));
      return (
        selection.application_revision === run.source_sha && service!.status()?.phase === 'current'
      );
    } catch (error) {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return false;
      throw error;
    }
  });
  const without_path = ({ archive_path: _path, ...value }: MachineBaseline) => value;
  const proof: MachineCompatibilityProof = {
    version: 1,
    platform,
    source_run: run,
    candidate: {
      archive_sha256: evidence.archive_sha256,
      build_id: evidence.build_id,
      repeat_build_id: evidence.repeat_build_id,
    },
    oldest: without_path(baseline.oldest),
    previous: without_path(baseline.previous),
    checks: {
      previous_lease_run_result: 'passed',
      oldest_channel_update: 'passed',
      wire_compatibility: 'passed',
      channel_version: 1,
    },
    seconds: (performance.now() - started) / 1000,
  };
  await stop_machine();
  await server.stop();
  server = undefined;
  await writeFile(
    join(root, `machine-compatibility-${platform}.json`),
    JSON.stringify(proof, null, 2) + '\n',
  );
} catch (error) {
  if (server)
    await writeFile(
      join(root, 'server-failure.json'),
      JSON.stringify(server.diagnostics(), null, 2),
    );
  await writeFile(join(root, 'failure.txt'), String((error as Error).stack ?? error));
  throw error;
} finally {
  await stop_machine();
  if (server) await server.stop();
  owner.seal();
  owner.force_kill(performance.now() / 1000 + 30);
  await owner.settled();
  proxy.closeAllConnections();
  if (proxy.listening)
    await new Promise<void>((resolve, reject) =>
      proxy.close((error) => (error ? reject(error) : resolve())),
    );
}
