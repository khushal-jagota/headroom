/** One new Apple Silicon installation owns its files and job until a fresh accepted lease or rollback. */
import { lstat, mkdir, open, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { userInfo } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { extract } from 'tar';
import { copy_environment } from '../../core/environment.ts';
import { is_record } from '../../core/records.ts';
import { valid_uuid } from '../../execution_identity.ts';
import { validate_server_url } from '../../executor/mac/configuration.ts';
import { credential_path, write_credential } from '../../executor/credential_file.ts';
import {
  mac_backend_path,
  mac_installation_assets,
  mac_installation_paths,
  MAC_RUNTIME_LAUNCHER_APP,
} from '../../executor/mac/platform.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { hash_archive } from '../release/archive.ts';
import { validate_app_sha } from '../release/logic/manifest.ts';
import { assert_release_execution, run_release_command } from '../release/process.ts';
import { accepted_lease } from '../linux_executor/logic/lease.ts';
import {
  JOIN_HEALTH_TIMEOUT_SECONDS,
  JOIN_LEASE_POLL_SECONDS,
  JOIN_LEASE_TIMEOUT_SECONDS,
} from '../linux_executor/configuration.ts';

const JOIN_BOOTOUT_CHECKS = 10;
const JOIN_BOOTOUT_CHECK_SECONDS = 0.5;

export interface MacExecutorJoinRequest {
  readonly archive: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly revision: string;
  readonly home_url: string;
  readonly machine_id: string;
  readonly credential: string;
  readonly home: string;
}

export function parse_mac_join_request(value: unknown): MacExecutorJoinRequest {
  if (!is_record(value)) throw new Error('Join inputs must be an object');
  const text = ['archive', 'archive_sha256', 'revision', 'home_url', 'machine_id', 'credential'];
  const fields = [...text, 'archive_size', 'home'];
  if (Object.keys(value).some((key) => !fields.includes(key)))
    throw new Error('Join inputs contain an unknown field');
  for (const key of [...text, 'home'])
    if (typeof value[key] !== 'string' || value[key] === '')
      throw new Error(`Invalid join field: ${key}`);
  if (!Number.isSafeInteger(value.archive_size) || (value.archive_size as number) <= 0)
    throw new Error('Invalid join archive size');
  return value as unknown as MacExecutorJoinRequest;
}

async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}
function join_environment(home: string, ambient: Readonly<NodeJS.ProcessEnv>) {
  const environment = copy_environment(ambient);
  for (const name of Object.keys(environment))
    if (
      name.startsWith('PLAN_') ||
      name.startsWith('DYLD_') ||
      ['NODE_OPTIONS', 'NODE_PATH'].includes(name)
    )
      delete environment[name];
  environment.HOME = home;
  return environment;
}

/** The archive must be the one the bootstrap checked, and it must name this release. */
async function install_runtime(
  request: MacExecutorJoinRequest,
  supervisor: string,
  execution: ReleaseExecution,
): Promise<void> {
  if ((await stat(request.archive)).size !== request.archive_size)
    throw new Error('The release size differs from its publication');
  if ((await hash_archive(request.archive)) !== request.archive_sha256)
    throw new Error('The release checksum differs from its publication');
  assert_release_execution(execution);
  await mkdir(supervisor, { mode: 0o700 });
  await extract({ file: request.archive, cwd: supervisor, strict: true, preservePaths: false });
  const marker: unknown = JSON.parse(await readFile(join(supervisor, 'mac-runtime.json'), 'utf8'));
  if (
    !is_record(marker) ||
    marker.schema_version !== 1 ||
    marker.platform !== 'darwin' ||
    marker.architecture !== 'arm64' ||
    marker.application_revision !== request.revision
  )
    throw new Error('The release is not the Apple Silicon machine code of this revision');
}

