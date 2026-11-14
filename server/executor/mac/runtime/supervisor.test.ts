/** One scripted failed trial must restore the baseline with the same handoff. */
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ExecutorPaths } from '../configuration.ts';
import { FilesService } from '../files.ts';
import { atomic_record } from './records.ts';
import { supervise } from './supervisor.ts';

test('an unconfirmed runtime restores the baseline and preserves planned custody', async () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-supervisor-')));
  const baseline = join(root, 'baseline'),
    runtime = join(root, 'candidate');
  const base_revision = 'a'.repeat(40),
    next_revision = 'b'.repeat(40);
  mkdirSync(join(baseline, 'server/executor/mac'), { recursive: true });
  mkdirSync(join(runtime, 'node/bin'), { recursive: true });
  mkdirSync(join(runtime, 'server/executor/mac'), { recursive: true });
  writeFileSync(join(baseline, 'package.json'), '{"type":"module"}');
  writeFileSync(
    join(runtime, 'mac-runtime.json'),
    JSON.stringify({
      schema_version: 1,
      application_revision: next_revision,
      platform: 'darwin',
      architecture: 'arm64',
    }),
  );
  writeFileSync(
    join(runtime, 'node/bin/node'),
    `#!/bin/sh
printf '%s\\n' "$PATH" "$PLAN_EXECUTOR_RUNTIME_HANDOFF" "$@" > "$PLAN_EXECUTOR_ROOT/trial.txt"
exit 1
`,
    { mode: 0o755 },
  );
  writeFileSync(join(runtime, 'server/executor/mac/entrypoint.ts'), '// scripted trial');
  const pending = {
    request_id: 'trial',
    application_revision: next_revision,
    runtime_path: runtime,
    previous_runtime_path: null,
    previous_revision: base_revision,
    executor_incarnation: '11111111-1111-1111-1111-111111111111',
    connection_generation: 7,
    archive_sha256: 'c'.repeat(64),
  };
  writeFileSync(
    join(baseline, 'server/executor/mac/entrypoint.ts'),
    `
import fs from 'node:fs';import{join}from'node:path';
const root=process.env.PLAN_EXECUTOR_ROOT, path=join(root,'observed.json');
const rows=fs.existsSync(path)?JSON.parse(fs.readFileSync(path,'utf8')):[];
rows.push({args:process.argv.slice(2),path:process.env.PATH,handoff:process.env.PLAN_EXECUTOR_RUNTIME_HANDOFF,
  failure:JSON.parse(fs.readFileSync(join(root,'updates/failure.json'),'utf8')).request_id});
fs.writeFileSync(path,JSON.stringify(rows));
if(rows.length===1){fs.writeFileSync(join(root,'updates/pending.json'),JSON.stringify(${JSON.stringify(pending)}));process.exitCode=75;}
`,
  );
  const files = new FilesService(),
    owners = new ChildProcesses(),
    one_shot_children = new OneShotChildren(owners);
  try {
    await atomic_record(files, join(root, 'updates/pending.json'), {
      ...pending,
      request_id: 'interrupted',
    });
    await atomic_record(files, join(root, 'updates/handoff.json'), {
      executor_incarnation: pending.executor_incarnation,
      connection_generation: 7,
      replay_pending: true,
    });
    const args = ['executor', 'run', '--application-revision', base_revision];
    expect(
      await supervise(args, {
        paths: new ExecutorPaths(root),
        baseline_root: baseline,
        baseline_node: process.execPath,
        environment: { PATH: '/usr/bin:/bin', HOME: root },
        files,
        child_processes: owners,
        one_shot_children,
        signal: new AbortController().signal,
      }),
    ).toBe(0);
    await owners.settled();
    const observed = JSON.parse(readFileSync(join(root, 'observed.json'), 'utf8'));
    const trial = readFileSync(join(root, 'trial.txt'), 'utf8').trimEnd().split('\n');
    expect(trial[0]).toBe(join(runtime, 'bin') + ':/usr/bin:/bin');
    expect(trial.slice(1)).toEqual([
      join(root, 'updates/handoff.json'),
      join(runtime, 'server/executor/mac/entrypoint.ts'),
      'executor',
      'run',
      '--application-revision',
      next_revision,
    ]);
    expect(observed.map((row: { failure: string }) => row.failure)).toEqual([
      'interrupted',
      'trial',
    ]);
    expect(
      observed.every(
        (row: { args: string[]; path: string; handoff: string }) =>
          JSON.stringify(row.args) === JSON.stringify(args) &&
          row.path.startsWith(join(baseline, 'bin') + ':') &&
          row.handoff === join(root, 'updates/handoff.json'),
      ),
    ).toBe(true);
    expect(JSON.parse(readFileSync(join(root, 'updates/failure.json'), 'utf8'))).toMatchObject({
      request_id: 'trial',
      phase: 'reverted',
    });
    expect(
      JSON.parse(readFileSync(join(root, 'updates/handoff.json'), 'utf8')).replay_pending,
    ).toBe(true);
  } finally {
    one_shot_children.seal();
    owners.seal();
    await one_shot_children.settled();
    await owners.settled();
    await files.stop({ deadline: performance.now() / 1000 + 10 });
    await files.settled();
    rmSync(root, { recursive: true, force: true });
  }
});
