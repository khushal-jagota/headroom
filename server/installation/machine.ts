/** First-machine runtime and service inputs stay independent of the server's replaceable app. */
import { cp } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { accepted_lease } from '../environments/linux_executor/logic/lease.ts';
import {
  JOIN_LEASE_TIMEOUT_SECONDS,
  JOIN_LEASE_POLL_SECONDS,
  JOIN_HEALTH_TIMEOUT_SECONDS,
} from '../environments/linux_executor/configuration.ts';
import { assert_release_execution, run_release_command } from '../environments/release/process.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import type { HomeInstallHost, HomeInstallRequest } from './contracts.ts';
import { machine_service_environment } from './environment.ts';

export function first_machine_arguments(
  request: HomeInstallRequest,
  host: HomeInstallHost,
): string[] {
  return [
    '/bin/sh',
    '-c',
    'unset NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH DYLD_FRAMEWORK_PATH; exec "$@"',
    'panels-machine',
    join(request.root, 'machine/runtime/node/bin/node'),
    join(request.root, 'machine/runtime/server/installation/machine_main.ts'),
    join(request.root, 'machine/data/identity.json'),
    request.revision,
    executor_link_socket_path(join(request.root, 'data/planner.db')),
  ];
}
export async function prepare_first_machine(
  app: string,
  runtime: string,
  execution: ReleaseExecution,
): Promise<void> {
  assert_release_execution(execution);
  // Copy the admitted bytes. A later server update must not replace the machine's baseline supervisor.
  await cp(app, runtime, {
    recursive: true,
    errorOnExist: true,
    force: false,
    verbatimSymlinks: true,
  });
  assert_release_execution(execution);
}
export async function wait_for_first_machine(
  request: HomeInstallRequest,
  host: HomeInstallHost,
  execution: ReleaseExecution,
  started_ms: number,
): Promise<void> {
  const environment = await machine_service_environment(request, host);
  const deadline = Math.min(
    execution.deadline ?? Infinity,
    performance.now() / 1000 + JOIN_LEASE_TIMEOUT_SECONDS,
  );
  while (performance.now() / 1000 < deadline) {
    assert_release_execution(execution);
    const result = await run_release_command(
      [
        join(request.root, 'machine/runtime/node/bin/node'),
        join(request.root, 'machine/runtime/server/executor/mac/entrypoint.ts'),
        'executor',
        'health',
      ],
      { environment, timeout_seconds: JOIN_HEALTH_TIMEOUT_SECONDS, allow_nonzero: true },
      execution,
    );
    if (
      result.code === 0 &&
      accepted_lease(JSON.parse(result.stdout), request.revision, started_ms)
    )
      return;
    await delay(JOIN_LEASE_POLL_SECONDS * 1000, undefined, { signal: execution.signal });
  }
  throw new Error('The first machine did not report an accepted connection to this server');
}
