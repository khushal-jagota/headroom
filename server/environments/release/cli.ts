/** Typed callbacks only. The CLI owns parsing and error presentation. */
import { posix_child_path } from '../../core/python_path.ts';
import { python_os_error_message } from '../../core/os_errors.ts';
import { domain_error } from '../operator_errors.ts';
import type { OperatorHandler } from '../command_contracts.ts';

const failure = (error: Error) => ({
  exit_code: 1 as const,
  stdout: '' as const,
  failure_message: python_os_error_message(error),
  stderr_prefix: '',
});
export const app_identity: OperatorHandler<'app-identity'> = async (args, deps) => {
  try {
    const manifest = await deps.validate_app_manifest(posix_child_path(args.app, 'manifest.json'));
    return { exit_code: 0, stdout: manifest.app_sha + '\n', stderr: '' };
  } catch (error) {
    if (!domain_error(error, { runtime: false, plain: false })) throw error;
    return failure(error);
  }
};
