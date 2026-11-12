#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
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
    const parsed = parse(argv, commands);
    if (parsed.help)
      return { exit_code: 0, stdout: render_help(parsed.command, commands), stderr: '' };
    const { run_work_command } = await import('./work_commands.ts');
    return await run_work_command({ ...parsed, environment, stdin });
  } catch (error) {
    if (error instanceof CliFailure) return error.result;
    throw error;
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const result = await run(process.argv.slice(2));
    process.stdout.write(result.stdout);
    process.stderr.write(result.stderr);
    process.exitCode = result.exit_code;
  } catch (error) {
    process.stderr.write(String(error) + '\n');
    process.exitCode = 1;
  }
}
