/**
 * A clean hosted Apple Silicon machine joins a private TypeScript server with the server's own
 * join command. The command gets a local release, because releases publish only from main.
 * The runner is disposable, so the join writes the real fixed paths of its account.
 */
import assert from 'node:assert/strict';
import { cp, lstat, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:https';
import { request as http_request } from 'node:http';
import { userInfo } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { shell_quote } from '../../../conversation/worker_unit/logic.ts';
import { mac_installation_paths } from '../platform.ts';
import { build_mac_launcher } from '../packaging/launcher_main.ts';

const root = resolve(process.env.MAC_REHEARSAL_ROOT!);
const evidence = resolve(process.env.MAC_REHEARSAL_EVIDENCE!);
const source = fileURLToPath(new URL('../../../../', import.meta.url));
const revision = process.env.MAC_REHEARSAL_REVISION!;
assert.equal(process.platform, 'darwin');
assert.equal(process.arch, 'arm64');
assert.match(revision, /^[0-9a-f]{40}$/);
assert.ok(root.startsWith(process.env.TMPDIR!));
const account = userInfo().homedir;
const paths = mac_installation_paths(account);
const owner = new ChildProcesses();
const execution = {
  child_processes: owner,
  signal: AbortSignal.timeout(15 * 60_000),
  deadline: performance.now() / 1000 + 900,
};
const started = performance.now();
const steps: Record<string, unknown> = {};
let command_number = 0;
const run = async (args: string[], environment = process.env, allow_nonzero = false) => {
  const result = await run_release_command(
    args,
    { timeout_seconds: 300, environment, cwd: source, allow_nonzero: true },
    execution,
  );
  await writeFile(join(evidence, `command-${command_number++}.log`), result.stdout + result.stderr);
  if (!allow_nonzero) assert.equal(result.code, 0, result.stdout + result.stderr);
  return result;
};
await mkdir(evidence, { recursive: true });
let server:
  Awaited<ReturnType<typeof import('./native_server.ts').start_native_server>> | undefined;
let proxy_target = '';
const login = 'rehearsal@example.invalid';
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
    const headers = { ...incoming.headers, 'tailscale-user-login': login };
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
const domain = `gui/${process.getuid!()}`;
/** Run a join command as a person does in a terminal, with the credential at its prompt. */
async function bootstrap(command: string, credential: string, log: string) {
  const child = owner.spawn('/bin/bash', ['-c', command], {
    env: {
      PATH: '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
      HOME: account,
      TMPDIR: process.env.TMPDIR,
      LANG: 'en_US.UTF-8',
      GH_TOKEN: process.env.GH_TOKEN,
    },
    cwd: root,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout!.on('data', (chunk: Buffer) => (output += chunk));
  child.stderr!.on('data', (chunk: Buffer) => (output += chunk));
  const closed = once(child, 'close');
  child.stdin!.end(credential + '\n');
  const [code] = (await closed) as [number | null];
  await writeFile(join(evidence, log), output);
  return { code, output };
}
let joined = false;
try {
  for (const path of [paths.install, paths.app, paths.plist, paths.state])
    await assert.rejects(lstat(path), { code: 'ENOENT' });
  await run(['launchctl', 'print', domain]);
  const launcher_app = join(root, 'launcher/Panels TypeScript.app');
  await mkdir(join(root, 'launcher'));
  await build_mac_launcher(launcher_app, '-');
  await run([
    process.execPath,
    join(source, 'server/executor/mac/packaging/publication_main.ts'),
    '--source',
    process.env.MAC_REHEARSAL_INPUTS!,
    '--node-distribution',
    resolve(process.execPath, '../..'),
    '--node-modules',
    join(process.env.MAC_REHEARSAL_INPUTS!, 'server/node_modules'),
    '--output',
    join(root, 'package'),
    '--revision',
    revision,
    '--launcher-app',
    launcher_app,
    '--run-id',
    process.env.MAC_REHEARSAL_RUN_ID!,
    '--run-attempt',
    process.env.MAC_REHEARSAL_RUN_ATTEMPT!,
  ]);
  steps.package = 'passed';

  proxy.listen(0, '127.0.0.1');
  await once(proxy, 'listening');
  const address = proxy.address();
  assert.ok(address && typeof address !== 'string');
  const origin = `https://127.0.0.1:${address.port}`;
  Object.assign(process.env, {
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_APP_SHA: revision,
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: login,
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: origin,
  });
  const { start_native_server } = await import('./native_server.ts');
  server = await start_native_server();
  proxy_target = server.address;
  steps.local_server = 'passed';

  // The owner asks the server for a join command, as the Settings page does.
  const response = await fetch(new URL('/api/machines/join-command', server.address), {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'tailscale-user-login': login },
    body: JSON.stringify({ name: 'Hosted Apple Silicon machine' }),
  });
  assert.equal(response.status, 200, await response.clone().text());
  const grant = (await response.json()) as {
    machine_id: string;
    home_url: string;
    command: string;
    credential: string;
  };
  assert.equal(grant.home_url, origin);
  await writeFile(join(evidence, 'join-command.txt'), grant.command + '\n');
  steps.join_command = 'passed';

  // Only the launchd domain needs the private certificate. The join itself never calls the server.
  await run(['launchctl', 'setenv', 'NODE_EXTRA_CA_CERTS', join(root, 'tls/cert.pem')]);
  const local_release =
    ' --archive ' +
    shell_quote(join(root, 'package/panels-mac-runtime.tar.gz')) +
    ' --receipt ' +
    shell_quote(join(root, 'package/mac-runtime-publication.json'));
  const first = await bootstrap(grant.command + local_release, grant.credential, 'bootstrap.log');
  assert.equal(first.code, 0, first.output);
  assert.match(first.output, /Machine joined\. The server accepted its credential and lease\./);
  joined = true;
  steps.bootstrap_join = 'passed';

  assert.equal(server.registry.is_present(grant.machine_id), true);
  const platform = server.context.database.read((sql) =>
    sql.get<{ platform: string }>(
      'SELECT platform FROM machine_executor_credentials WHERE machine_id=?',
      [grant.machine_id],
    ),
  );
  assert.equal(platform?.platform, 'darwin');
  steps.server_presence = 'passed';
  const job = await run(['launchctl', 'print', `${domain}/${paths.job}`]);
  assert.match(job.stdout, /state = running/);
  const signature = await run(['codesign', '-dv', '--verbose=2', paths.app]);
  assert.match(signature.stderr, /Identifier=com\.panels\.executor\.typescript\.app/);
  steps.launch_agent = 'passed';
  // The same command again must refuse before it changes the installation.
  const second = await bootstrap(grant.command + local_release, grant.credential, 'again.log');
  assert.notEqual(second.code, 0, second.output);
  assert.match(second.output, /This machine already has Panels machine code/);
  await run(['codesign', '--verify', '--strict', paths.app]);
  steps.second_join_refused = 'passed';

  const quiesce = await run([paths.console, 'executor', 'quiesce', '--exit-when-ready']);
  assert.equal(JSON.parse(quiesce.stdout).exit_committed, true);
  steps.quiesce = 'passed';
  steps.passed = true;
} catch (error) {
  await writeFile(join(evidence, 'failure.txt'), String((error as Error).stack ?? error));
  throw error;
} finally {
  const cleanup_errors: unknown[] = [];
  const attempt = async (step: () => Promise<unknown>) => {
    try {
      await step();
    } catch (error) {
      cleanup_errors.push(error);
    }
  };
  if (joined)
    await attempt(() => run(['launchctl', 'bootout', `${domain}/${paths.job}`], process.env, true));
  await attempt(() => run(['launchctl', 'unsetenv', 'NODE_EXTRA_CA_CERTS'], process.env, true));
  await attempt(() =>
    cp(join(paths.state, 'logs'), join(evidence, 'launch-logs'), { recursive: true }),
  );
  owner.seal();
  await attempt(async () => {
    owner.force_kill(performance.now() / 1000 + 30);
    await owner.settled();
  });
  if (server) await attempt(() => server!.stop());
  proxy.closeAllConnections();
  if (proxy.listening)
    await attempt(
      () => new Promise<void>((yes, no) => proxy.close((error) => (error ? no(error) : yes()))),
    );
  steps.seconds = (performance.now() - started) / 1000;
  if (cleanup_errors.length) {
    steps.passed = false;
    steps.cleanup_errors = cleanup_errors.map(String);
  }
  await writeFile(join(evidence, 'result.json'), JSON.stringify(steps, null, 2) + '\n');
  if (cleanup_errors.length) throw new AggregateError(cleanup_errors, 'Rehearsal cleanup failed');
}
