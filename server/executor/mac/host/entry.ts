/** Production bindings use the invocation's existing stores and process owners. */
import { backend_executable_path } from '../../../conversation/snapshot/environment.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../../lifecycle/configuration.ts';
import { AgentProcesses } from '../agent_processes.ts';
import { create_mac_adapter_host } from '../adapters.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import { MAC_RUNTIME_UPDATE_EXIT_CODE } from '../configuration.ts';
import { MacUpdateController, create_update_session } from '../updates/controller.ts';
import { MacService } from './service.ts';
import type { MacHostBindings, MacHostOptions } from './contracts.ts';

export function production_bindings(
  configuration: MacHostOptions,
  environment: Readonly<NodeJS.ProcessEnv>,
): MacHostBindings {
  return {
    adapters: (owners) =>
      create_mac_adapter_host({
        ...owners,
        panels_server_url: configuration.panels_server_url,
        environment,
        executable_path: (name) => backend_executable_path(name, environment),
      }),
    catalog: (host, owners, on_failure) => create_mac_catalog({ host, ...owners, on_failure }),
    processes: ({ paths, files, one_shot_children }) =>
      process.platform === 'darwin'
        ? new AgentProcesses({
            run: paths.run,
            parent_process_id: process.pid,
            files,
            one_shot_children,
            warn: console.warn,
          })
        : null,
    updates: (context) =>
      new MacUpdateController({
        paths: context.paths,
        runner: context.runner,
        presence: { identity: context.identity, leased: () => context.presence.leased() },
        outbox: context.outbox,
        files: context.update_files,
        executor_id: configuration.executor_id,
        runtime: context.runtime,
        session_factory: () =>
          create_update_session(configuration.panels_server_url, configuration.http),
        request_exit: () => context.request_exit(MAC_RUNTIME_UPDATE_EXIT_CODE),
        on_failure: context.on_failure,
      }),
  };
}

export async function run_mac_service(
  configuration: MacHostOptions,
  environment: Readonly<NodeJS.ProcessEnv>,
): Promise<number> {
  let service: MacService | undefined;
  let interrupted: number | undefined;
  const interrupt = (code: number) => {
    interrupted ??= code;
    service?.request_exit(code);
  };
  const sigint = () => interrupt(130),
    sigterm = () => interrupt(143);
  const options = () => ({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  process.on('SIGINT', sigint);
  process.on('SIGTERM', sigterm);
  try {
    service = await MacService.open(
      configuration,
      production_bindings(configuration, environment),
      options(),
    );
    if (interrupted !== undefined) {
      service.request_exit(interrupted);
      await service.settled();
      return interrupted;
    }
    return await service.run();
  } finally {
    try {
      if (service) {
        try {
          await service.stop(options());
        } finally {
          await service.settled();
        }
      }
    } finally {
      process.removeListener('SIGINT', sigint);
      process.removeListener('SIGTERM', sigterm);
    }
  }
}
