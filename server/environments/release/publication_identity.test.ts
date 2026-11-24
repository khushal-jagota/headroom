/** A PR event can precede its merge commit, but the selected parents must match it. */
import { expect, test, vi } from 'vitest';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { publication_git, require_publication_run, source_run } from './publication_identity.ts';

test('PR provenance binds the selected merge parents when the event merge SHA is absent or stale', async () => {
  const root = await mkdtemp(join(tmpdir(), 'publication-identity-'));
  const owner = new ChildProcesses();
  const execution = {
    child_processes: owner,
    signal: AbortSignal.timeout(20_000),
    deadline: performance.now() / 1000 + 20,
  };
  const git = async (...args: string[]) => (await publication_git(root, args, execution)).trim();
  try {
    await git('init', '-q');
    await git('config', 'user.name', 'Fixture');
    await git('config', 'user.email', 'fixture@example.invalid');
    const tree = await git('hash-object', '-w', '-t', 'tree', '/dev/null');
    const base = await git('commit-tree', tree, '-m', 'base');
    const head = await git('commit-tree', tree, '-p', base, '-m', 'head');
    const merge = await git('commit-tree', tree, '-p', base, '-p', head, '-m', 'merge');
    await git('update-ref', 'HEAD', merge);
    await writeFile(join(root, '.git/shallow'), merge + '\n');
    const event_path = join(root, 'event.json');
    const event = {
      repository: { full_name: 'owner/repo' },
      number: 77,
      pull_request: { base: { sha: base }, head: { sha: head } },
    };
    for (const [name, value] of Object.entries({
      GITHUB_ACTIONS: 'true',
      GITHUB_REPOSITORY: 'owner/repo',
      GITHUB_SHA: merge,
      GITHUB_EVENT_NAME: 'pull_request',
      GITHUB_REF: 'refs/pull/77/merge',
      GITHUB_WORKFLOW_REF: 'owner/repo/.github/workflows/first-install.yml@refs/pull/77/merge',
      GITHUB_EVENT_PATH: event_path,
      GITHUB_RUN_ID: '123',
      GITHUB_RUN_ATTEMPT: '1',
    }))
      vi.stubEnv(name, value);
    await writeFile(event_path, JSON.stringify(event));
    const run = await source_run(merge, root, execution);
    expect(run.source_sha).toBe(merge);
    expect(() => require_publication_run(run)).toThrow('reserved main-push');
    await writeFile(
      event_path,
      JSON.stringify({
        ...event,
        pull_request: { ...event.pull_request, merge_commit_sha: 'f'.repeat(40) },
      }),
    );
    expect((await source_run(merge, root, execution)).source_sha).toBe(merge);

    const wrong_merge = await git('commit-tree', tree, '-p', head, '-p', base, '-m', 'wrong order');
    await git('update-ref', 'HEAD', wrong_merge);
    vi.stubEnv('GITHUB_SHA', wrong_merge);
    await expect(source_run(wrong_merge, root, execution)).rejects.toThrow(
      'selected merge parents',
    );
    vi.stubEnv('GITHUB_REF', 'refs/pull/78/merge');
    await expect(source_run(wrong_merge, root, execution)).rejects.toThrow('selected merge ref');
  } finally {
    vi.unstubAllEnvs();
    owner.seal();
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
});
