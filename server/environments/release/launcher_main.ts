/** Fixed private service route. Unit 20 owns the public command dispatcher. */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isAbsolute } from 'node:path';
import { copy_environment } from '../../core/environment.ts';
import { launch_app } from './entrypoints.ts';
import type { PackagedLauncherArguments } from './contracts.ts';

export function launcher_arguments(argv: readonly string[]): PackagedLauncherArguments {
  if (argv.length === 1 && argv[0] === 'serve') return { operation: 'serve', mode: 'foreground' };
  if (
    argv.length === 3 &&
    argv[0] === 'serve' &&
    argv[1] === '--managed-linux-runtime-directory' &&
    isAbsolute(argv[2])
  )
    return { operation: 'serve', mode: 'managed-linux', runtime_directory: argv[2] };
  throw new Error('Expected serve [--managed-linux-runtime-directory ABSOLUTE_DIRECTORY]');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const args = launcher_arguments(process.argv.slice(2));
    const ambient = copy_environment(process.env);
    await launch_app(fileURLToPath(new URL('../../../', import.meta.url)), args, ambient);
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Panels launcher failed');
    process.exitCode = 1;
  }
}
