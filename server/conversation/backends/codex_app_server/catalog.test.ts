import { afterEach, describe, expect, it, vi } from 'vitest';
import { CatalogCoordinator, catalog_snapshot } from './catalog.ts';
import * as P from './protocol.ts';
import { CodexRequestRejected } from './errors.ts';
import { CodexAuthReading, error_summary_for, LOGIN_IS_REJECTED_SUMMARY, read_a_failed_turn } from './auth_failure.ts';

afterEach(() => vi.restoreAllMocks());
const skills = P.SkillsListResponse.read({ data: [] });
const plugins = P.PluginInstalledResponse.read({ marketplaces: [] });
const installed = (ids: string[]) => P.AppsInstalledResponse.read({ apps: ids.map((id) => ({ id, enabled: true, callable: true, runtimeName: id })) });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

describe('Codex child catalog', () => {
  it('caps retries, retains independent hints, and recovers a failed publication without secret logs', async () => {
    vi.useFakeTimers();
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
    let now = 1000;
    let available = false;
    let source_calls = 0;
    let skills_calls = 0;
    let publication_fails = true;
    const coordinator = new CatalogCoordinator({
      clock: () => now,
      read_skills: async () => { skills_calls += 1; return skills; },
      read_plugins: async () => plugins,
      read_installed: async () => {
        source_calls += 1;
        if (!available) throw new CodexRequestRejected('app/installed', -32000, 'secret upstream response');
        return installed([]);
      },
      read_metadata: async () => ({ apps: [] }),
      publish: async () => {
        if (available && publication_fails) throw new Error('secret sink body');
      },
    });
    try {
      coordinator.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(source_calls).toBe(1);
      coordinator.hint_skills();
      for (const delay of [5, 10, 20, 40, 80, 160, 300, 300]) {
        for (let count = 0; count < 30; count += 1) coordinator.hint_apps();
        now += delay - 0.01;
        coordinator.wake();
        await vi.advanceTimersByTimeAsync(0);
        const before = source_calls;
        now += 0.01;
        coordinator.wake();
        await vi.advanceTimersByTimeAsync(0);
        expect(source_calls).toBe(before + 1);
      }
      expect(skills_calls).toBe(2);
      available = true;
      now += 300;
      coordinator.wake();
      await vi.advanceTimersByTimeAsync(0);
      expect(coordinator.installed).toEqual({ apps: [] });
      const before_recovery = source_calls;
      publication_fails = false;
      now += 300;
      coordinator.wake();
      await vi.advanceTimersByTimeAsync(0);
      expect(source_calls).toBe(before_recovery + 1);
      expect(warning.mock.calls.flat().every((line) => typeof line === 'string' && line.length <= 240 && !line.includes('secret'))).toBe(true);
    } finally {
      await coordinator.close();
      expect(vi.getTimerCount()).toBe(0);
      vi.useRealTimers();
    }
  });

  it('batches metadata, caches missing labels until expiry, and rejects late revoked metadata on close', async () => {
    vi.useFakeTimers();
    let now = 1000;
    let ids = Array.from({ length: 101 }, (_, index) => `app-${String(index).padStart(3, '0')}`);
    const batches: string[][] = [];
    const late = deferred<P.AppsReadResponse>();
    let hold = false;
    const coordinator = new CatalogCoordinator({
      clock: () => now,
      read_skills: async () => skills,
      read_plugins: async () => plugins,
      read_installed: async () => installed(ids),
      read_metadata: async (batch) => {
        batches.push(batch);
        return hold ? late.promise : { apps: [] };
      },
      publish: async () => {},
    });
    try {
      coordinator.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(batches.map((batch) => batch.length)).toEqual([100, 1]);
      expect(coordinator.metadata.size).toBe(101);
      now += 901;
      coordinator.wake();
      await vi.advanceTimersByTimeAsync(0);
      expect(batches).toHaveLength(2);
      hold = true;
      coordinator.hint_apps();
      now += 0.05;
      coordinator.wake();
      await vi.advanceTimersByTimeAsync(0);
      expect(batches).toHaveLength(3);
      ids = [];
      coordinator.hint_apps();
      now += 1;
      coordinator.wake();
      await vi.advanceTimersByTimeAsync(0);
      expect(coordinator.installed?.apps).toEqual([]);
      expect(coordinator.metadata.size).toBe(0);
      await coordinator.close();
      late.resolve({ apps: [{ id: 'app-000', name: 'Too late', description: null }] });
      await vi.advanceTimersByTimeAsync(0);
      expect(coordinator.metadata.size).toBe(0);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      await coordinator.close();
      vi.useRealTimers();
    }
  });

  it('keeps collision aliases deterministic and executable without disabled source entries', () => {
    const app_data = installed(['one', 'two']);
    const metadata = [{ id: 'one', name: 'Straße', description: null }, { id: 'two', name: 'STRASSE', description: null }];
    const snapshot = catalog_snapshot(skills, app_data, metadata, plugins);
    const apps = snapshot.entries.filter((entry) => entry.kind === 'app');
    expect(apps).toHaveLength(2);
    expect(new Set(apps.map((entry) => entry.insertion_text)).size).toBe(2);
    expect(apps.every((entry) => /^@strasse~[0-9a-f]{8} $/.test(entry.insertion_text))).toBe(true);
    expect(apps.map((entry) => snapshot.resolve(entry.insertion_text.trim())?.path).sort()).toEqual(['app://one', 'app://two']);
    expect(snapshot.resolve('@strasse')).toBeNull();
    expect(catalog_snapshot(skills, installed(['two', 'one']), metadata, plugins).entries).toEqual(snapshot.entries);
    expect(catalog_snapshot(skills, null, metadata, plugins).entries.filter((entry) => entry.kind === 'app')).toEqual([]);
  });
});

it('distinguishes Codex auth evidence from tools and preserves authorization uncertainty', () => {
  const read = (error: string | null, stderr: string | null) => read_a_failed_turn({ error_summary: error, standard_error_this_turn: stderr });
  expect(read('routing failed', 'github auth: not logged in\ncodex_login::auth: refresh token was already used')).toBe(CodexAuthReading.login_is_rejected);
  expect(read('routing failed', 'github auth: not logged in')).toBe(CodexAuthReading.says_nothing_about_the_login);
  expect(read('1403ms 401Z é401é', 'écodex_login::auth: not logged in')).toBe(CodexAuthReading.says_nothing_about_the_login);
  expect(read('HTTP 401', null)).toBe(CodexAuthReading.authorization_was_refused);
  expect(error_summary_for(read('HTTP 401', null), 'HTTP 401')).toBe('HTTP 401 — the request was refused; the Codex sign-in may need attention');
  expect(error_summary_for(read('invalid_grant', null), 'invalid_grant')).toBe(LOGIN_IS_REJECTED_SUMMARY);
});
