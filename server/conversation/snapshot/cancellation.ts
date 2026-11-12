import type { BackendProbeEnvironment } from './contracts.ts';
/** The full probe shares its caller's lifetime without changing adapter signatures. */
export function cancellable_environment(
  environment: BackendProbeEnvironment,
  signal?: AbortSignal,
): BackendProbeEnvironment {
  if (signal === undefined) return environment;
  return {
    executable_path: (name) => {
      signal.throwIfAborted();
      return environment.executable_path(name);
    },
    configured_executable_path: (name) => {
      signal.throwIfAborted();
      return environment.configured_executable_path(name);
    },
    real_path: (path) => environment.real_path(path),
    user_local_npm_prefix: () => environment.user_local_npm_prefix(),
    prefix_is_owned_and_writable: (prefix) => environment.prefix_is_owned_and_writable(prefix),
    run: (argv, options) => environment.run(argv, { ...options, signal }),
    latest_released_version: (name, options) =>
      environment.latest_released_version(name, { ...options, signal }),
  };
}
