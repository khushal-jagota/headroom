/** New gap: one native start command must own bounded retries and exact failed-child cleanup.
 * Reuse the synthetic native options; no provider, command journal, or listener starts.
 */
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { setTimeout as pause } from 'node:timers/promises';
import {
  BackendInitializationTimeout,
  BackendSpawnFailed,
} from '../../../conversation/backends/contracts.ts';
import { Deferred } from '../../../core/deferred.ts';
import { ExecutorPaths } from '../configuration.ts';
import { native_fixture_options } from '../testing/runner_fixture.ts';
import { MacNativeChildren } from './start_stop.ts';
import { localize_start } from './localization.ts';
import type { StartRun } from '../../wire.generated.ts';

vi.mock('node:timers/promises', async (original) => ({
  ...(await original<typeof import('node:timers/promises')>()),
  setTimeout: vi.fn(async () => undefined),
}));

test('initialization retries wait for exact cleanup, stay bounded and exclude other failures', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-start-retry-'));
  const reference = {
    executor_incarnation: 'inc',
    child_id: 'child',
    conversation_id: 'conversation',
  };
  const input: StartRun = {
    canonical_skill_snapshot: { required_skill_names: [], sources: [] },
    resolved_start: {
      conversation_id: reference.conversation_id,
      backend_key: 'claude',
      model: 'haiku',
      reasoning_effort: null,
      access: 'full',
      workspace_folder: root,
      workspace_path_policy: 'absolute',
      role_materials: {
        role_text: '',
        identity_environment_variables: [['PLAN_TICKET_ID', 't_one']],
      },
    },
    vendor_session_cursor: 'retained-session',
  };
  const unexpected = async (): Promise<never> => {
    throw new Error('No prompt belongs to native start');
  };
  try {
    for (const mode of ['recover', 'exhaust', 'other'] as const) {
      vi.mocked(pause).mockClear();
      const events: string[] = [];
      let attempts = 0;
      const cleanup_started = new Deferred<void>();
      const cleanup_release = new Deferred<void>();
      const owner = new MacNativeChildren({
        ...native_fixture_options({
          paths: new ExecutorPaths(join(root, mode)),
          workspace_home: root,
          native_skill_home: () => ({ path: root, skills_directory: 'skills' }),
          snapshot_directory: root,
          on_failure: (error) => {
            throw error;
          },
          factory: () => () => {
            const attempt = ++attempts;
            return {
              stop_outside_lock: false,
              withdraw_delivery: unexpected,
              detach: unexpected,
              write_prompt: unexpected,
              steer: unexpected,
              cancel_running_turn: unexpected,
              answer_permission_ask: unexpected,
              answer_user_input: unexpected,
              async start(_resolved, options) {
                expect(options.vendor_session_cursor).toBe('retained-session');
                events.push(`start${attempt}`);
                if (mode === 'other') throw new BackendSpawnFailed('ordinary startup failure');
                if (mode === 'exhaust' || attempt < 3)
                  throw new BackendInitializationTimeout('initialize');
              },
              async stop() {
                events.push(`stop${attempt}`);
                if (attempt === 1) {
                  cleanup_started.resolve();
                  await cleanup_release.promise;
                }
              },
            };
          },
        }),
        event_sink: () => ({ async report() {} }),
      });
      const starting = owner.start(reference, input, () => {});
      const outcome = starting.then(
        () => null,
        (error) => error,
      );
      await cleanup_started.promise;
      expect(attempts).toBe(1);
      expect(vi.mocked(pause)).not.toHaveBeenCalled();
      cleanup_release.resolve();
      const error = await outcome;
      if (mode === 'recover') expect(error).toBeNull();
      else
        expect(error).toBeInstanceOf(
          mode === 'other' ? BackendSpawnFailed : BackendInitializationTimeout,
        );
      expect(attempts).toBe(mode === 'other' ? 1 : 3);
      expect(vi.mocked(pause).mock.calls.map((call) => call[0])).toEqual(
        mode === 'other' ? [] : [5000, 5000],
      );
      expect(events.slice(0, 4)).toEqual(
        mode === 'other' ? ['start1', 'stop1'] : ['start1', 'stop1', 'start2', 'stop2'],
      );
      await owner.stop({ deadline: performance.now() / 1000 + 2 });
      await owner.settled();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

// Gap: native retry coverage uses an unmanaged folder and does not prove managed launch localization.
// Reuse the synthetic native options without a provider, listener, or Git child.
test('managed Mac launch starts in the Ticket folder and names its prepared checkout', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-ticket-launch-'));
  try {
    const paths = new ExecutorPaths(root);
    const reference = {
      executor_incarnation: 'inc',
      child_id: 'child',
      conversation_id: 'conversation',
    };
    const options = native_fixture_options({
      paths,
      workspace_home: root,
      native_skill_home: () => ({ path: root, skills_directory: 'skills' }),
      snapshot_directory: root,
      factory: () => () => {
        throw new Error('No native process belongs to localization');
      },
      on_failure: (error) => {
        throw error;
      },
    });
    const localized = await localize_start(
      {
        canonical_skill_snapshot: { required_skill_names: [], sources: [] },
        resolved_start: {
          conversation_id: reference.conversation_id,
          backend_key: 'codex',
          model: 'test',
          reasoning_effort: null,
          access: 'full',
          workspace_folder: '/',
          workspace_path_policy: 'executor_default',
          role_materials: {
            role_text: '',
            identity_environment_variables: [
              ['PLAN_TICKET_ID', 't_one'],
              ['PLAN_TICKET_WORKSPACE_MANAGED', '1'],
            ],
          },
        },
      },
      reference,
      'capability',
      root,
      { ...options, event_sink: () => ({ async report() {} }) },
    );
    const folder = join(paths.artifacts, 't_one');
    expect(localized.workspace_folder).toBe(folder);
    expect((await stat(join(folder, 'worktree'))).isDirectory()).toBe(true);
    expect(localized.role_materials!.role_text).toContain(
      `Use the supplied worktree at ${join(folder, 'worktree')}.`,
    );
    expect(localized.role_materials!.role_text).toContain(
      `Write reviewable artifacts and evidence under ${join(folder, 'artifacts')}.`,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
