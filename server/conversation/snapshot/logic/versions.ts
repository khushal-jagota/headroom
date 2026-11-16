const VERSION_PATTERN = /(?<![\d.])v?(\d+\.\d+\.\d+)(?![\d.])/;
export function parse_version(output: string): string | null {
  return VERSION_PATTERN.exec(output)?.[1] ?? null;
}
export function version_is_newer(latest: string, installed: string): boolean {
  const parts = (value: string) => value.split('.').map(Number);
  if (!/^\d+(?:\.\d+)*$/.test(latest) || !/^\d+(?:\.\d+)*$/.test(installed))
    return latest !== installed;
  const first = parts(latest);
  const second = parts(installed);
  for (let index = 0; index < Math.max(first.length, second.length); index++) {
    if (first[index] === undefined) return false;
    if (second[index] === undefined) return true;
    if (first[index] !== second[index]) return first[index] > second[index];
  }
  return false;
}
