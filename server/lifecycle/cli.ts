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
