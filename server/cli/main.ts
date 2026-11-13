#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import { realpathSync } from 'node:fs';
import type { Writable } from 'node:stream';
import { commands } from './commands.ts';
import type { CommandResult, Invocation } from './contracts.ts';
import { CliFailure, MissingResponseValue } from './errors.ts';
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
  write_stderr?: Invocation['write_stderr'],
): Promise<CommandResult> {
  let invocation: Invocation | undefined;
  try {
    const parsed = parse(argv, commands, environment);
    if (parsed.help)
      return { exit_code: 0, stdout: render_help(parsed.command, commands), stderr: '' };
    invocation = { ...parsed, environment, stdin, write_stderr };
    switch (parsed.command.path[0]) {
      case 'environment':
      case 'stalls':
      case 'serve':
      case 'restart':
      case 'server-event-loop-tasks':
        return await (await import('./operator_commands.ts')).run_operator_command(invocation);
      case 'goal':
        return await (await import('../goals/cli/index.ts')).run_goal_command(invocation);
      case 'page':
        return await (await import('../managers/page_cli/index.ts')).run_page_command(invocation);
      case 'credential':
        return await (
          await import('../credentials/cli/index.ts')
        ).run_credential_command(invocation);
      case 'measure':
        return await (await import('../measures/cli/index.ts')).run_measure_command(invocation);
      case 'attention':
        return await (await import('../attention/cli/index.ts')).run_attention_command(invocation);
      case 'measure-source':
        return await (
          await import('../measure_sources/cli/index.ts')
        ).run_measure_source_command(invocation);
      case 'preview':
        return await (await import('../previews/cli/index.ts')).run_preview_command(invocation);
      case 'dataset':
        return await (await import('../datasets/cli/index.ts')).run_dataset_command(invocation);
    }
    const { run_work_command } = await import('./work_commands.ts');
    return await run_work_command(invocation);
  } catch (error) {
    if (error instanceof MissingResponseValue && invocation) {
      const { fail_validation } = await import('./http.ts');
      try {
        fail_validation(invocation, error.message);
      } catch (failure) {
        if (failure instanceof CliFailure) return failure.result;
        throw failure;
      }
    }
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
    const result = await run(process.argv.slice(2), process.env, undefined, (text) =>
      write(process.stderr, text),
    );
    try {
      const output = (async () => {
        await write(process.stdout, result.stdout);
        await write(process.stderr, result.stderr);
        result.launch?.output_flushed?.();
      })();
      if (result.launch?.interrupted) await Promise.race([output, result.launch.interrupted]);
      else await output;
      process.exitCode = result.exit_code;
      result.launch?.execute();
    } finally {
      await result.launch?.close();
    }
  } catch (error) {
    process.exitCode = 1;
    if (!broken_pipe(error)) {
      const { OperatorInterrupted } = await import('../environments/command_contracts.ts');
      if (error instanceof OperatorInterrupted && error.signal === 'SIGTERM') {
        try {
          await write(process.stderr, error.stderr_prefix);
        } finally {
          process.kill(process.pid, error.signal);
        }
      } else if (error instanceof OperatorInterrupted) {
        await write(process.stderr, error.stderr_prefix + error.message + '\n');
      } else await write(process.stderr, String(error) + '\n');
    }
  }
}
