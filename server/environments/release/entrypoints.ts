/** Relocated entry wrappers and the installed application's explicit environment boundary. */
import { access, chmod, mkdir, realpath, stat, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';
import { shell_quote } from '../../conversation/worker_unit/logic.ts';
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

const EXTERNAL_RUNTIME_ENVIRONMENT_KEYS = new Set([
  'HOME',
  'LOGNAME',
  'PATH',
  'SHELL',
  'LANG',
  'TERM',
  'TMPDIR',
  'USER',
  'XDG_RUNTIME_DIR',
  'DBUS_SESSION_BUS_ADDRESS',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_PORT',
  'PLAN_BOUNDARY_HOUR',
  'PLAN_TICK_SECONDS',
  'PLAN_DISPATCH_ENABLED',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_TRUSTED_INGRESS_PROVIDER',
  'PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN',
  'PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN',
  'PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS',
  'PLAN_GROQ_API_KEY',
  'PLAN_APNS_KEY_PATH',
  'PLAN_APNS_KEY_ID',
  'PLAN_APNS_TEAM_ID',
  'PLAN_APNS_BUNDLE_IDS',
  'PLAN_HERMES_HOME',
  'PLAN_HERMES_PYTHON',
  'PLAN_CLAUDE_EXECUTABLE',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_UPDATE_REPOSITORY',
  'PLAN_UPDATE_TOKEN_FILE',
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_API_KEY',
  // TS's explicit lifecycle address must survive the first launcher boundary.
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
  'PLAN_SERVER_LISTENER_FD',
]);
function inside(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix);
}
export async function validate_public_cli_entrypoint(root: string, input: string): Promise<void> {
  if (!input || isAbsolute(input) || input.split('/').includes('..'))
    throw new AppValidationError(
      'public CLI entrypoint must be an explicit app-relative executable',
    );
  const public_cli = await realpath(join(root, input));
  if (!inside(root, public_cli) || !(await stat(public_cli)).isFile())
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
