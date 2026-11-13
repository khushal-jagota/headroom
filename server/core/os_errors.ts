/** Source-style OSError text for the operator adapters that share this boundary. */
import { getSystemErrorMap } from 'node:util';
import koffi from 'koffi';
import { python_string_repr } from './python_repr.ts';

const os_error_numbers = new Map(
  [...getSystemErrorMap()].map(([number, [name]]) => [name, Math.abs(number)]),
);
let native_strerror: ((number: number) => string) | undefined;
export function python_os_error_message(
  error: Error,
  options: { include_paths?: boolean } = {},
): string {
  if (!('code' in error) || typeof error.code !== 'string') return error.message;
  const known_number = os_error_numbers.get(error.code);
  if (known_number === undefined) return error.message;
  const number =
    'errno' in error && typeof error.errno === 'number' ? Math.abs(error.errno) : known_number;
  native_strerror ??= koffi
    .load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6')
    .func('const char *strerror(int error)');
  let message = `[Errno ${number}] ${native_strerror(number)}`;
  if (options.include_paths !== false && 'path' in error && typeof error.path === 'string') {
    message += `: ${python_string_repr(error.path)}`;
    if ('dest' in error && typeof error.dest === 'string')
      message += ` -> ${python_string_repr(error.dest)}`;
  }
  return message;
}
