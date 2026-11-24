/** Retain only non-secret facts when the ordinary file-backed turn fails in a disposable home. */
import { execFile } from 'node:child_process';
import { constants } from 'node:fs';
import { access, readFile, stat, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import Database from 'better-sqlite3';
import { DEFAULT_BACKEND_INSTANCE_ID } from '../../../drivers/built_in.ts';

export async function write_turn_diagnostics(
  base: string,
  workspace: string,
  machine: string,
): Promise<void> {
  const run = promisify(execFile);
  const info: Record<string, unknown> = {
    machine,
    workspace,
    workspace_is_directory: (await stat(workspace)).isDirectory(),
  };
  const fixture = join(homedir(), '.local/bin/codex');
  info.account_peer = (
    await run(fixture, ['--version'], { timeout: 5000 }).catch(() => null)
  )?.stdout.trim();
  const records = (
    await readFile(join(base, 'codex-transcript.jsonl'), 'utf8').catch(() => '')
  ).split('\n');
  const methods = new Set<string>();
  for (const line of records) {
    if (!line) continue;
    try {
      const method = JSON.parse(line).method;
      if (typeof method === 'string' && /^[a-zA-Z/]+$/.test(method)) methods.add(method);
    } catch {
      /* A pipe can end during the last frame. */
    }
  }
  info.observed_peer_methods = [...methods];
  const db = new Database(join(base, 'installed/data/planner.db'), {
    readonly: true,
    fileMustExist: true,
  });
  try {
    const row = db
      .prepare(
        'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
      )
      .get(machine, DEFAULT_BACKEND_INSTANCE_ID) as { snapshot_json: string } | undefined;
    const snapshot = row && JSON.parse(row.snapshot_json);
    info.advertised_provider = snapshot
      ? {
          installed: snapshot.installed,
          executable_path: snapshot.executable_path,
          version: snapshot.version,
          models_complete: snapshot.models_complete,
          offers_test:
            snapshot.available_models?.some(
              (model: { model_id: string }) => model.model_id === 'TEST',
            ) ?? false,
        }
      : null;
  } finally {
    db.close();
  }
  if (process.platform === 'linux') {
    const unit = 'panels-executor-typescript.service';
    const show = await run(
      'systemctl',
      ['--user', 'show', unit, '-p', 'MainPID', '-p', 'ControlGroup', '-p', 'Delegate'],
      { timeout: 5000 },
    );
    const props = Object.fromEntries(
      show.stdout
        .trim()
        .split('\n')
        .map((line) => line.split('=')),
    );
    const environment = (
      await readFile('/proc/' + props.MainPID + '/environ', 'utf8').catch(() => '')
    ).split('\0');
    info.joined_service = {
      ...props,
      environment: environment.filter((item) => /^(HOME|PATH)=/.test(item)),
      delegated_cgroup_writable:
        !!props.ControlGroup &&
        (await access(join('/sys/fs/cgroup', props.ControlGroup), constants.W_OK).then(
          () => true,
          () => false,
        )),
    };
  }
  await writeFile(join(base, 'evidence/turn-diagnostics.json'), JSON.stringify(info, null, 2));
}
