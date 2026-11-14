/** Gap: private review fixtures do not prove committed authorship or job-edit refusal. */
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { publication_git } from '../environments/release/publication_identity.ts';
import { check_head_review, contract_digest, REVIEW_PATH } from './check.ts';
import { MINIMUM_SUPPORTED_BUILD } from './policy.ts';

test('publication accepts the committed reviewer and refuses a job-edited review', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-ios-head-'));
  const owner = new ChildProcesses();
  const execution = {
    child_processes: owner,
    signal: new AbortController().signal,
    deadline: performance.now() / 1000 + 15,
  };
  try {
    for (const name of [
      'server/package.json',
      'server/package-lock.json',
      'server/core/database/application_schema.json',
      'server/skills/cli_commands.json',
      'server/environments/release/migration_graph.json',
      'server/goals/cli/guidance.json',
      'server/managers/page_cli/guidance.json',
      'server/executor/wire.schema.json',
      'src/planner/conversation/backends/hermes_acp_extension.py',
      'src/planner/conversation/backends/hermes_model_catalog.py',
    ]) {
      await mkdir(dirname(join(root, name)), { recursive: true });
      await writeFile(join(root, name), '{}\n');
    }
    const review = {
      contract_sha256: await contract_digest(root),
      oldest_supported_build: MINIMUM_SUPPORTED_BUILD,
      decision: 'compatible',
      evidence: 'Private committed-review fixture only, without publication or device approval.',
    };
    await mkdir(dirname(join(root, REVIEW_PATH)), { recursive: true });
    await writeFile(join(root, REVIEW_PATH), JSON.stringify(review) + '\n');
    await publication_git(root, ['init', '--template='], execution);
    await publication_git(root, ['add', '.'], execution);
    await publication_git(
      root,
      [
        '-c',
        'user.name=Fixture Reviewer',
        '-c',
        'user.email=fixture-reviewer@example.invalid',
        'commit',
        '-m',
        'Private iOS review fixture',
      ],
      execution,
    );
    const head = (await publication_git(root, ['rev-parse', 'HEAD'], execution)).trim();
    expect(await check_head_review(root, head, execution)).toEqual({
      ...review,
      review_commit: head,
      reviewer: { name: 'Fixture Reviewer', email: 'fixture-reviewer@example.invalid' },
    });
    await writeFile(
      join(root, REVIEW_PATH),
      JSON.stringify({
        ...review,
        evidence: 'A job-generated compatible verdict must never authorize publication.',
      }) + '\n',
    );
    await expect(check_head_review(root, head, execution)).rejects.toThrow('clean working tree');
  } finally {
    owner.seal();
    try {
      owner.force_kill(performance.now() / 1000);
    } finally {
      await owner.settled();
      await rm(root, { recursive: true, force: true });
    }
  }
}, 15_000);
