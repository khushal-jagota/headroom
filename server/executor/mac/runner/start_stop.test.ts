/** New gap: one native start command must own bounded retries and exact failed-child cleanup.
 * Reuse the synthetic native options; no provider, command journal, or listener starts.
 */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readlinkSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { setTimeout as pause } from 'node:timers/promises';
import {
  BackendInitializationTimeout,
  BackendSpawnFailed,
} from '../../../conversation/backends/contracts.ts';
import { git } from '../../../ticket_workspaces/filesystem/shared.ts';
import { Deferred } from '../../../core/deferred.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { WorkerScratch } from '../../../conversation/worker_scratch.ts';
import { ExecutorPaths } from '../configuration.ts';
import { native_fixture_options } from '../testing/runner_fixture.ts';
import { MacNativeChildren } from './start_stop.ts';
import { localize_start } from './localization.ts';
import { agent_home_text } from '../../../agent_homes/agent_home.ts';
import type { StartRun } from '../../wire.generated.ts';
import { worker_ticket_id } from '../../../worker_supervision/logic/worker_launch.ts';

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
      const setup_reports: string[] = [];
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
        // Every attempt declares its setup steps to the home server, in order.
        async report_worker_setup(child, step) {
          expect(child).toEqual(reference);
          setup_reports.push(step);
        },
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
      expect(setup_reports).toEqual(
        Array.from({ length: attempts }, () => ['preparing_workspace', 'starting_worker']).flat(),
      );
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

// Gap: on 22 Nov a scratch folder that another process kept writing failed a Worker stop, and
// the runner faulted and stopped every Mac Worker. Reuse the synthetic native options; the
// fixture backend stops through a real WorkerScratch whose folder cannot be deleted.
test('a Worker stop whose scratch folder cannot be deleted does not fault the Mac runner', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-busy-scratch-'));
  const processes = new ChildProcesses();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const failures: Error[] = [];
  const reference = { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'c' };
  const scratch = await WorkerScratch.create(
    { PLAN_TICKET_ID: 't_one' },
    { home: root, processes },
  );
  const locked = join(scratch.directory, 'build');
  await mkdir(locked);
  await writeFile(join(locked, 'output.log'), 'busy');
  await chmod(locked, 0o500);
  const unexpected = async (): Promise<never> => {
    throw new Error('No prompt belongs to this stop');
  };
  try {
    const owner = new MacNativeChildren({
      ...native_fixture_options({
        paths: new ExecutorPaths(join(root, 'executor')),
        workspace_home: root,
        native_skill_home: () => ({ path: root, skills_directory: 'skills' }),
        snapshot_directory: root,
        on_failure: (error) => failures.push(error),
        factory: () => () => ({
          stop_outside_lock: false,
          withdraw_delivery: unexpected,
          detach: unexpected,
          write_prompt: unexpected,
          steer: unexpected,
          cancel_running_turn: unexpected,
          answer_permission_ask: unexpected,
          answer_user_input: unexpected,
          async start() {},
          stop: () => scratch.close(),
        }),
      }),
      event_sink: () => ({ async report() {} }),
      async report_worker_setup() {},
    });
    await owner.start(
      reference,
      {
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
      },
      () => {},
    );
    await owner.stop_child(reference, () => {});
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('Worker scratch folder stays in place'),
    );
    await owner.stop({ deadline: performance.now() / 1000 + 2 });
    await owner.settled();
    expect(failures).toEqual([]);
  } finally {
    await chmod(locked, 0o700);
    warn.mockRestore();
    processes.seal();
    await processes.settled();
    await rm(root, { recursive: true, force: true });
  }
}, 15_000);

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

