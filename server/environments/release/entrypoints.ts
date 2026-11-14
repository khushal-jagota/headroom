/** Relocated entry wrappers and the installed application's explicit environment boundary. */
import { access, chmod, mkdir, realpath, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { is_file } from '../backup/evidence.ts';
import { shell_quote } from '../../conversation/worker_unit/logic.ts';
import { EXTERNAL_RUNTIME_ENVIRONMENT_KEYS } from '../../core/config.ts';
import { AppValidationError } from './contracts.ts';
import type {
  PackagedLauncherArguments,
  ReleaseBuildDependencies,
  ReleaseExecution,
} from './contracts.ts';
import { assert_release_execution } from './process.ts';
import { validate_app_manifest, validate_app_sha, validate_app_tree } from './identity.ts';
import { resolved_path } from '../paths.ts';
import { execve } from './posix.ts';

export async function validate_public_cli_entrypoint(root: string, input: string): Promise<void> {
  if (!input || isAbsolute(input) || input.split('/').includes('..'))
    throw new AppValidationError(
      'public CLI entrypoint must be an explicit app-relative executable',
    );
  const public_cli = await realpath(join(root, input));
  if (!path_is_within(root, public_cli) || !(await is_file(public_cli)))
    throw new AppValidationError('public CLI entrypoint must remain inside the application');
  try {
    await access(public_cli, constants.X_OK);
  } catch {
    throw new AppValidationError('public CLI entrypoint is not executable');
  }
}
export async function write_app_entrypoints(
  app_root: string,
  app_sha: string,
  dependencies: ReleaseBuildDependencies,
  execution: ReleaseExecution,
): Promise<void> {
  const root = await realpath(app_root);
  await validate_app_tree(root);
  validate_app_sha(app_sha);
  const input = dependencies.public_cli_entrypoint;
  await validate_public_cli_entrypoint(root, input);
  const prefix =
    '#!/bin/sh\nset -eu\n' +
    'case "$0" in */*) script_dir=${0%/*};; *) script_dir=.;; esac\n' +
    'root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)\n' +
    'unset NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH\n';
  assert_release_execution(execution);
  await mkdir(join(root, 'bin'), { recursive: true });
  assert_release_execution(execution);
  await writeFile(
    join(root, 'bin/panels'),
    prefix +
      `export PLAN_APP_ROOT="$root" PLAN_APP_SHA=${shell_quote(app_sha)}\n` +
      `exec "$root"/${shell_quote(input)} "$@"\n`,
  );
  assert_release_execution(execution);
  await writeFile(
    join(root, 'bin/panels-launcher'),
    prefix + 'exec node "$root/server/environments/release/launcher_main.ts" "$@"\n',
  );
  assert_release_execution(execution);
  await chmod(join(root, 'bin/panels'), 0o755);
  assert_release_execution(execution);
  await chmod(join(root, 'bin/panels-launcher'), 0o755);
  assert_release_execution(execution);
}
export async function build_app_launch_env(
  app_root: string,
  ambient: Readonly<Record<string, string>>,
): Promise<Record<string, string>> {
  const root = await resolved_path(app_root);
  const manifest = await validate_app_manifest(join(root, 'manifest.json'), {
    require_runtime: true,
  });
  if (manifest.runtime_contract?.version !== 2)
    throw new AppValidationError('Node launcher requires a version-2 runtime contract');
  return {
    ...Object.fromEntries(
      Object.entries(ambient).filter(
        ([key]) =>
          EXTERNAL_RUNTIME_ENVIRONMENT_KEYS.has(key) ||
          key.startsWith('LC_') ||
          key.toLowerCase().endsWith('_proxy') ||
          key === 'REQUEST_METHOD',
      ),
    ),
    PLAN_APP_ROOT: root,
    PLAN_APP_SHA: manifest.app_sha,
  };
}
export async function launch_app(
  app_root: string,
  args: PackagedLauncherArguments,
  ambient: Readonly<Record<string, string>>,
): Promise<never> {
  const root = await resolved_path(app_root);
  const environment = await build_app_launch_env(root, ambient);
  const version = /^22\.(\d+)\.(\d+)$/.exec(process.versions.node);
  if (!version || Number(version[1]) < 18)
    throw new AppValidationError('Node launcher requires host Node 22 >=22.18.0');
  const target = args.mode === 'foreground' ? 'foreground_main.ts' : 'managed_main.ts';
  return execve(
    process.execPath,
    [
      process.execPath,
      join(root, 'server/lifecycle', target),
      ...(args.mode === 'managed-linux' ? [args.runtime_directory] : []),
    ],
    environment,
  );
}
