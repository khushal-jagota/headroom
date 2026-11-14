/** Socket commands reuse the canonical CLI parser and transport result renderer. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { CliFailure, human_error } from '../../cli/errors.ts';
import { emit, executor_request, json } from '../../cli/http.ts';
import { scalar_text } from '../../cli/text.ts';
import { as_object } from '../protocol.ts';
import { ExecutorValidationError } from '../../executor/protocol_errors.ts';
import { local_status, request } from './ipc_client.ts';

export async function run_executor_command(inv: Invocation): Promise<CommandResult> {
  switch (inv.command.path[1]) {
    case 'run':
      return (await import('./run.ts')).run_executor(inv);
    case 'retry': {
      const value = await executor_request(inv, 'retry', {
        submission_id: inv.values.submission_id,
      });
      return emit(value, !!inv.values.as_json, 'The original submission returned its receipt.');
    }
    case 'health': {
      try {
        return {
          exit_code: 0,
          stdout: json(as_object(await local_status(inv.environment), 'MacLocalStatus')) + '\n',
          stderr: '',
        };
      } catch (error) {
        if (!(error instanceof ExecutorValidationError)) throw error;
        throw new CliFailure(human_error(inv.command, error.message));
      }
    }
    case 'quiesce': {
      const response = await request(
        'quiesce',
        { exit_when_ready: !!inv.values.exit_when_ready },
        inv.environment,
      );
      if (response.error !== null)
        throw new CliFailure(human_error(inv.command, scalar_text(response.error)));
      return { exit_code: 0, stdout: json(response.result) + '\n', stderr: '' };
    }
    case 'permissions':
      return (await import('./permissions.ts')).permissions(inv);
    default:
      throw new Error('Unknown executor command.');
  }
}
