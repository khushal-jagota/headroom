/**
 * Build and sign the one launcher app that every Apple Silicon account shares.
 * A release signs it with the project's Developer ID. A rehearsal passes "-" for an ad-hoc signature.
 */
import { lstat, mkdir, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join } from 'node:path';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { mac_launcher_bundle } from '../platform.ts';
import { macho_arm64 } from './build.ts';

const LAUNCHER_COMMAND_SECONDS = 120;

export async function build_mac_launcher(output: string, identity: string): Promise<void> {
  if (process.platform !== 'darwin' || process.arch !== 'arm64')
    throw new Error('The launcher build requires Darwin arm64');
  if (!isAbsolute(output) || basename(output) !== 'Panels TypeScript.app')
    throw new Error('The launcher output must be an absolute path to Panels TypeScript.app');
  if (!identity) throw new Error('A signing identity is required. Use "-" for ad-hoc signing.');
  try {
    await lstat(output);
    throw new Error(`The launcher output already exists: ${output}`);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const bundle = mac_launcher_bundle();
  const contents = join(output, 'Contents');
  await mkdir(join(contents, 'MacOS'), { recursive: true });
  await writeFile(join(contents, 'Info.plist'), bundle.info_plist, { mode: 0o644 });
  const source = join(dirname(output), 'launcher.c');
  await writeFile(source, bundle.launcher_source, { flag: 'wx', mode: 0o644 });
  const executable = join(contents, 'MacOS/Panels');
  const child_processes = new ChildProcesses();
  const execution = { child_processes, signal: new AbortController().signal, deadline: null };
  const run = (args: string[]) =>
    run_release_command(args, { timeout_seconds: LAUNCHER_COMMAND_SECONDS }, execution);
  try {
    await run(['/usr/bin/clang', '-arch', 'arm64', '-O2', source, '-o', executable]);
    await macho_arm64(executable, true);
    // A team identity gets the hardened runtime and a secure timestamp, so notarization stays possible.
    const team = identity === '-' ? [] : ['--options', 'runtime', '--timestamp'];
    await run(['/usr/bin/codesign', '--force', '--sign', identity, ...team, output]);
    await run(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', output]);
  } finally {
    child_processes.seal();
    await child_processes.settled();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { values } = parseArgs({
      args: process.argv.slice(2),
      options: { output: { type: 'string' }, sign: { type: 'string' } },
      strict: true,
    });
    if (!values.output || !values.sign) throw new Error('Use --output APP --sign IDENTITY');
    await build_mac_launcher(values.output, values.sign);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
