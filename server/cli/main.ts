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
    const invocation = { ...parsed, environment, stdin };
    switch (parsed.command.path[0]) {
      case 'goal':
        return await (await import('../goals/cli/index.ts')).run_goal_command(invocation);
      case 'page':
        return await (await import('../managers/page_cli/index.ts')).run_page_command(invocation);
      case 'credential':
        return await (await import('../credentials/cli/index.ts')).run_credential_command(invocation);
      case 'measure':
        return await (await import('../measures/cli/index.ts')).run_measure_command(invocation);
      case 'attention':
        return await (await import('../attention/cli/index.ts')).run_attention_command(invocation);
      case 'measure-source':
        return await (await import('../measure_sources/cli/index.ts')).run_measure_source_command(invocation);
      case 'preview':
        return await (await import('../previews/cli/index.ts')).run_preview_command(invocation);
      case 'dataset':
        return await (await import('../datasets/cli/index.ts')).run_dataset_command(invocation);
    }
    const { run_work_command } = await import('./work_commands.ts');
    return await run_work_command(invocation);
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
