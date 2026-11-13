/** One stable supervisor, one replaceable application, ownership through actual reaping. */
import { spawn } from 'node:child_process';
import { writeSync } from 'node:fs';
import type { ChildProcess } from 'node:child_process';
import { acquire_server_lifecycle_lease } from './lease.ts';
import { open_control_server } from './control.ts';
import {
  capability_capture_roots,
  admit_capability_placement,
  capability_matches,
  publish_stop_capability,
} from './stop_capability.ts';
import {
  STOP_INTENT_SECONDS,
  SUPERVISOR_APP_STOP_SECONDS,
  monotonic_seconds,
} from './configuration.ts';
import { stop_intent_is_current } from './logic/shutdown.ts';
import { observe_shutdown } from './deadline.ts';
import { ServerLifecycleError } from './contracts.ts';
import type { StopIntent, SupervisorOptions } from './contracts.ts';

interface OwnedApplication {
  readonly process: ChildProcess;
  readonly closed: Promise<0 | 1>;
  exited: boolean;
}
export async function run_server_supervisor(options: SupervisorOptions): Promise<0 | 1> {
  const mode = options.mode;
  let lease: ReturnType<typeof acquire_server_lifecycle_lease>;
  try {
    if (mode.kind === 'managed-linux') {
      const { managed_process_support } = await import('./linux_process.ts');
      managed_process_support();
      await admit_capability_placement(
        mode.runtime_directory,
        await capability_capture_roots(mode),
      );
    }
    lease = acquire_server_lifecycle_lease(options.lease_path, options.port);
  } catch (error) {
    options.application.listener?.close();
    throw error;
  }
  let capability: Awaited<ReturnType<typeof publish_stop_capability>> | undefined;
  let control: Awaited<ReturnType<typeof open_control_server>> | undefined;
  let child: OwnedApplication | undefined, stopping_child: Promise<0 | 1> | undefined;
  let restart_flight = Promise.resolve(),
    stopping = false,
    finished = false;
  let intent: StopIntent | null = null;
  let complete!: (result: 0 | 1) => void;
  const result = new Promise<0 | 1>((resolve) => {
    complete = resolve;
  });
  const finish = (code: 0 | 1) => {
    if (!finished) {
      finished = true;
      control?.cancel_pending();
      complete(code);
    }
  };
  const stop_child = (): Promise<0 | 1> => {
    if (stopping_child) return stopping_child;
    const owned = child;
    if (!owned) return Promise.resolve(0);
    const deadline = monotonic_seconds() + SUPERVISOR_APP_STOP_SECONDS;
    let signal_failed = false;
    // Reserve the actual close flight and its first-stop observer before signal delivery.
    const flight = owned.closed.then((code): 0 | 1 => {
      if (child === owned) child = undefined;
      if (stopping_child === flight) stopping_child = undefined;
      return signal_failed ? 1 : code;
    });
    stopping_child = flight;
    void observe_shutdown(owned.closed, { deadline }, 'supervisor application').catch(() => {
      try {
        writeSync(
          2,
          `Panels application exceeded the ${SUPERVISOR_APP_STOP_SECONDS}-second supervisor stop bound; forcing its owned group.\n`,
        );
      } catch {
        /* Log failure cannot prevent the terminal fallback. */
      }
      try {
        const current = owned.process;
        if (
          !owned.exited &&
          current.pid !== undefined &&
          current.exitCode === null &&
          current.signalCode === null
        ) {
          if (process.platform === 'win32') current.kill('SIGKILL');
          else process.kill(-current.pid, 'SIGKILL');
        }
      } catch (error) {
        try {
          writeSync(2, `Panels application force was not confirmed: ${String(error)}\n`);
        } catch {
          /* Attempted force remains a failure even without stderr. */
        }
      } finally {
        // No unbounded finally await or unconfirmed resource finalizers on this path.
        process.exit(1);
      }
    });
    if (!owned.exited && owned.process.exitCode === null && owned.process.signalCode === null) {
      try {
        owned.process.kill('SIGTERM');
      } catch (error) {
        signal_failed = true;
        try {
          console.error(`Panels application stop signal failed: ${String(error)}`);
        } catch {
          /* The original observer and actual close still own this child. */
        }
      }
    }
    return flight;
  };
  const stop = () => {
    if (stopping || finished) return;
    stopping = true;
    intent = null;
    control?.cancel_pending();
    void stop_child()
      .then(async (code) => {
        await restart_flight;
        return code;
      })
      .then(
        (code) => finish(code),
        () => finish(1),
      );
  };
  const signal = (name: 'SIGINT' | 'SIGTERM') => {
    if (stopping || finished) return;
    if (
      mode.kind === 'foreground' ||
      (capability &&
        stop_intent_is_current(intent, capability.record.incarnation, monotonic_seconds()))
    )
      stop();
    else
      console.error(
        `Panels supervisor refused ${name} without a current authenticated stop intent.`,
      );
  };
  const interrupt = () => signal('SIGINT'),
    terminate = () => signal('SIGTERM');
  const start_child = () => {
    if (stopping || finished) return;
    const environment = { ...options.application.environment };
    delete environment.RUNTIME_DIRECTORY;
    delete environment.PLAN_SERVER_LISTENER_FD;
    environment.PLAN_SERVER_CONTROL_SOCKET = options.control_socket_path;
    const listener = options.application.listener;
    if (listener) environment.PLAN_SERVER_LISTENER_FD = '3';
    const process_child = spawn(
      options.application.node_executable,
      [options.application.entrypoint],
      {
        cwd: options.application.launch_root,
        env: environment,
        detached: true,
        stdio: listener
          ? ['ignore', 'inherit', 'inherit', listener.fd]
          : ['ignore', 'inherit', 'inherit'],
      },
    );
    let reaped!: (result: 0 | 1) => void;
    const owned: OwnedApplication = {
      process: process_child,
      exited: false,
      closed: new Promise((resolve) => {
        reaped = resolve;
      }),
    };
    child = owned;
    process_child.once('exit', () => {
      owned.exited = true;
    });
    process_child.once('error', () => {
      if (process_child.pid === undefined) owned.exited = true;
      /* close below remains the actual owned cleanup boundary. */
    });
    process_child.once('close', (code, signal) => {
      owned.exited = true;
      reaped(code === 0 && signal === null ? 0 : 1);
      if (child === owned && !stopping_child && !stopping) finish(1);
    });
  };
  const restart = () => {
    if (stopping || finished) return;
    restart_flight = restart_flight
      .then(async () => {
        if (stopping || finished) return;
        if ((await stop_child()) !== 0) {
          finish(1);
          return;
        }
        if (!stopping && !finished) start_child();
      })
      .catch(() => finish(1));
  };
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  try {
    if (mode.kind === 'managed-linux')
      capability = await publish_stop_capability(mode.runtime_directory);
    if (stopping) return 0;
    control = await open_control_server(
      options.control_socket_path,
      (request) => {
        if (stopping || finished) return null;
        if (request.operation === 'restart') {
          if (!child || child.process.exitCode !== null || child.process.signalCode !== null)
            return null;
          return { version: 1, status: 'accepted' };
        }
        if (
          !capability ||
          !capability_matches(capability.record, request.incarnation, request.capability)
        )
          return null;
        intent = {
          incarnation: capability.record.incarnation,
          expires_at: monotonic_seconds() + STOP_INTENT_SECONDS,
        };
        return {
          version: 2,
          status: 'accepted',
          incarnation: capability.record.incarnation,
          supervisor_pid: process.pid,
        };
      },
      restart,
    );
    start_child();
    return await result;
  } catch (error) {
    throw error instanceof ServerLifecycleError
      ? error
      : new ServerLifecycleError(
          `Panels supervisor failed: ${error instanceof Error ? error.message : 'unknown error'}`,
        );
  } finally {
    stopping = true;
    control?.cancel_pending();
    try {
      await stop_child();
      await restart_flight;
      try {
        await control?.close();
      } finally {
        await capability?.close();
      }
    } finally {
      process.off('SIGINT', interrupt);
      process.off('SIGTERM', terminate);
      options.application.listener?.close();
      lease.close();
    }
  }
}
