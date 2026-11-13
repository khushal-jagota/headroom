/** Prepared source-runtime launch. The caller owns output and the final process replacement. */
import net from 'node:net';
import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join } from 'node:path';
import { inspect_environment_instance } from './materialize.ts';
import { parse_environment_file, validate_repository_roots } from './logic.ts';
import {
  build_environment_run_env,
  build_test_environment_run_env,
  resolve_hermes_python,
} from './launch.ts';
import { EnvironmentValidationError } from './contracts.ts';
import { OperatorInterrupted } from '../command_contracts.ts';
import type {
  OperatorArguments,
  OperatorPathInputs,
  PreparedOperatorLaunch,
} from '../command_contracts.ts';
import { execve, inherit_descriptor } from '../release/posix.ts';

export async function prepare_environment_run(
  args: OperatorArguments['run'],
  environment: Readonly<NodeJS.ProcessEnv>,
  paths: OperatorPathInputs,
): Promise<{ launch: PreparedOperatorLaunch; stderr: string }> {
  if (args.repository_roots.length !== 1)
    throw new EnvironmentValidationError('environment run requires exactly one repository root');
  const request = {
    ...args,
    environment_root: paths.admit_path(paths.expand_path(args.environment_root)),
    credentials_env_file:
      args.credentials_env_file == null
        ? args.credentials_env_file
        : paths.admit_path(paths.expand_path(args.credentials_env_file)),
    repository_roots: args.repository_roots.map((path) =>
      paths.admit_path(paths.expand_path(path)),
    ),
  };
  const manifest = await inspect_environment_instance(request, paths.admit_path);
  if (manifest.prepared_at === null)
    throw new EnvironmentValidationError('environment instance is not prepared');
  const [root] = await validate_repository_roots(
    request.repository_roots,
    manifest.repository_roots,
  );
  const entrypoint = paths.admit_path(join(root!, 'server/cli/main.ts'));
  await access(entrypoint, constants.R_OK);
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major! < 22 || (major === 22 && minor! < 18))
    throw new EnvironmentValidationError('application runtime requires Node 22.18 or newer');
  const credentials =
    manifest.credentials_env_file === null
      ? {}
      : await parse_environment_file(
          paths.admit_path(manifest.credentials_env_file),
          manifest.kind,
        );
  let listener: net.Server | undefined;
  let closed = false;
  let closing: Promise<void> | undefined;
  let signal: 'SIGINT' | 'SIGTERM' | undefined;
  let prefix = '';
  let reject_interruption!: (error: OperatorInterrupted) => void;
  const interrupted = new Promise<never>((_resolve, reject) => {
    reject_interruption = reject;
  });
  void interrupted.catch(() => {});
  const interruption = () =>
    new OperatorInterrupted(signal!, prefix, new Error('The caller stopped the operation.'));
  const interrupt = (value: 'SIGINT' | 'SIGTERM') => {
    if (signal) return;
    signal = value;
    void close().then(
      () => reject_interruption(interruption()),
      (error) => reject_interruption(new OperatorInterrupted(value, prefix, error)),
    );
  };
  const sigint = () => interrupt('SIGINT');
  const sigterm = () => interrupt('SIGTERM');
  const close = (): Promise<void> =>
    (closing ??= (async () => {
      closed = true;
      process.removeListener('SIGINT', sigint);
      process.removeListener('SIGTERM', sigterm);
      if (listener?.listening)
        await new Promise<void>((resolve, reject) =>
          listener!.close((error) => (error ? reject(error) : resolve())),
        );
    })());
  try {
    let port: number;
    if (manifest.port_policy.kind === 'fixed') port = manifest.port_policy.port;
    else {
      listener = net.createServer((socket) => socket.destroy());
      await new Promise<void>((resolve, reject) => {
        listener!.once('error', reject);
        listener!.listen(0, '127.0.0.1', resolve);
      });
      port = (listener.address() as net.AddressInfo).port;
    }
    process.on('SIGINT', sigint);
    process.on('SIGTERM', sigterm);
    const builder = args.test_mode ? build_test_environment_run_env : build_environment_run_env;
    const run_environment = builder(
      {
        ...manifest,
        allowed_repository_roots: manifest.repository_roots,
        prepared: true,
        running: false,
      },
      {
        credentials,
        ambient: environment,
        hermes_python: resolve_hermes_python(undefined, environment),
        runtime_port: port,
      },
    );
    const fd = listener
      ? (listener as net.Server & { _handle: { fd: number } })._handle.fd
      : undefined;
    if (fd !== undefined) {
      run_environment.PLAN_SERVER_LISTENER_FD = String(fd);
      run_environment.PLAN_SERVER_LIFECYCLE_LEASE_PATH = paths.admit_path(
        join(manifest.instance_root, 'run/server-lifecycle.lock'),
      );
    }
    const previous = process.cwd();
    prefix = fd === undefined ? '' : `staging http://127.0.0.1:${port}\n`;
    return {
      stderr: prefix,
      launch: {
        close,
        interrupted,
        output_flushed() {
          prefix = '';
        },
        execute() {
          if (signal) throw interruption();
          if (closed) throw new EnvironmentValidationError('environment launch is closed');
          process.chdir(root!);
          try {
            if (fd !== undefined) inherit_descriptor(fd);
            return execve(
              process.execPath,
              [process.execPath, entrypoint, 'serve'],
              run_environment,
            );
          } finally {
            if (process.cwd() === root) process.chdir(previous);
          }
        },
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}
