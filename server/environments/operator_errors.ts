/** Operational errors that environment commands report to the caller. */
import { is_os_error } from '../core/system_errors.ts';
import { AppManifestValidationFailure } from './command_contracts.ts';

export function domain_error(
  error: unknown,
  options: { runtime?: boolean; plain?: boolean } = {},
): error is Error {
  if (!(error instanceof Error)) return false;
  if (is_os_error(error)) return true;
  if (error instanceof AppManifestValidationFailure)
    return options.runtime !== false || error.category !== 'runtime';
  return error instanceof TypeError || (options.plain !== false && error.constructor === Error);
}
