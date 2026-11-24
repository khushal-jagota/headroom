import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { it } from 'vitest';
import { createDatabaseFromSeed } from '../core/database/index.ts';
import { application_schema_seed } from '../core/database/admission.ts';
import { bootstrap_application } from '../environments/bootstrap.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { verify_enrolled_executor } from '../machines/executor_credentials.ts';
import { prepare_first_machine } from './machine.ts';
import { create_linux_home_service } from './linux.ts';
import { home_install_fixture } from './testing/home_fixture.ts';
import { ensure_upgrade_machine, has_home_credential } from './upgrade_machine.ts';

it('resumes a missing local machine after a failed unit start without replacing its credential', async () => {
  const f = await home_install_fixture();
  const { root, revision, port, service } = f.request;
  await mkdir(join(root, 'data'), { recursive: true });
  await mkdir(join(root, 'app'));
  await writeFile(join(root, 'app/manifest.json'), JSON.stringify({ app_sha: revision }));
  const db = createDatabaseFromSeed(join(root, 'data/planner.db'), application_schema_seed, (tx) =>
    bootstrap_application(tx, 1),
  );
  let starts = 0;
  const deps = {
    ...f.deps,
    prepare_machine: prepare_first_machine,
    service: create_linux_home_service,
    command: async (argv: readonly string[]) => {
      if (argv.includes('enable') && ++starts === 1)
        throw new Error('simulated unit start failure');
      return { code: 0, stdout: '', stderr: '' };
    },
  };
  try {
    const ensure = () =>
      ensure_upgrade_machine(root, service + '.service', port, revision, db, deps);
    assert(!has_home_credential(db));
    await assert.rejects(ensure, /simulated unit start failure/);
    const saved = async () =>
      JSON.parse(await readFile(join(root, 'machine/data/machine-credential.json'), 'utf8'))
        .credential;
    const before = await saved();
    await ensure();
    await ensure();
    assert.equal(starts, 2);
    assert.equal(await saved(), before);
    db.read((sql) => verify_enrolled_executor(sql, HOME_MACHINE_ID, before));
    // Simulate restoring the pre-trial snapshot, then trying a different release.
    db.write((tx) =>
      tx.run('UPDATE machine_executor_credentials SET credential_sha256=NULL WHERE machine_id=?', [
        HOME_MACHINE_ID,
      ]),
    );
    await ensure_upgrade_machine(root, service + '.service', port, 'e'.repeat(40), db, deps);
    db.read((sql) => verify_enrolled_executor(sql, HOME_MACHINE_ID, before));
    assert.equal(
      JSON.parse(await readFile(join(root, 'machine/data/identity.json'), 'utf8')).machine_id,
      HOME_MACHINE_ID,
    );
  } finally {
    db.close();
    await f.close();
  }
});
