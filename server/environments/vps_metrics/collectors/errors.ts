/** Python exception names are part of unavailable measurement evidence. */
export function evidence_error(name: string, message: string): Error {
  return Object.assign(new Error(message), { name });
}
export function evidence_reason(error: unknown): string {
  if (error instanceof Error && 'code' in error) {
    const names: Record<string, string> = {
      ENOENT: 'FileNotFoundError',
      EACCES: 'PermissionError',
      EPERM: 'PermissionError',
      ENOTDIR: 'NotADirectoryError',
      EISDIR: 'IsADirectoryError',
      EEXIST: 'FileExistsError',
      ETIMEDOUT: 'TimeoutError',
    };
    return names[String(error.code)] ?? 'OSError';
  }
  if (error instanceof Error) return error.name;
  throw error;
}
export function source_error(error: unknown, allowed: readonly string[]): string {
  const name = evidence_reason(error);
  const os = [
    'OSError',
    'FileNotFoundError',
    'PermissionError',
    'NotADirectoryError',
    'IsADirectoryError',
    'FileExistsError',
    'TimeoutError',
  ];
  if (allowed.includes(name) || (allowed.includes('OSError') && os.includes(name))) return name;
  throw error;
}
