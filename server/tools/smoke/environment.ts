/** Private production configuration with the scripted peer of the default backend. */
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdir, realpath, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { isAbsolute, join, relative, sep } from 'node:path';
import { DEFAULT_BACKEND_INSTANCE_ID } from '../../drivers/built_in.ts';
import { BUILT_IN_MANIFESTS, built_in_driver_of } from '../../drivers/built_in_manifests.ts';
import { write_default_backend_peer } from './codex_peer.ts';

const DRIVER_HOMES = BUILT_IN_MANIFESTS.flatMap(({ manifest }) =>
  manifest.home ? [manifest.home] : [],
);
/** Every variable that carries a driver home, each with the home's directory name. */
export const HOME_VARIABLES = DRIVER_HOMES.flatMap((home) =>
  [home.environment_name, ...(home.extra_environment_names ?? [])].map(
    (name) => [name, home.isolated_directory_name] as const,
  ),
);

/** A loopback base on a port the kernel just handed out and released. */
export async function reserve_loopback_base(): Promise<string> {
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const address = reservation.address();
  assert(address && typeof address !== 'string');
  await new Promise<void>((resolve, reject) =>
    reservation.close((error) => (error ? reject(error) : resolve())),
  );
  return `http://127.0.0.1:${address.port}`;
}

/** The caller admits the populated seed's readonly quiescence before server startup. */
export async function create_environment(
  root: string,
  source: string,
  app_root: string,
  base: string,
  sha: string,
  homes: Readonly<Record<string, string>>,
): Promise<Record<string, string>> {
  root = await realpath(root);
  source = await realpath(source);
  app_root = await realpath(app_root);
  assert.match(sha, /^[a-f0-9]{40}$/);
  const url = new URL(base);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, '127.0.0.1');
  assert.ok(url.port && url.port !== '0');
  assert.notEqual(url.port, '8767', 'Smoke refuses the live server port before connection');
  assert.equal(url.pathname, '/');
  assert.equal(url.username + url.password + url.search + url.hash, '');
  const environment: Record<string, string> = {};
  for (const name of ['HOME', ...HOME_VARIABLES.map(([name]) => name)]) {
    const value = homes[name];
    assert.ok(typeof value === 'string');
    const home = await realpath(value),
      suffix = relative(root, home);
    assert.ok(suffix && suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix));
    environment[name] = home;
  }
  for (const name of [
    'bin',
    'run',
    'tmp',
    'logs',
    'backups',
    'workspace',
    'config',
    'cache',
    'state',
  ])
    await mkdir(join(root, name), { recursive: true, mode: 0o700 });
  await writeFile(join(root, 'config.yaml'), '{}\n', {
    flag: 'wx',
    mode: 0o600,
  });
  // The Linux runtime supervisor rebuilds PATH around the account’s .local/bin.
  const peer_bin = join(environment.HOME!, '.local/bin');
  await mkdir(peer_bin, { recursive: true, mode: 0o700 });
  await write_default_backend_peer(root, source, peer_bin);
  // Every other driver's executable is absent, so only the peer answers.
  const peer_driver = built_in_driver_of(DEFAULT_BACKEND_INSTANCE_ID);
  const absent_executables = BUILT_IN_MANIFESTS.filter((driver) => driver !== peer_driver).flatMap(
    ({ manifest: { executable } }) =>
      executable.override_environment_name === undefined
        ? []
        : [
            [
              executable.override_environment_name,
              join(root, `bin/absent-${executable.name}`),
            ] as const,
          ],
  );
  return {
    ...environment,
    PATH: peer_bin + ':' + join(root, 'bin') + ':/usr/bin:/bin',
    LANG: 'C.UTF-8',
    TMPDIR: join(root, 'tmp'),
    TMP: join(root, 'tmp'),
    TEMP: join(root, 'tmp'),
    XDG_CONFIG_HOME: join(root, 'config'),
    XDG_CACHE_HOME: join(root, 'cache'),
    XDG_DATA_HOME: join(root, 'data'),
    XDG_STATE_HOME: join(root, 'state'),
    XDG_RUNTIME_DIR: join(root, 'run'),
    PLAN_CONFIG_PATH: join(root, 'config.yaml'),
    PLAN_DB_PATH: join(root, 'data/planning.db'),
    PLAN_APP_ROOT: app_root,
    PLAN_APP_SHA: sha,
    PLAN_PORT: url.port,
    PLAN_SERVER_URL: url.origin,
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'data/credential-key'),
    PLAN_WORKSPACE_ROOT: join(root, 'workspace'),
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'run/dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'run/control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'run/lifecycle.lock'),
    ...Object.fromEntries(absent_executables),
  };
}
