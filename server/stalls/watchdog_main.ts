/** Fixed timer entry. Public argument parsing remains with the CLI. */
import { isAbsolute } from 'node:path';
import { watchdog_handler } from './cli.ts';

try {
  const args = process.argv.slice(2);
  if (args.length !== 2 || !args.every(isAbsolute))
    throw new Error('Expected absolute watchdog database and state-directory paths');
  console.log(await watchdog_handler({ db: args[0]!, state_dir: args[1]! }, process.env));
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Panels watchdog failed');
  process.exitCode = 1;
}
