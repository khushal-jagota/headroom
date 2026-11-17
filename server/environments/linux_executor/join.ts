/** One new Linux installation owns its unit until a fresh accepted lease or rollback. */
import { lstat, mkdir, mkdtemp, open, readFile, rm, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { copy_environment } from '../../core/environment.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import { is_record } from '../../core/records.ts';
import { valid_uuid } from '../../execution_identity.ts';
import { validate_server_url } from '../../executor/mac/configuration.ts';
import { write_credential } from '../../executor/credential_file.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { validate_app_sha } from '../release/logic/manifest.ts';
import { assert_release_execution, run_release_command } from '../release/process.ts';
import { unit_value } from '../release/units.ts';
import type { LinuxExecutorInstallation, LinuxExecutorJoinRequest } from './contracts.ts';
import { install } from './install.ts';
import { absolute_executor_path, linux_executor_path } from './logic/paths.ts';
import { accepted_lease } from './logic/lease.ts';
import {
  JOIN_HEALTH_TIMEOUT_SECONDS,
  JOIN_LEASE_POLL_SECONDS,
  JOIN_LEASE_TIMEOUT_SECONDS,
  LINUX_EXECUTOR_ENTRYPOINT,
  LINUX_EXECUTOR_SERVICE_NAME,
} from './configuration.ts';

export function parse_join_request(value: unknown): LinuxExecutorJoinRequest {
  if (!is_record(value)) throw new Error('Join inputs must be an object');
  const fields = [
    'archive',
    'archive_sha256',
    'archive_size',
    'revision',
    'home_url',
    'machine_id',
    'credential',
    'root',
    'home',
    'node_executable',
    'isolated_providers',
  ];
  if (Object.keys(value).some((key) => !fields.includes(key)))
    throw new Error('Join inputs contain an unknown field');
  for (const key of fields.filter((key) => !['archive_size', 'isolated_providers'].includes(key)))
    if (typeof value[key] !== 'string' || value[key] === '')
      throw new Error(`Invalid join field: ${key}`);
  if (!Number.isSafeInteger(value.archive_size) || (value.archive_size as number) <= 0)
    throw new Error('Invalid join archive size');
  if (value.isolated_providers !== undefined && typeof value.isolated_providers !== 'boolean')
    throw new Error('Invalid isolated providers choice');
  return {
    ...value,
    isolated_providers: value.isolated_providers ?? false,
  } as unknown as LinuxExecutorJoinRequest;
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
function join_environment(
  request: LinuxExecutorJoinRequest,
  ambient: Readonly<NodeJS.ProcessEnv>,
): Record<string, string> {
  const environment = copy_environment(ambient);
  for (const name of Object.keys(environment))
    if (
      name.startsWith('PLAN_') ||
      ['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'].includes(name)
    )
      delete environment[name];
  environment.HOME = request.home;
  return environment;
}
export async function provider_environment(
  root: string,
  isolated: boolean,
): Promise<Record<string, string>> {
  if (!isolated) return {};
  const environment: Record<string, string> = {};
  for (const { manifest } of BUILT_IN_MANIFESTS) {
    if (manifest.home === undefined) continue;
    const path = join(root, 'providers', manifest.home.isolated_directory_name);
    await mkdir(path, { recursive: true, mode: 0o700 });
    environment[manifest.home.environment_name] = path;
  }
  return environment;
}
export async function wait_for_lease(
  request: LinuxExecutorJoinRequest,
  runtime: string,
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv>,
  timeout_seconds = JOIN_LEASE_TIMEOUT_SECONDS,
): Promise<void> {
  const data = join(request.root, 'data'),
    started_ms = Date.now();
  const deadline = Math.min(
    performance.now() / 1000 + timeout_seconds,
    execution.deadline ?? Infinity,
  );
  const health_environment = join_environment(request, environment);
  health_environment.PLAN_EXECUTOR_ROOT = data;
  health_environment.PATH = linux_executor_path(runtime, request.node_executable, request.home);
  let detail = 'The executor did not report an accepted lease';
  while (performance.now() / 1000 < deadline) {
    assert_release_execution(execution);
    try {
      const result = await run_release_command(
        [request.node_executable, join(runtime, LINUX_EXECUTOR_ENTRYPOINT), 'executor', 'health'],
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
export async function install_linux_executor(
  request: LinuxExecutorJoinRequest,
  execution: ReleaseExecution,
  ambient: Readonly<NodeJS.ProcessEnv> = process.env,
): Promise<void> {
  assert_release_execution(execution);
  const home_url = validate_server_url(request.home_url);
  if (!valid_uuid(request.machine_id)) throw new Error('Join needs a valid machine UUID');
  if (request.credential.length !== 43 || !/^[A-Za-z0-9_-]{43}$/.test(request.credential))
    throw new Error('The private machine credential is invalid');
  validate_app_sha(request.revision, 'release revision');
  for (const path of [request.root, request.home, request.node_executable])
    absolute_executor_path(path);
  if (await exists(request.root))
    throw new Error(`The installation root already exists: ${request.root}`);
  const units = join(request.home, '.config/systemd/user'),
    unit = join(units, LINUX_EXECUTOR_SERVICE_NAME);
  if (await exists(unit)) throw new Error(`The executor service already exists: ${unit}`);
  const environment = join_environment(request, ambient);
  const command = (
    args: readonly string[],
    timeout_seconds: number,
    scope = execution,
    allow_nonzero = false,
  ) =>
    run_release_command(
      ['systemctl', '--user', ...args],
      { environment, timeout_seconds, allow_nonzero },
      scope,
    );
  await command(['show-environment'], 10);
  const loaded = await command(
    ['show', LINUX_EXECUTOR_SERVICE_NAME, '--property=LoadState', '--value'],
    10,
    execution,
    true,
  );
  if (loaded.code !== 0 || loaded.stdout.trim() !== 'not-found')
    throw new Error(`The executor service already exists: ${LINUX_EXECUTOR_SERVICE_NAME}`);
  assert_release_execution(execution);
  await mkdir(dirname(request.root), { recursive: true, mode: 0o700 });
  assert_release_execution(execution);
  await mkdir(request.root, { mode: 0o700 });
  let owns_unit = false,
    load_attempted = false;
  try {
    const configuration: LinuxExecutorInstallation = {
      installation: join(request.root, 'installation'),
      data: join(request.root, 'data'),
      executor_id: request.machine_id,
      server_url: home_url,
      revision: request.revision,
      home: request.home,
      node_executable: request.node_executable,
    };
    const temporary = await mkdtemp(join(tmpdir(), 'panels-join-credential-'));
    let staged_unit: string;
    try {
      const grant = join(temporary, 'credential.json');
      await write_credential(grant, request.credential);
      staged_unit = await install(
        configuration,
        {
          archive: request.archive,
          archive_sha256: request.archive_sha256,
          archive_size: request.archive_size,
          credential_file: grant,
          unit_directory: join(request.root, 'units'),
        },
        execution,
      );
    } finally {
      await rm(temporary, { recursive: true });
    }
    const private_environment = await provider_environment(
      request.root,
      request.isolated_providers,
    );
    let text = await readFile(staged_unit, 'utf8');
    if (Object.keys(private_environment).length) {
      const marker = '\n[Install]\n',
        index = text.indexOf(marker);
      if (index < 0) throw new Error('The new executor unit has no Install section');
      const additions = Object.entries(private_environment)
        .map(([name, value]) => 'Environment=' + unit_value(`${name}=${value}`))
        .join('\n');
      text = text.slice(0, index) + '\n' + additions + text.slice(index);
      await writeFile(staged_unit, text);
    }
    assert_release_execution(execution);
    await mkdir(units, { recursive: true });
    const handle = await open(unit, 'wx', 0o600);
    owns_unit = true;
    try {
      await handle.writeFile(text);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await command(['daemon-reload'], 15);
    load_attempted = true;
    await command(['enable', '--now', LINUX_EXECUTOR_SERVICE_NAME], 30);
    const runtime = join(
      configuration.installation,
      'releases',
      `${request.revision}-${request.archive_sha256}`,
    );
    await wait_for_lease(request, runtime, execution, environment);
  } catch (error) {
    const cleanup: ReleaseExecution = { ...execution, signal: new AbortController().signal };
    try {
      if (load_attempted) {
        const stopped = await command(['stop', LINUX_EXECUTOR_SERVICE_NAME], 30, cleanup, true);
        if (stopped.code !== 0) {
          const current = await command(
            ['show', LINUX_EXECUTOR_SERVICE_NAME, '--property=ActiveState', '--value'],
            10,
            cleanup,
            true,
          );
          if (current.code !== 0 || current.stdout.trim() !== 'inactive')
            throw new Error(`Cannot stop the new executor service: ${LINUX_EXECUTOR_SERVICE_NAME}`);
        }
        await command(['disable', LINUX_EXECUTOR_SERVICE_NAME], 15, cleanup);
      }
      if (owns_unit) {
        await unlink(unit);
        await command(['daemon-reload'], 15, cleanup);
      }
      await rm(request.root, { recursive: true });
    } catch (failure) {
      throw new Error(
        `Join failed: ${error instanceof Error ? error.message : String(error)}. Cleanup failed for ${LINUX_EXECUTOR_SERVICE_NAME}: ${failure instanceof Error ? failure.message : String(failure)}. Retained installation: ${request.root}`,
        { cause: error },
      );
    }
    throw error;
  }
}
