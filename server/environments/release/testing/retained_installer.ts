/** Exercise the new installer's own command from the sealed package on this native host. */
import { mkdir, readFile, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { create_installer_operations } from '../../updates/installation.ts';
import { release_path } from '../../updates/installer/selection.ts';
import { atomic_write_json } from '../../updates/attempts.ts';
import { UpdateBarrier } from '../../update_barrier.ts';
import { run_release_command } from '../process.ts';
import type { ReleaseExecution } from '../contracts.ts';

export async function prove_retained_installer(
  root: string,
  from: string,
  execution: ReleaseExecution,
): Promise<string> {
  const app = join(root, 'app');
  const data = join(root, 'installer-proof');
  await mkdir(data);
  const id = 'missing-next-release';
  const record = join(data, 'update-attempt.json');
  const to = (from[0] === 'a' ? 'b' : 'a').repeat(40);
  atomic_write_json(record, {
    attempt_id: id,
    from_sha: from,
    to_sha: to,
    state: 'installing',
    steps: {},
  });
  const barrier = new UpdateBarrier(join(data, 'update-barrier.json'));
  barrier.freeze(id);
  const operations = create_installer_operations({
    application_root: app,
    node_executable: join(app, 'node/bin/node'),
    child_processes: execution.child_processes,
    signal: execution.signal,
    environment: {},
  });
  const argv = operations.prepare({
    current_root: root,
    database: join(data, 'planner.db'),
    data_directory: data,
    backup_directory: data,
    attempt_id: id,
    service_unit: 'never-start.service',
    health_url: 'http://127.0.0.1:0/api/health',
    health_timeout_seconds: 1,
  });
  const retained = release_path(root, from);
  if ((await realpath(app)) !== retained || argv[0] !== join(retained, 'node/bin/node'))
    throw new Error('The installer did not select the retained packaged interpreter');
  const result = await run_release_command(
    argv,
    { allow_nonzero: true, timeout_seconds: 30, environment: { PATH: '/usr/bin:/bin' } },
    execution,
  );
  const attempt = JSON.parse(await readFile(record, 'utf8'));
  barrier.refresh();
  if (
    result.code !== 1 ||
    attempt.state !== 'failed' ||
    !attempt.detail.includes('downloaded version is missing') ||
    barrier.frozen ||
    (await realpath(app)) !== retained
  )
    throw new Error(
      'The retained installer did not safely reject the missing package: ' + result.stderr,
    );
  return attempt.detail;
}
