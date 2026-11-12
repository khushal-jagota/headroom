import type { BackendInstallMethod } from '../contracts.ts';
const normalize = (path: string) => path.replaceAll('\\', '/').toLowerCase();
const MARKERS: readonly (readonly [BackendInstallMethod, readonly string[]])[] = [
  ['bun_global', ['/.bun/bin/']],
  ['pnpm_global', ['/.local/share/pnpm/', '/library/pnpm/', '/local/share/pnpm/', '/pnpm/global/']],
  ['npm_global', ['/node_modules/.bin/', '/lib/node_modules/', '/npm/node_modules/']],
  ['homebrew', ['/opt/homebrew/cellar/', '/usr/local/cellar/', '/homebrew/bin/']],
];
export function classify_install_method(
  candidate_paths: readonly string[],
  options: { native_path_marker: string | null },
): BackendInstallMethod {
  const normalized = candidate_paths.map(normalize);
  if (
    options.native_path_marker !== null &&
    normalized.some((path) => path.includes(normalize(options.native_path_marker!)))
  )
    return 'native';
  for (const [method, markers] of MARKERS) {
    if (normalized.some((path) => markers.some((marker) => path.includes(marker)))) return method;
  }
  return 'manual_only';
}
export function npm_prefix_from_path(path: string): string | null {
  const normalized = path.replaceAll('\\', '/');
  const lowered = normalized.toLowerCase();
  for (const marker of ['/lib/node_modules/', '/npm/node_modules/', '/node_modules/']) {
    const position = lowered.indexOf(marker);
    if (position >= 0) return normalized.slice(0, position) || '/';
  }
  return null;
}
