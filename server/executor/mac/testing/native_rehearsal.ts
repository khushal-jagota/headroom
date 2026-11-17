/** One hosted Darwin installation, real remote transport, scripted provider executables. */
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { createServer } from 'node:https';
import { request as http_request } from 'node:http';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { createWriteStream } from 'node:fs';
import { extract } from 'tar';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { mac_installation_paths } from '../platform.ts';
import { credential_path, write_credential } from '../../credential_file.ts';

const root = resolve(process.env.MAC_REHEARSAL_ROOT!);
const evidence = resolve(process.env.MAC_REHEARSAL_EVIDENCE!);
const source = fileURLToPath(new URL('../../../../', import.meta.url));
const revision = process.env.MAC_REHEARSAL_REVISION!;
assert.equal(process.platform, 'darwin');
assert.equal(process.arch, 'arm64');
assert.match(revision, /^[0-9a-f]{40}$/);
assert.ok(root.startsWith(process.env.TMPDIR!));
const owner = new ChildProcesses();
const lifetime = AbortSignal.timeout(12 * 60_000);
const execution = {
  child_processes: owner,
  signal: lifetime,
  deadline: performance.now() / 1000 + 720,
};
const started = performance.now();
const steps: Record<string, unknown> = {};
let command_number = 0;
const run = async (args: string[], environment = process.env) => {
  const result = await run_release_command(
    args,
    { timeout_seconds: 180, environment, cwd: source, allow_nonzero: true },
    execution,
  );
  await writeFile(join(evidence, `command-${command_number++}.log`), result.stdout + result.stderr);
  assert.equal(result.code, 0, result.stdout + result.stderr);
  return result;
};
await mkdir(evidence, { recursive: true });
const home = join(root, 'executor-home');
// Darwin Unix sockets have a short path limit. Use the supported explicit state root.
const paths = { ...mac_installation_paths(home), state: join(root, 'state') };
const bin = join(root, 'scripted-bin');
await mkdir(bin, { recursive: true });
await mkdir(home, { recursive: true });
let server:
  Awaited<ReturnType<typeof import('./native_server.ts').start_native_server>> | undefined;
