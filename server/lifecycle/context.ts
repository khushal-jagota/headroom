/** One config/address source for both unit actions and foreground serve. */
import { closeSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { lifecycle_launch_settings, loadConfig } from '../core/config.ts';
import { strip } from '../projects/logic/index.ts';
import { expand_path, resolved_path } from '../environments/paths.ts';
import { resolve_server_lifecycle_lease_path } from './lease.ts';
import { ServerLifecycleError } from './contracts.ts';
import type {
  ApplicationLaunch,
  ManagedEntrypointArguments,
  UnitLifecycleContext,
} from './contracts.ts';

export async function lifecycle_launch_root(): Promise<string> {
  return resolved_path(fileURLToPath(new URL('../../', import.meta.url)));
}
export async function resolve_unit_lifecycle_context(
  args: ManagedEntrypointArguments,
  environment: NodeJS.ProcessEnv,
  launch_root: string,
): Promise<UnitLifecycleContext> {
  const settings = lifecycle_launch_settings(environment);
  const config_path = settings.config_path ?? join(launch_root, 'config.yaml');
  // loadConfig consumes the refusal switch. APP must receive the original input.
  const config = loadConfig(config_path, settings.environment);
  const explicit = strip(settings.control_socket_path ?? '');
  let control_socket_path: string;
  if (explicit) {
    const path = expand_path(explicit);
    control_socket_path = await resolved_path(isAbsolute(path) ? path : launch_root + '/' + path);
  } else
    control_socket_path = resolve_server_lifecycle_lease_path(config.port).replace(
      /\.lock$/,
      '.sock',
    );
  const lease = settings.lease_path;
  // Python Path.resolve does not expand '~' for this explicit lease input.
  const lease_path = lease
    ? await resolved_path(isAbsolute(lease) ? lease : process.cwd() + '/' + lease)
    : resolve_server_lifecycle_lease_path(config.port);
  return {
    runtime_directory: args.runtime_directory,
    control_socket_path,
    lease_path,
    port: config.port,
    config_path,
    config,
  };
}
export function private_runtime_argument(args: readonly string[]): ManagedEntrypointArguments {
  if (args.length !== 1 || !isAbsolute(args[0]!))
    throw new ServerLifecycleError('Expected one absolute managed runtime directory');
  return { runtime_directory: args[0]! };
}
export function application_launch(
  launch_root: string,
  environment: NodeJS.ProcessEnv,
): ApplicationLaunch {
  const { environment: values, listener_fd: raw } = lifecycle_launch_settings(environment);
  let listener: ApplicationLaunch['listener'];
  if (raw !== undefined) {
    const token = strip(raw);
    const fd = Number(token.replaceAll('_', ''));
    if (!/^\+?\d(?:_?\d)*$/.test(token) || !Number.isSafeInteger(fd) || fd < 0)
      throw new ServerLifecycleError('Invalid inherited Panels listener descriptor');
    let closed = false;
    listener = {
      fd,
      close() {
        if (!closed) {
          closed = true;
          closeSync(fd);
        }
      },
    };
  }
  return {
    node_executable: process.execPath,
    entrypoint: join(launch_root, 'server/lifecycle/application.ts'),
    launch_root,
    environment: values,
    ...(listener ? { listener } : {}),
  };
}