export async function install_mac_executor(
  request: MacExecutorJoinRequest,
  execution: ReleaseExecution,
  ambient: Readonly<NodeJS.ProcessEnv> = process.env,
): Promise<void> {
  assert_release_execution(execution);
  const home_url = validate_server_url(request.home_url);
  if (!valid_uuid(request.machine_id)) throw new Error('Join needs a valid machine UUID');
  if (!/^[A-Za-z0-9_-]{43}$/.test(request.credential))
    throw new Error('The private machine credential is invalid');
  validate_app_sha(request.revision, 'release revision');
  // The signed launcher finds its Node below the account's home, so the install must use that home.
  if (request.home !== userInfo().homedir)
    throw new Error(`HOME must be this account's home folder: ${userInfo().homedir}`);
  const backend_path = mac_backend_path(request.home);
  const {
    paths,
    launch_agent,
    console: console_script,
  } = mac_installation_assets({
    home: request.home,
    revision: request.revision,
    executor_id: request.machine_id,
    server_url: home_url,
    backend_path,
  });
  for (const path of [paths.install, paths.app, paths.plist, paths.state])
    if (await exists(path))
      throw new Error(`A Panels machine installation already exists: ${path}`);
  const environment = join_environment(request.home, ambient);
  const domain = `gui/${process.getuid!()}`;
  const launchctl = (args: readonly string[], scope = execution, allow_nonzero = false) =>
    run_release_command(
      ['/bin/launchctl', ...args],
      { environment, timeout_seconds: 30, allow_nonzero },
      scope,
    );
  const loaded = await launchctl(['print', `${domain}/${paths.job}`], execution, true);
  if (loaded.code === 0) throw new Error(`The machine job already exists: ${paths.job}`);
  if (!loaded.stderr.includes('Could not find service'))
    throw new Error('Log in to the graphical session of this machine before you join it');

  const owned: string[] = [];
  let bootstrapped = false;
  try {
    for (const parent of [dirname(paths.install), dirname(paths.app), dirname(paths.plist)])
      await mkdir(parent, { recursive: true, mode: 0o700 });
    await mkdir(paths.install, { mode: 0o700 });
    owned.push(paths.install);
    await install_runtime(request, paths.supervisor, execution);
    await mkdir(dirname(paths.console), { mode: 0o700 });
    await writeFile(paths.console, console_script, { flag: 'wx', mode: 0o755 });
    assert_release_execution(execution);
    // The join owns the app folder before the copy, so a failed copy leaves nothing behind.
    // ditto keeps the bundle exactly as signed. The check proves that the copy still verifies.
    await mkdir(paths.app, { mode: 0o700 });
    owned.push(paths.app);
    await run_release_command(
      ['/usr/bin/ditto', join(paths.supervisor, MAC_RUNTIME_LAUNCHER_APP), paths.app],
      { environment, timeout_seconds: 60 },
      execution,
    );
    await run_release_command(
      ['/usr/bin/codesign', '--verify', '--strict', paths.app],
      { environment, timeout_seconds: 60 },
      execution,
    );
    await mkdir(dirname(paths.state), { recursive: true, mode: 0o700 });
    await mkdir(paths.state, { mode: 0o700 });
    owned.push(paths.state);
    await mkdir(dirname(paths.stdout), { mode: 0o700 });
    await write_credential(credential_path(paths.state), request.credential);
    const handle = await open(paths.plist, 'wx', 0o600);
    owned.push(paths.plist);
    try {
      await handle.writeFile(launch_agent);
      await handle.sync();
    } finally {
      await handle.close();
    }
    assert_release_execution(execution);
    bootstrapped = true;
    await launchctl(['bootstrap', domain, paths.plist]);
    await wait_for_lease(request, paths, execution, environment);
  } catch (error) {
    const cleanup: ReleaseExecution = { ...execution, signal: new AbortController().signal };
    // The state folder holds the credential and leaves with the rollback. Keep its last launch error.
    const launch_error = await launch_error_tail(paths.stderr);
    try {
      if (bootstrapped) {
        await launchctl(['bootout', `${domain}/${paths.job}`], cleanup, true);
        // launchd can list the job for a moment while it removes it.
        let listed = true;
        for (let attempt = 0; listed && attempt < JOIN_BOOTOUT_CHECKS; attempt++) {
          if (attempt) await delay(JOIN_BOOTOUT_CHECK_SECONDS * 1000);
          listed = (await launchctl(['print', `${domain}/${paths.job}`], cleanup, true)).code === 0;
        }
        if (listed) throw new Error(`Cannot stop the new machine job: ${paths.job}`);
      }
      for (const path of owned.reverse()) await rm(path, { recursive: true, force: true });
    } catch (failure) {
      throw new Error(
        `Join failed: ${error instanceof Error ? error.message : String(error)}. Cleanup failed: ${failure instanceof Error ? failure.message : String(failure)}. Retained installation: ${paths.install}`,
        { cause: error },
      );
    }
    if (launch_error && error instanceof Error)
      throw new Error(`${error.message}\nLast launch error:\n${launch_error}`, { cause: error });
    throw error;
  }
}

async function launch_error_tail(path: string): Promise<string> {
  try {
    return (await readFile(path, 'utf8')).trimEnd().split('\n').slice(-20).join('\n');
  } catch {
    return '';
  }
}

async function wait_for_lease(
  request: MacExecutorJoinRequest,
  paths: ReturnType<typeof mac_installation_paths>,
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv>,
): Promise<void> {
  const started_ms = Date.now();
  const deadline = Math.min(
    performance.now() / 1000 + JOIN_LEASE_TIMEOUT_SECONDS,
    execution.deadline ?? Infinity,
  );
  const health_environment = { ...environment, PLAN_EXECUTOR_ROOT: paths.state };
  let detail = 'The machine code did not report an accepted lease';
  while (performance.now() / 1000 < deadline) {
    assert_release_execution(execution);
    try {
      const result = await run_release_command(
        [paths.console, 'executor', 'health'],
        {
          environment: health_environment,
          timeout_seconds: JOIN_HEALTH_TIMEOUT_SECONDS,
          allow_nonzero: true,
        },
        execution,
      );
      if (result.code === 0) {
        const status: unknown = JSON.parse(result.stdout);
        if (accepted_lease(status, request.revision, started_ms)) return;
        if (is_record(status))
          detail = String(status.last_lease_failure || status.refusal || detail);
      }
    } catch (error) {
      execution.signal.throwIfAborted();
      if (error instanceof AggregateError) throw error;
    }
    await delay(JOIN_LEASE_POLL_SECONDS * 1000, undefined, { signal: execution.signal });
  }
  throw new Error(`Join did not complete: ${detail}`);
}