let app: ReturnType<ChildProcesses['spawn']> | undefined;
let app_closed: Promise<unknown> | undefined;
let proxy_target = '';
const proxy = createServer(
  {
    key: await readFile(join(root, 'tls/key.pem')),
    cert: await readFile(join(root, 'tls/cert.pem')),
  },
  (incoming, outgoing) => {
    if (!proxy_target) {
      outgoing.writeHead(503).end();
      return;
    }
    const headers = { ...incoming.headers, 'tailscale-user-login': 'rehearsal@example.invalid' };
    const target = http_request(
      new URL(incoming.url!, proxy_target),
      { method: incoming.method, headers },
      (response) => {
        outgoing.writeHead(response.statusCode!, response.headers);
        response.pipe(outgoing);
      },
    );
    target.on('error', (error) => outgoing.destroy(error));
    incoming.on('error', (error) => target.destroy(error));
    outgoing.on('close', () => target.destroy());
    incoming.pipe(target);
  },
);
try {
  await run(['launchctl', 'print', `gui/${process.getuid!()}`]);
  steps.launchd_domain = 'passed';
  proxy.listen(0, '127.0.0.1');
  await once(proxy, 'listening');
  const address = proxy.address();
  assert.ok(address && typeof address !== 'string');
  const origin = `https://127.0.0.1:${address.port}`;
  process.env.PLAN_EXECUTOR_ID = 'test-executor';
  process.env.PLAN_TRUSTED_INGRESS_PROVIDER = 'tailscale';
  process.env.PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN = 'rehearsal@example.invalid';
  process.env.PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN = origin;
  const { start_native_server } = await import('./native_server.ts');
  server = await start_native_server();
  proxy_target = server.address;
  steps.local_server = 'passed';

  await run([
    process.execPath,
    join(source, 'server/executor/mac/packaging/publication_main.ts'),
    '--source',
    process.env.MAC_REHEARSAL_INPUTS!,
    '--node-distribution',
    dirname(dirname(process.execPath)),
    '--node-modules',
    join(process.env.MAC_REHEARSAL_INPUTS!, 'server/node_modules'),
    '--output',
    join(root, 'package'),
    '--revision',
    revision,
    '--home',
    home,
    '--executor-id',
    'test-executor',
    '--server-url',
    origin,
    '--backend-path',
    `${bin}:/usr/bin:/bin:/usr/sbin:/sbin`,
    '--run-id',
    process.env.MAC_REHEARSAL_RUN_ID!,
    '--run-attempt',
    process.env.MAC_REHEARSAL_RUN_ATTEMPT!,
  ]);
  const build = JSON.parse(await readFile(join(root, 'package/build.json'), 'utf8'));
  await cp(join(root, 'package/proof-work'), join(evidence, 'package-proof'), { recursive: true });
  await cp(
    join(root, 'package/mac-runtime-publication.json'),
    join(evidence, 'mac-runtime-publication.json'),
  );
  await cp(join(root, 'package/build.json'), join(evidence, 'build.json'));
  await mkdir(paths.supervisor, { recursive: true });
  await extract({
    cwd: paths.supervisor,
    file: join(root, 'package/panels-mac-runtime.tar.gz'),
    strict: true,
  });
  await mkdir(dirname(paths.console), { recursive: true });
  await cp(join(root, 'package/assets/bin/panels'), paths.console);
  await cp(join(root, 'package/assets/Panels TypeScript.app'), paths.app, { recursive: true });
  await mkdir(dirname(paths.app_executable), { recursive: true });
  await run([
    'clang',
    '-arch',
    'arm64',
    '-O2',
    join(root, 'package/assets/launcher.c'),
    '-o',
    paths.app_executable,
  ]);
  await run(['codesign', '--force', '--sign', '-', paths.app]);
  await run(['codesign', '--verify', '--strict', '--verbose=2', paths.app]);
  steps.package = {
    revision,
    archive_sha256: build.archive_sha256,
    node_version: build.node_version,
    signing: 'ad-hoc',
  };

  await writeFile(join(evidence, 'claude-launch.json'), '{}');
  for (const [name, script, extra] of [
    [
      'claude',
      'claude/scripted_cli.ts',
      {
        UNIT18_TRACE: join(evidence, 'claude.jsonl'),
        UNIT18_WRAPPED_FILE: join(evidence, 'claude-launch.json'),
      },
    ],
    [
      'codex',
      'codex/scripted_app_server.ts',
      { UNIT17_TRACE: join(evidence, 'codex.jsonl'), UNIT17_CORE_PROOF: '1' },
    ],
  ] as const) {
    const text = `#!${paths.node}\nObject.assign(process.env, ${JSON.stringify(extra)});\nawait import(${JSON.stringify(new URL(`../../../conversation/testing/${script}`, import.meta.url).href)});\n`;
    await writeFile(join(bin, name), text, { mode: 0o755 });
  }
  const environment: NodeJS.ProcessEnv = {
    PATH: `${join(paths.supervisor, 'node/bin')}:${bin}:/usr/bin:/bin:/usr/sbin:/sbin`,
    HOME: home,
    TMPDIR: process.env.TMPDIR,
    LANG: 'en_US.UTF-8',
    PLAN_TEST_MODE: '0',
    PLAN_EXECUTOR_ROOT: paths.state,
    PLAN_CLAUDE_EXECUTABLE: join(bin, 'claude'),
    PLAN_SERVER_URL: origin,
    NODE_EXTRA_CA_CERTS: join(root, 'tls/cert.pem'),
    CODEX_HOME: join(home, '.codex'),
    CLAUDE_CONFIG_DIR: join(home, '.claude'),
  };
  for (const path of [environment.CODEX_HOME!, environment.CLAUDE_CONFIG_DIR!])
    await mkdir(join(path, 'skills'), { recursive: true });
  await mkdir(paths.state, { recursive: true, mode: 0o700 });
  // Only this isolated server grants this credential. Nothing reads account credentials.
  await write_credential(credential_path(paths.state), server.credential);
  app = owner.spawn(
    paths.app_executable,
    [
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
  app.stdout!.pipe(createWriteStream(join(evidence, 'executor.stdout.log')));
  app.stderr!.pipe(createWriteStream(join(evidence, 'executor.stderr.log')));
  app_closed = once(app, 'close');
  void app_closed.catch(() => {});
  const command = async (args: string[]) => run([paths.console, ...args], environment);
  await server.wait_for_presence();
  steps.protocol_26_presence = 'passed';
  for (const backend of ['claude', 'codex'] as const) {
    const turn = await server.create_turn(backend);
    await server.turn_result(turn.conversation_id);
    if (backend === 'claude')
      await server.active_stop(turn.conversation_id, join(evidence, 'claude.jsonl'));
    else await server.stop_turn(turn.conversation_id);
    steps[`${backend}_turn_and_stop`] = 'passed';
  }
  await server.files({
    root: paths.state,
    command,
    runtime: paths.supervisor,
    environment,
    claude_trace: join(evidence, 'claude.jsonl'),
  });
  steps.upload_and_download = 'passed';
  const quiesce = await command(['executor', 'quiesce', '--exit-when-ready']);
  const status = JSON.parse(quiesce.stdout);
  assert.equal(status.exit_committed, true);
  await observe_shutdown(app_closed, execution, 'Quiescent executor did not exit');
  assert.equal(app.exitCode, 0);
  app = undefined;
  steps.executor_exit = 'passed';
  steps.passed = true;
} catch (error) {
  await writeFile(join(evidence, 'failure.txt'), String((error as Error).stack ?? error));
  throw error;
} finally {
  const cleanup_errors: unknown[] = [];
  const stop = { deadline: performance.now() / 1000 + 30 };
  if (app && app.exitCode === null) {
    app.kill('SIGTERM');
    try {
      await observe_shutdown(app_closed!, stop, 'Rehearsal executor did not stop');
    } catch (error) {
      cleanup_errors.push(error);
    }
  }
  owner.seal();
  try {
    owner.force_kill(stop.deadline);
    await owner.settled();
    steps.children_settled = true;
  } catch (error) {
    cleanup_errors.push(error);
  }
  try {
    if (server) await server.stop();
  } catch (error) {
    cleanup_errors.push(error);
  }
  proxy.closeAllConnections();
  try {
    if (proxy.listening)
      await new Promise<void>((yes, no) => proxy.close((error) => (error ? no(error) : yes())));
  } catch (error) {
    cleanup_errors.push(error);
  }
  steps.seconds = (performance.now() - started) / 1000;
  if (cleanup_errors.length) {
    steps.passed = false;
    steps.cleanup_errors = cleanup_errors.map(String);
  }
  await writeFile(join(evidence, 'result.json'), JSON.stringify(steps, null, 2) + '\n');
  if (cleanup_errors.length) throw new AggregateError(cleanup_errors, 'Rehearsal cleanup failed');
}
