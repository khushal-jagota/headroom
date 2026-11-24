/** Sealed first-home entry: one JSON object on stdin, no source checkout. */
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { home_install_request } from './logic/home.ts';
import { install_home } from './home.ts';
import { production_home_dependencies } from './production.ts';
import { HOME_COMMAND } from './configuration.ts';

export async function home_main(): Promise<number> {
  const child_processes = new ChildProcesses(),
    controller = new AbortController();
  let interrupted = false;
  const stop = () => {
    if (interrupted) return;
    interrupted = true;
    controller.abort(new Error('Home installation interrupted'));
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
      throw new Error('The home installer accepts its request on stdin');
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      controller.signal.throwIfAborted();
      chunks.push(Buffer.from(chunk));
    }
    const request = home_install_request(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    const deps = await production_home_dependencies({
      child_processes,
      signal: controller.signal,
      deadline: null,
    });
    const address = await install_home(request, deps);
    console.log(`Installed Panels at ${request.root}. Open ${address} on this machine.`);
    // Either spelling of the account home counts: the resolved one and the shell's HOME.
    const directories = [deps.account_home, deps.environment.HOME ?? deps.account_home].map(
      (home) => dirname(join(home, HOME_COMMAND)),
    );
    if (
      !(deps.environment.PATH ?? '')
        .split(':')
        .some((entry) => entry && directories.includes(resolve(entry)))
    )
      console.log(
        `To run panels, add ${directories[0]} to your PATH: export PATH="${directories[0]}:$PATH"`,
      );
    console.log('Next steps: see ops/mac-home/installation.md, After the install.');
    return 0;
  } catch (error) {
    console.error(`Home setup refused: ${error instanceof Error ? error.message : String(error)}`);
    return interrupted ? 130 : 1;
  } finally {
    child_processes.seal();
    await child_processes.settled();
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await home_main();
