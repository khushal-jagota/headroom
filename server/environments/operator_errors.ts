/** The operator adapters share Python's operational exception boundary. */
import { is_os_error } from '../core/system_errors.ts';
import { AppManifestValidationFailure } from './command_contracts.ts';

export function os_error(error: unknown): boolean {
  return (
    error instanceof Error &&
    (is_os_error(error) || ['OSError', 'FileNotFoundError'].includes(error.name))
  );
}
export function domain_error(
  error: unknown,
  options: { runtime?: boolean; plain?: boolean } = {},
): error is Error {
  if (!(error instanceof Error)) return false;
  if (os_error(error)) return true;
  if (error instanceof AppManifestValidationFailure)
    return options.runtime !== false || error.category !== 'runtime';
  return (
    error.name === 'ValueError' ||
    (options.runtime !== false && error.name === 'RuntimeError') ||
    (options.plain !== false && error.constructor === Error)
  );
}
