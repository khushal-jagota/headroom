/** Typed callbacks only. The CLI owns parsing and error presentation. */
import { join } from 'node:path';
import { getSystemErrorMap } from 'node:util';
import { python_os_error_message } from '../../core/os_errors.ts';
import { AppManifestValidationFailure } from '../command_contracts.ts';
import type { OperatorHandler } from '../command_contracts.ts';

const os_codes = new Set([...getSystemErrorMap().values()].map(([name]) => name));
function expected(error: unknown, runtime: boolean): error is Error {
  return (
    error instanceof Error &&
    ((error instanceof AppManifestValidationFailure && (runtime || error.category !== 'runtime')) ||
      ('code' in error && typeof error.code === 'string' && os_codes.has(error.code)) ||
      ['OSError', 'FileNotFoundError', 'ValueError', ...(runtime ? ['RuntimeError'] : [])].includes(
        error.name,
      ))
  );
}
const failure = (error: Error) => ({
  exit_code: 1 as const,
  stdout: '' as const,
  failure_message: python_os_error_message(error),
  stderr_prefix: '',
});
export const app_identity: OperatorHandler<'app-identity'> = async (args, deps) => {
  try {
    const manifest = await deps.validate_app_manifest(join(args.app, 'manifest.json'));
    return { exit_code: 0, stdout: manifest.app_sha + '\n', stderr: '' };
  } catch (error) {
    if (!expected(error, false)) throw error;
    return failure(error);
  }
};
