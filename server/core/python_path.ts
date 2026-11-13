/** Pure PosixPath spelling, without expansion, parent cancellation or filesystem reads. */
export function pure_posix_path(value: string): string {
  const prefix =
    value.startsWith('//') && !value.startsWith('///') ? '//' : value.startsWith('/') ? '/' : '';
  return (
    prefix +
      value
        .split('/')
        .filter((part) => part !== '' && part !== '.')
        .join('/') || '.'
  );
}

/** Append one filename with PosixPath spelling, without canceling parent components. */
export function posix_child_path(directory: string, name: string): string {
  const parent = pure_posix_path(directory);
  return pure_posix_path(parent + (parent.endsWith('/') ? '' : '/') + name);
}
