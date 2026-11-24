import type { DesktopRelease, UpdateLifecycle } from './contracts';

export function desktopRelease(tag: string, draft: boolean, prerelease: boolean): DesktopRelease | null {
  const match = /^desktop-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(tag);
  if (!match || draft || prerelease) return null;
  const version = [Number(match[1]), Number(match[2]), Number(match[3])] as const;
  if (!version.every(Number.isSafeInteger)) return null;
  return { tag, version };
}

export function newestDesktopRelease(releases: readonly DesktopRelease[]): DesktopRelease | null {
  return releases.reduce<DesktopRelease | null>((newest, release) => {
    if (newest === null) return release;
    for (let index = 0; index < 3; index++) {
      const difference = release.version[index]! - newest.version[index]!;
      if (difference !== 0) return difference > 0 ? release : newest;
    }
    return newest;
  }, null);
}

/** null means that an automatic install cannot run while a window exists or before download. */
export function installDelay(state: UpdateLifecycle, now: number, delay: number): number | null {
  if (!state.downloaded || state.noWindowSince === null) return null;
  return Math.max(0, state.noWindowSince + delay - now);
}
