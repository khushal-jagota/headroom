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
