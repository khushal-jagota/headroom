/** APP's side of its supervisor pipe. The supervisor sets the pipe at fd 4 when it spawns APP. */
import { writeSync } from 'node:fs';
import { Socket } from 'node:net';

/**
 * The supervisor holds the only other end of this pipe, so the pipe closes when the
 * supervisor dies for any reason. APP then stops exactly as a supervisor TERM stops it.
 * A supervised APP also leaves SIGHUP to its supervisor, which stops it and waits. On
 * Darwin APP shares the supervisor's group, so a terminal hangup reaches APP directly.
 */
export function follow_supervisor_lifetime(): void {
  const value = process.env.PLAN_SERVER_SUPERVISOR_PIPE_FD;
  if (value === undefined) return;
  delete process.env.PLAN_SERVER_SUPERVISOR_PIPE_FD;
  const fd = Number(value);
  if (!Number.isSafeInteger(fd) || fd < 3)
    throw new Error('invalid PLAN_SERVER_SUPERVISOR_PIPE_FD');
  // Node marks this inherited descriptor close-on-exec at startup, so no process that APP
  // spawns holds a copy. A copy would keep the supervisor waiting for APP's 'close'.
  process.on('SIGHUP', () => {});
  const pipe = new Socket({ fd, readable: true, writable: false });
  let gone = false;
  const stop = () => {
    if (gone) return;
    gone = true;
    try {
      writeSync(2, 'Panels supervisor is gone; the application stops.\n');
    } catch {
      /* A dead supervisor can take APP's stderr reader with it. The stop still runs. */
    }
    process.kill(process.pid, 'SIGTERM');
  };
  pipe.on('end', stop).on('close', stop).on('error', stop);
  pipe.resume();
  pipe.unref();
}