// Gap: project-less localization never proves remote repository cloning or refusal when repository metadata is missing.
// Reuse the local runner fixture and a bare temporary Git remote without a provider or listener.
test('managed remote launch clones workspace_repository and refuses absent project repository metadata', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-ticket-repo-'));
  const children = new ChildProcesses();
  try {
    const remote = join(root, 'origin.git');
    const source = join(root, 'source');
    mkdirSync(remote);
    mkdirSync(source);
    for (const [cwd, args] of [
      [remote, ['init', '--bare']],
      [remote, ['symbolic-ref', 'HEAD', 'refs/heads/main']],
      [source, ['init', '-b', 'main']],
      [source, ['config', 'user.email', 'proof@example.test']],
      [source, ['config', 'user.name', 'Synthetic proof']],
    ] as const) {
      const result = await git(cwd, [...args]);
      expect(result.returncode, result.stderr).toBe(0);
    }
    writeFileSync(join(source, 'README.md'), 'hello\n');
    for (const args of [
      ['add', '.'],
      ['commit', '-m', 'init'],
      ['remote', 'add', 'origin', remote],
      ['push', 'origin', 'main'],
    ]) {
      const result = await git(source, args);
      expect(result.returncode, result.stderr).toBe(0);
    }
    const paths = new ExecutorPaths(join(root, 'exec'));
    const reference = {
      executor_incarnation: 'inc',
      child_id: 'child',
      conversation_id: 'conversation',
    };
    const options = {
      ...native_fixture_options({
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
      }),
      workspace_git: {
        children: {
          signal: new AbortController().signal,
          spawn: children.spawn.bind(children),
          seal: children.seal.bind(children),
          settled: children.settled.bind(children),
        },
        environment: process.env,
      },
      event_sink: () => ({ async report() {} }),
    };
    const missing_repo_start: StartRun = {
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
            ['PLAN_TICKET_ID', 't_missing1'],
            ['PLAN_TICKET_WORKSPACE_MANAGED', '1'],
            ['PLAN_TICKET_PROJECT_ID', 'project_wren'],
          ],
        },
      },
    };
    await expect(
      localize_start(missing_repo_start, reference, 'capability', root, options),
    ).rejects.toThrow(BackendSpawnFailed);
    expect(existsSync(join(paths.artifacts, 't_missing1', 'worktree'))).toBe(false);

    const cloned = await localize_start(
      {
        ...missing_repo_start,
        workspace_repository: { repository_url: remote, default_branch: 'main' },
        resolved_start: {
          ...missing_repo_start.resolved_start,
          role_materials: {
            role_text: '',
            identity_environment_variables: [
              ['PLAN_TICKET_ID', 't_cloned01'],
              ['PLAN_TICKET_WORKSPACE_MANAGED', '1'],
              ['PLAN_TICKET_PROJECT_ID', 'project_wren'],
            ],
          },
        },
      },
      reference,
      'capability',
      root,
      options,
    );
    const worktree = join(paths.artifacts, 't_cloned01', 'worktree');
    expect(cloned.workspace_folder).toBe(join(paths.artifacts, 't_cloned01'));
    expect(existsSync(join(worktree, 'README.md'))).toBe(true);
    expect(existsSync(join(paths.workspace, 'projects'))).toBe(false);
  } finally {
    children.seal();
    await children.settled();
    await rm(root, { recursive: true, force: true });
  }
});

