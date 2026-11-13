/** Operator callbacks have no argv parser or process-output side effects. */
import type { OperatorHandler } from '../environments/command_contracts.ts';
export const server_event_loop_tasks: OperatorHandler<'server-event-loop-tasks'> = async (args) => {
  const message = 'The TypeScript runtime cannot enumerate Promise await chains.';
  return {
    exit_code: 1,
    stdout: '',
    stderr: args.json
      ? '{"error": {"code": "unsupported", "message": "' + message + '", "detail": {}}}\n'
      : 'error: ' + message + '\nThis diagnostic is unavailable in the TypeScript server.\n',
  };
};
export const watchdog: OperatorHandler<'watchdog'> = async (args, deps) => ({
  exit_code: 0,
  stdout: (await deps.run_watchdog(args)) + '\n',
  stderr: '',
});

export const serve: OperatorHandler<'serve'> = async (_args, deps) => {
  try {
    const code = await deps.run_supervisor();
    return code === 0
      ? { exit_code: 0, stdout: '', stderr: '' }
      : {
          exit_code: 1,
          stdout: '',
          failure_message: 'Panels application exited unexpectedly.',
          stderr_prefix: '',
        };
  } catch (error) {
    const { ServerLifecycleError } = await import('./contracts.ts');
    if (!(error instanceof ServerLifecycleError)) throw error;
    return { exit_code: 1, stdout: '', failure_message: error.message, stderr_prefix: '' };
  }
};
export const restart: OperatorHandler<'restart'> = async (_args, deps) => {
  try {
    await deps.request_restart();
    return { exit_code: 0, stdout: 'Panels restart accepted.\n', stderr: '' };
  } catch (error) {
    const { ServerRestartConnectionError, ServerRestartProtocolError } =
      await import('./contracts.ts');
    if (
      !(error instanceof ServerRestartConnectionError) &&
      !(error instanceof ServerRestartProtocolError)
    )
      throw error;
    return { exit_code: 1, stdout: '', failure_message: error.message, stderr_prefix: '' };
  }
};
