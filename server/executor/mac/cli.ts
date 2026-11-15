/** Socket commands reuse the canonical CLI parser and transport result renderer. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { CliFailure, human_error } from '../../cli/errors.ts';
import { emit, executor_request, json } from '../../cli/http.ts';
import { scalar_text } from '../../cli/text.ts';
import { is_os_error } from '../../core/system_errors.ts';
import { credential_path, read_credential, write_credential } from '../credential_file.ts';
import { as_object } from '../protocol.ts';
import { ExecutorValidationError } from '../../executor/protocol_errors.ts';
import { local_status, request } from './ipc_client.ts';
import { executor_paths } from './configuration.ts';

export async function run_executor_command(inv: Invocation): Promise<CommandResult> {
  switch (inv.command.path[1]) {
    case 'credential': {
      try {
        const credential = await read_credential(inv.values.source as string);
        if (credential === null)
          throw new ExecutorValidationError('The credential file is missing');
        await write_credential(credential_path(executor_paths(inv.environment).root), credential);
      } catch (error) {
        if (
          !(error instanceof ExecutorValidationError) &&
          !(error instanceof SyntaxError) &&
          !(
            error instanceof TypeError &&
            'code' in error &&
            error.code === 'ERR_ENCODING_INVALID_ENCODED_DATA'
          ) &&
          !is_os_error(error)
        )
          throw error;
        const message =
          error instanceof SyntaxError
            ? 'The machine credential file is invalid'
            : (error as Error).message;
        throw new CliFailure(human_error(inv.command, message));
      }
      return { exit_code: 0, stdout: 'Machine credential installed.\n', stderr: '' };
    }
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