// Gap: every earlier start carries a Ticket identity. A Chief or Manager start must find its own
// home, on the server's folders or the machine's, without any Ticket step or Worker setting.
// Reuse the synthetic native options without a provider, listener, or Git child.
test('the Chief and a Manager start in their own homes, with no Ticket step or Worker identity', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-supervisor-launch-'));
  try {
    const reference = {
      executor_incarnation: 'inc',
      child_id: 'child',
      conversation_id: 'conversation',
    };
    const start = (identity: [string, string][]): StartRun => ({
      canonical_skill_snapshot: { required_skill_names: [], sources: [] },
      resolved_start: {
        conversation_id: reference.conversation_id,
        backend_key: 'claude',
        model: 'test',
        reasoning_effort: null,
        access: 'full',
        // The server's own path, which another machine does not have.
        workspace_folder: '/server/data/files/chief',
        workspace_path_policy: 'home_relative',
        role_materials: { role_text: 'role', identity_environment_variables: identity },
      },
      workspace_home_relative: 'server/data/files/chief',
    });
    const localize = (paths: ExecutorPaths, input: StartRun) => {
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
      return localize_start(input, reference, 'capability', root, {
        ...options,
        event_sink: () => ({ async report() {} }),
      }).then((localized) => ({ localized }));
    };
    const manager = start([
      ['PLAN_ACTOR', 'manager'],
      ['PLAN_MANAGER_ID', 'm_one'],
    ]);

    // Another machine keeps its own folders and writes the Manager's home files itself.
    const own = new ExecutorPaths(join(root, 'machine'));
    const { localized: remote } = await localize(own, manager);
    const home = join(root, 'machine', 'managers', 'm_one');
    expect(remote.workspace_folder).toBe(home);
    expect((await stat(join(home, 'artifacts'))).isDirectory()).toBe(true);
    expect((await stat(join(home, 'logs'))).isDirectory()).toBe(true);
    expect(existsSync(join(home, 'CLAUDE.md'))).toBe(true);
    expect(remote.role_materials!.role_text).toBe(
      `role\nThe repositories on this machine are under ${root}.`,
    );
    const environment = new Map(remote.role_materials!.identity_environment_variables);
    expect(environment.has('PLAN_TICKET_FILES_DIR')).toBe(false);
    expect(environment.get('PLAN_MANAGER_ID')).toBe('m_one');
    // Worker priority, scratch and per-Ticket process records follow this identity.
    expect(worker_ticket_id(remote)).toBeNull();

    // Machine code also prepares the Manager home on the local link.
    const served = join(root, 'data', 'files');
    const shared = new ExecutorPaths(join(root, 'shared'), {
      tickets: join(served, 'tickets'),
      chief: join(served, 'chief'),
      managers: join(served, 'sprint-items'),
    });
    const served_home = join(served, 'sprint-items', 'm_one');
    mkdirSync(served_home, { recursive: true });
    const { localized: local } = await localize(shared, manager);
    expect(local.workspace_folder).toBe(served_home);
    expect((await stat(join(served_home, 'artifacts'))).isDirectory()).toBe(true);
    expect(existsSync(join(served_home, 'logs'))).toBe(true);
    expect(await readFile(join(served_home, 'CLAUDE.md'), 'utf8')).toContain(
      'You are a Manager of one domain',
    );
    expect(existsSync(join(served_home, 'rules'))).toBe(true);
    // A fresh install: the first Chief starts with no home, and Panels sets one up.
    const chief_home = join(served, 'chief');
    const chief_start = start([['PLAN_ACTOR', 'chief']]);
    const { localized: chief } = await localize(shared, chief_start);
    expect(chief.workspace_folder).toBe(chief_home);
    expect(readdirSync(chief_home).sort()).toEqual(['AGENTS.md', 'CLAUDE.md', 'logs', 'rules']);
    for (const name of ['CLAUDE.md', 'AGENTS.md'])
      expect(await readFile(join(chief_home, name), 'utf8')).toBe(agent_home_text('chief'));
    // The home is the Chief's after that. A next start writes nothing, also through the link
    // from AGENTS.md to CLAUDE.md that a Chief can choose.
    writeFileSync(join(chief_home, 'CLAUDE.md'), 'my own router');
    rmSync(join(chief_home, 'AGENTS.md'));
    symlinkSync('CLAUDE.md', join(chief_home, 'AGENTS.md'));
    await localize(shared, chief_start);
    expect(await readFile(join(chief_home, 'CLAUDE.md'), 'utf8')).toBe('my own router');
    expect(readlinkSync(join(chief_home, 'AGENTS.md'))).toBe('CLAUDE.md');
    expect(readdirSync(chief_home).sort()).toEqual(['AGENTS.md', 'CLAUDE.md', 'logs', 'rules']);
    expect(existsSync(join(served, 'tickets'))).toBe(false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
