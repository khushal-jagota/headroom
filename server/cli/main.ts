#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import { realpathSync } from 'node:fs';
import type { Writable } from 'node:stream';
import { commands } from './commands.ts';
import type { CommandResult } from './contracts.ts';
import { CliFailure } from './errors.ts';
import { render_help } from './help.ts';
import { parse } from './parser.ts';

export async function run(
  argv: string[],
  environment: NodeJS.ProcessEnv = process.env,
  stdin: () => Promise<Buffer> = async () => {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks);
  },
): Promise<CommandResult> {
  try {
    const parsed = parse(argv, commands, environment);
    if (parsed.help)
      return { exit_code: 0, stdout: render_help(parsed.command, commands), stderr: '' };
    const { run_work_command } = await import('./work_commands.ts');
    return await run_work_command({ ...parsed, environment, stdin });
  } catch (error) {
    if (error instanceof CliFailure) return error.result;
    throw error;
  }
}
function is_entrypoint(): boolean {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}
function broken_pipe(error: unknown): boolean {
  return error instanceof Error && (error as NodeJS.ErrnoException).code === 'EPIPE';
}
async function write(stream: Writable, text: string): Promise<void> {
  if (!text) return;
  await new Promise<void>((resolve, reject) =>
    stream.write(text, (error) => (error ? reject(error) : resolve())),
  );
}
if (is_entrypoint()) {
  // Node emits stream errors asynchronously as well as invoking the write callback.
  const stream_error = (error: Error) => {
    if (broken_pipe(error)) process.exitCode = 1;
    else throw error;
  };
  process.stdout.on('error', stream_error);
  process.stderr.on('error', stream_error);
  try {
    const result = await run(process.argv.slice(2));
    await write(process.stdout, result.stdout);
    await write(process.stderr, result.stderr);
    process.exitCode = result.exit_code;
  } catch (error) {
    process.exitCode = 1;
    if (!broken_pipe(error)) await write(process.stderr, String(error) + '\n');
  }
}
