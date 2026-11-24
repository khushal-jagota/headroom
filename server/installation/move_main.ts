/**
 * Sealed move-mode entry: one JSON object on stdin, `command: 'install' | 'remove'`. `install`
 * prints `ServerMoveInstallResult` as JSON on stdout. Refusals go to stderr with exit code 1.
 */
import { pathToFileURL } from 'node:url';
import { is_record } from '../core/records.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { install_moved_home, remove_moved_home } from './move.ts';
import { production_move_dependencies } from './production.ts';
import { HOME_CLEANUP_TIMEOUT_SECONDS } from './configuration.ts';

export async function move_main(): Promise<number> {
  const child_processes = new ChildProcesses(),
    controller = new AbortController();
  let interrupted = false;
  const stop = () => {
    if (interrupted) return;
    interrupted = true;
    controller.abort(new Error('Server move installation interrupted'));
    child_processes.seal();
    try {
      child_processes.force_kill(performance.now() / 1000);
    } catch (error) {
      console.error(`Installer child cleanup needs attention: ${String(error)}`);
    }
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  try {
    if (process.argv.length !== 2)
      throw new Error('The move installer accepts its request on stdin');
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      controller.signal.throwIfAborted();
      chunks.push(Buffer.from(chunk));
    }
    const request: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const command = is_record(request) ? request.command : undefined;
    if (command === 'install') {
      const deps = await production_move_dependencies({
        child_processes,
        signal: controller.signal,
        deadline: null,
      });
      console.log(JSON.stringify(await install_moved_home(request, deps)));
    } else if (command === 'remove') {
      const deps = await production_move_dependencies({
        child_processes,
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(HOME_CLEANUP_TIMEOUT_SECONDS * 1000),
        ]),
        deadline: performance.now() / 1000 + HOME_CLEANUP_TIMEOUT_SECONDS,
      });
      await remove_moved_home(request, deps);
    } else throw new Error('The move request command must be install or remove');
    return 0;
  } catch (error) {
    console.error(`Server move refused: ${error instanceof Error ? error.message : String(error)}`);
    return interrupted ? 130 : 1;
  } finally {
    child_processes.seal();
    await child_processes.settled();
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await move_main();
