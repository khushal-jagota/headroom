/** The installer's move mode: one JSON request on stdin, its answer on stdout. */
import { join } from 'node:path';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import { spawn_owned_process } from '../../../lifecycle/owned_processes.ts';
import { copy_environment } from '../../../core/environment.ts';
import type {
  ServerMoveInstallRequest,
  ServerMoveRemoveRequest,
} from '../../../server_move/contracts.ts';
import {
  MAX_SERVER_MOVE_INSTALLER_OUTPUT_BYTES,
  SERVER_MOVE_INSTALLER,
  SERVER_MOVE_INSTALLER_SECONDS,
} from './configuration.ts';
import { ServerMoveFailure } from './contracts.ts';

/** What the installer needs from this account: its home, its path and its service manager. */
function installer_environment(environment: Readonly<NodeJS.ProcessEnv>): Record<string, string> {
  return copy_environment({
    HOME: environment.HOME,
    USER: environment.USER,
    LOGNAME: environment.LOGNAME,
    PATH: environment.PATH || '/usr/local/bin:/usr/bin:/bin',
    LANG: 'C.UTF-8',
    TMPDIR: environment.TMPDIR,
    XDG_RUNTIME_DIR: environment.XDG_RUNTIME_DIR,
    DBUS_SESSION_BUS_ADDRESS: environment.DBUS_SESSION_BUS_ADDRESS,
  });
}
export interface ServerMoveInstallerOptions {
  readonly child_processes: OwnedChildProcesses;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
}
/** Runs `<node> <payload>/server/installation/move_main.ts` and returns its standard output. */
export async function run_move_installer(
  options: ServerMoveInstallerOptions,
  payload: string,
  request: ServerMoveInstallRequest | ServerMoveRemoveRequest,
  signal: AbortSignal,
): Promise<string> {
  const owned = await spawn_owned_process(
    [join(payload, 'node/bin/node'), join(payload, SERVER_MOVE_INSTALLER)],
    {
      child_processes: options.child_processes,
      environment: installer_environment(options.environment),
      working_directory: payload,
    },
  );
  const deadline = AbortSignal.any([
    signal,
    AbortSignal.timeout(SERVER_MOVE_INSTALLER_SECONDS * 1000),
  ]);
  const stop = () => void owned.stop().catch(() => {});
  deadline.addEventListener('abort', stop, { once: true });
  let output = Buffer.alloc(0),
    diagnostic = '';
  owned.child.stdout.on('data', (chunk: Buffer) => {
    if (output.length + chunk.length <= MAX_SERVER_MOVE_INSTALLER_OUTPUT_BYTES)
      output = Buffer.concat([output, chunk]);
  });
  owned.child.stderr.on('data', (chunk: Buffer) => {
    diagnostic = (diagnostic + chunk.toString('utf8')).slice(-2000);
  });
  const closed = new Promise<number | null>((resolve) => owned.child.once('close', resolve));
  try {
    owned.child.stdin.end(JSON.stringify(request));
    const code = await closed;
    signal.throwIfAborted();
    if (code !== 0) {
      const last = diagnostic.trim().split('\n').at(-1) ?? '';
      throw new ServerMoveFailure(
        deadline.aborted
          ? `The installer did not finish within ${SERVER_MOVE_INSTALLER_SECONDS} seconds.`
          : `The installer failed${last ? `: ${last}` : '.'}`,
      );
    }
    return output.toString('utf8');
  } finally {
    deadline.removeEventListener('abort', stop);
  }
}
