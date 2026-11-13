/** Fake time and exact fake child handles only. No process or socket starts. */
import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { expect, test, vi } from 'vitest';
import { acquire_server_lifecycle_lease } from './lease.ts';
import { open_control_server } from './control.ts';
import { run_server_supervisor } from './supervisor.ts';
import { SUPERVISOR_APP_STOP_SECONDS } from './configuration.ts';

vi.mock('node:child_process', () => ({ spawn: vi.fn() }));
vi.mock('./lease.ts', () => ({ acquire_server_lifecycle_lease: vi.fn() }));
vi.mock('./control.ts', () => ({ open_control_server: vi.fn() }));

test('one first-stop bound survives restart and never signals an exited APP group', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  const exit = vi.spyOn(process, 'exit').mockImplementation(() => undefined as never);
  const group = vi.spyOn(process, 'kill').mockReturnValue(true);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    for (const already_exited of [false, true]) {
      const lease_close = vi.fn(),
        listener_close = vi.fn();
      vi.mocked(acquire_server_lifecycle_lease).mockReturnValueOnce({ close: lease_close });
      let restart!: () => void;
      vi.mocked(open_control_server).mockImplementationOnce(
        async (_path, _request, restart_app) => {
          restart = restart_app;
          return { cancel_pending() {}, close: async () => {} };
        },
      );
      let spawned!: () => void;
      const ready = new Promise<void>((resolve) => {
        spawned = resolve;
      });
      const child = Object.assign(new EventEmitter(), {
        pid: 424242,
        exitCode: null as number | null,
        signalCode: null as NodeJS.Signals | null,
        kill: vi.fn<(signal?: NodeJS.Signals | number) => boolean>(() => true),
      });
      vi.mocked(spawn).mockImplementationOnce(() => {
        spawned();
        return child as unknown as ChildProcess;
      });
      const before = new Set(process.listeners('SIGTERM'));
      const running = run_server_supervisor({
        mode: { kind: 'foreground' },
        application: {
          node_executable: '/synthetic/node',
          entrypoint: '/synthetic/app.ts',
          launch_root: '/synthetic',
          environment: {},
          listener: { fd: 3, close: listener_close },
        },
        lease_path: '/synthetic/lease',
        control_socket_path: '/synthetic/socket',
        port: 0,
      });
      try {
        await ready;
        const stop = process.listeners('SIGTERM').find((callback) => !before.has(callback))!;
        expect(stop).toBeTypeOf('function');
        restart();
        await Promise.resolve();
        expect(child.kill).toHaveBeenCalledExactlyOnceWith('SIGTERM');
        await vi.advanceTimersByTimeAsync(20_000);
        stop('SIGTERM');
        stop('SIGTERM');
        if (already_exited) child.emit('exit', 0, null);
        const previous_groups = group.mock.calls.length;
        const previous_exits = exit.mock.calls.length;
        await vi.advanceTimersByTimeAsync(SUPERVISOR_APP_STOP_SECONDS * 1000 - 20_001);
        expect(exit.mock.calls).toHaveLength(previous_exits);
        await vi.advanceTimersByTimeAsync(1);
        expect(exit.mock.calls).toHaveLength(previous_exits + 1);
        expect(exit).toHaveBeenLastCalledWith(1);
        if (process.platform !== 'win32') {
          expect(group.mock.calls).toHaveLength(previous_groups + (already_exited ? 0 : 1));
          if (!already_exited) expect(group).toHaveBeenLastCalledWith(-child.pid, 'SIGKILL');
        } else
          expect(child.kill.mock.calls.filter(([signal]) => signal === 'SIGKILL')).toHaveLength(
            already_exited ? 0 : 1,
          );
        expect(lease_close).not.toHaveBeenCalled();
        expect(listener_close).not.toHaveBeenCalled();
        expect(spawn).toHaveBeenCalledTimes(already_exited ? 2 : 1);
      } finally {
        // The mocked terminal exit cannot close the process. Supply the actual close explicitly.
        child.exitCode = 0;
        child.emit('exit', 0, null);
        child.emit('close', 0, null);
        await running;
      }
      expect(lease_close).toHaveBeenCalledOnce();
      expect(listener_close).toHaveBeenCalledOnce();
    }
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});
