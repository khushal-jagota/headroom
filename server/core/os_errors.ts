/** Native error text for adapters that still share this entry point. */
export function python_os_error_message(
  error: Error,
  _options: { include_paths?: boolean } = {},
): string {
  return error.message;
}
