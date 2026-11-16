/** A prepared launch retains its listener until the executable consumes or abandons it. */
import type { OperatorHandler } from './command_contracts.ts';
import { EnvironmentValidationError } from './registry/contracts.ts';
export const environment_run: OperatorHandler<'run'> = async (args, deps) => {
  try {
    const prepared = await deps.prepare_run(args);
    return { exit_code: 0, stdout: '', stderr: prepared.stderr, launch: prepared.launch };
  } catch (error) {
    if (!(error instanceof EnvironmentValidationError)) throw error;
    return { exit_code: 1, stdout: '', failure_message: error.message, stderr_prefix: '' };
  }
};
