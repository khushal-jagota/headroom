/** Host each driver's executor half with this machine's account and launch settings. */
import { BackendSpawnFailed } from '../../conversation/backends/contracts.ts';
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import { executable_override } from '../../conversation/snapshot/environment.ts';
import { BUILT_IN_DRIVERS } from '../../drivers/built_in.ts';
import type { AnyAgentDriver } from '../../drivers/core/driver.ts';
import type { ExecutorDriver, ExecutorDriverHost } from '../../drivers/core/executor.ts';
import { default_instance_id_for_driver } from '../../drivers/core/identity.ts';
import type { BackendInstanceId, DriverKind } from '../../drivers/core/identity.ts';
import type { DriverManifest } from '../../drivers/core/manifest.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { AccountSkillHome } from '../../skills/account.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import { expand_executor_path } from './platform.ts';

/** A driver this build can run on an executor machine, with its executor half loaded. */
export interface LoadedExecutorDriver {
  readonly driver_kind: DriverKind;
  readonly manifest: DriverManifest;
  readonly executor: ExecutorDriver;
}
/** Every driver's executor half, in BUILT_IN_DRIVERS order. */
export async function load_executor_drivers(
  drivers: readonly AnyAgentDriver[] = BUILT_IN_DRIVERS,
): Promise<readonly LoadedExecutorDriver[]> {
  return Promise.all(
    drivers.map(async (driver) => ({
      driver_kind: driver.driver_kind,
      manifest: driver.manifest,
      executor: await driver.executor(),
    })),
  );
}
export interface MacAdapterOptions {
  readonly drivers: readonly LoadedExecutorDriver[];
  readonly panels_server_url: string;
  readonly executable_path: (name: string) => string | null;
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly worker_supervision: WorkerSupervisionRuntime;
  readonly environment?: Readonly<NodeJS.ProcessEnv>;
  /** Defaults to this process's platform; the Linux executor shares this host. */
  readonly platform?: NodeJS.Platform;
}
/** One driver this machine runs, under its default instance id. */
export interface MacDriver {
  readonly driver_kind: DriverKind;
  readonly instance_id: BackendInstanceId;
  readonly manifest: DriverManifest;
  readonly executor: ExecutorDriver;
  readonly host: ExecutorDriverHost;
}
export interface MacAdapterHost {
  readonly environment: Readonly<NodeJS.ProcessEnv>;
  executable_path(name: string): string | null;
  /** In BUILT_IN_DRIVERS order. Each lease advertises their kinds. */
  readonly drivers: readonly MacDriver[];
  /** Refuses a backend this machine does not run. */
  factory(start: ResolvedConversationStart): BackendChildFactory;
  /** Refuses a backend this machine does not run, or one without a home. */
  native_skill_home(backend: string): AccountSkillHome;
}
function cannot_run(backend: string): BackendSpawnFailed {
  return new BackendSpawnFailed(`This machine cannot run the ${backend} backend.`);
}
export function create_mac_adapter_host(options: MacAdapterOptions): MacAdapterHost {
  const environment: NodeJS.ProcessEnv = { ...(options.environment ?? process.env) };
  const expand = (path: string) => expand_executor_path(path, environment);
  const macos = (options.platform ?? process.platform) === 'darwin';
  const executables = new Map(
    options.drivers.map(({ manifest: { executable } }) => [
      executable.name,
      options.executable_path(executable.name),
    ]),
  );
  // Resolve every driver's paths before any launch copies the environment.
  const resolved = options.drivers.map(({ manifest }) => {
    const { executable, home } = manifest;
    const override = executable_override(executable, environment, expand);
    if (override !== undefined) environment[executable.override_environment_name!] = override;
    let home_path: string | null = null;
    if (home !== undefined) {
      home_path = expand(environment[home.environment_name] || home.default_path);
      // Panels still needs the resolved folder for skills.
      if (macos && home.leave_unset_on_macos && !environment[home.environment_name])
        delete environment[home.environment_name];
      else environment[home.environment_name] = home_path;
    }
    return {
      home: home_path,
      executable: override ?? executables.get(executable.name) ?? executable.name,
    };
  });
  const drivers: MacDriver[] = options.drivers.map((driver, index) => ({
    driver_kind: driver.driver_kind,
    instance_id: default_instance_id_for_driver(driver.driver_kind),
    manifest: driver.manifest,
    executor: driver.executor,
    host: {
      environment,
      get panels_server_url() {
        return options.panels_server_url;
      },
      child_processes: options.child_processes,
      one_shot_children: options.one_shot_children,
      worker_supervision: options.worker_supervision,
      ...resolved[index]!,
    },
  }));
  const driver_of = (backend: string): MacDriver => {
    const driver = drivers.find((candidate) => candidate.instance_id === backend);
    if (driver === undefined) throw cannot_run(backend);
    return driver;
  };
  return {
    environment,
    executable_path: (name) => executables.get(name) ?? null,
    drivers,
    factory(start) {
      const driver = driver_of(start.backend_key);
      return driver.executor.child_factory(driver.host);
    },
    native_skill_home(backend) {
      const driver = driver_of(backend);
      if (driver.host.home === null) throw cannot_run(backend);
      return {
        path: driver.host.home,
        skills_directory: driver.manifest.home!.skills_directory ?? 'skills',
      };
    },
  };
}
