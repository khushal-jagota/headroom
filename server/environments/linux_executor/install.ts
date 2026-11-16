/** Admit a release before private credential and exclusive unit publication. */
import { chmod, lstat, mkdir, open, readdir, rm, rmdir, stat, unlink } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import {
  credential_path,
  read_credential,
  write_credential,
} from '../../executor/credential_file.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { resolved_path } from '../paths.ts';
import { LINUX_EXECUTOR } from '../release/contracts.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { validate_app_manifest } from '../release/identity.ts';
import { require_host_node } from '../release/logic/host.ts';
import { assert_release_execution, run_release_command } from '../release/process.ts';
import { RUNTIME_PROBE_TIMEOUT_SECONDS } from '../release/process_configuration.ts';
import { extract_runtime } from './archive.ts';
import type { LinuxExecutorInstallation, LinuxExecutorInstallRequest } from './contracts.ts';
import { LINUX_EXECUTOR_SERVICE_NAME } from './configuration.ts';
import { render_service } from './units.ts';
import { absolute_executor_path } from './logic/paths.ts';

const absent = (error: unknown): boolean =>
  ['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '');
async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (absent(error)) return false;
    throw error;
  }
}
async function nonempty(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isDirectory() && (await readdir(path)).length !== 0;
  } catch (error) {
    if (absent(error)) return false;
    throw error;
  }
}
export async function install(
  configuration: LinuxExecutorInstallation,
  request: LinuxExecutorInstallRequest,
  execution: ReleaseExecution,
): Promise<string> {
  assert_release_execution(execution);
  absolute_executor_path(request.unit_directory);
  const installation = await resolved_path(configuration.installation),
    data = await resolved_path(configuration.data);
  if (path_is_within(installation, data) || path_is_within(data, installation))
    throw new Error('executor data and installation must be separate folders');
  const releases = join(installation, 'releases'),
    unit = join(request.unit_directory, LINUX_EXECUTOR_SERVICE_NAME);
  if ((await nonempty(data)) || (await nonempty(releases)) || (await exists(unit)))
    throw new Error(
      'This executor is already installed. Use its update path instead of reinstalling.',
    );
  const credential = await read_credential(request.credential_file);
  if (credential === null) throw new Error('machine credential file is missing');
  render_service(configuration, join(releases, 'validation'));
  const directories: string[] = [],
    files: string[] = [];
  const data_mode = await stat(data).then(
    (value) => value.mode & 0o777,
    (error: unknown) => {
      if (absent(error)) return null;
      throw error;
    },
  );
  let runtime: string | undefined;
  async function directory(path: string): Promise<void> {
    if (await exists(path)) {
      if (!(await stat(path)).isDirectory())
        throw new Error(`executor directory is not a directory: ${path}`);
      return;
    }
    await directory(dirname(path));
    assert_release_execution(execution);
    try {
      await mkdir(path, { mode: 0o700 });
      directories.push(path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    }
  }
  try {
    await directory(releases);
    runtime = await extract_runtime(
      request.archive,
      releases,
      {
        expected_sha256: request.archive_sha256,
        expected_size: request.archive_size,
        expected_revision: configuration.revision,
      },
      execution,
    );
    const manifest = await validate_app_manifest(join(runtime, 'manifest.json'), {
      expected_sha: configuration.revision,
      expected_target: LINUX_EXECUTOR,
      require_runtime: true,
      verify_artifact_digest: false,
    });
    if (manifest.runtime_contract?.version !== 2 || manifest.runtime_contract.system !== 'linux')
      throw new Error('Linux executor requires its native Node runtime contract');
    const node = await run_release_command(
      [configuration.node_executable, '--version'],
      {
        timeout_seconds: RUNTIME_PROBE_TIMEOUT_SECONDS,
        environment: {
          HOME: configuration.home,
          PATH: dirname(configuration.node_executable) + ':/usr/bin:/bin',
        },
      },
      execution,
    );
    require_host_node(node.stdout, manifest.runtime_contract);
    await directory(data);
    assert_release_execution(execution);
    await chmod(data, 0o700);
    assert_release_execution(execution);
    files.push(credential_path(data));
    await write_credential(credential_path(data), credential);
    await directory(request.unit_directory);
    assert_release_execution(execution);
    const handle = await open(unit, 'wx', 0o600);
    files.push(unit);
    try {
      await handle.writeFile(render_service(configuration, runtime));
      await handle.sync();
    } finally {
      await handle.close();
    }
    return unit;
  } catch (error) {
    const failures: unknown[] = [error];
    for (const file of files.reverse()) {
      try {
        await unlink(file);
      } catch (cleanup) {
        if (!absent(cleanup)) failures.push(cleanup);
      }
    }
    if (runtime !== undefined && dirname(runtime) === releases) {
      try {
        await rm(runtime, { recursive: true });
      } catch (cleanup) {
        if (!absent(cleanup)) failures.push(cleanup);
      }
    }
    if (data_mode !== null) {
      try {
        await chmod(data, data_mode);
      } catch (cleanup) {
        failures.push(cleanup);
      }
    }
    for (const path of directories.reverse()) {
      try {
        await rmdir(path);
      } catch (cleanup) {
        if (
          !['ENOENT', 'ENOTEMPTY', 'EEXIST'].includes((cleanup as NodeJS.ErrnoException).code ?? '')
        )
          failures.push(cleanup);
      }
    }
    if (failures.length > 1)
      throw new AggregateError(failures, 'Executor installation cleanup failed');
    throw error;
  }
}
