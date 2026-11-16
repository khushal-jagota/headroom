import { getSystemErrorMap } from 'node:util';

const os_error_codes = new Set([...getSystemErrorMap().values()].map(([name]) => name));

/** Node exposes operating-system failures through its documented errno code set. */
export function is_os_error(error: unknown): boolean {
  return (
    error instanceof Error &&
    'code' in error &&
    typeof error.code === 'string' &&
    os_error_codes.has(error.code)
  );
}
