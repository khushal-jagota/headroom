/** Isolated signed-bundle test after the normal joined-machine rehearsal. Use the prior
 * published runtime's launcher generator, as that release built its launcher at join. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { mac_installation_paths } from '../platform.ts';
import { digest_machine_build } from '../../../environments/release/machine_identity.ts';
import { atomic_write_json } from '../../../environments/updates/attempts.ts';

type Run = (
  args: string[],
  environment?: NodeJS.ProcessEnv,
  allow_nonzero?: boolean,
) => Promise<{ code: number; stdout: string; stderr: string }>;
export async function native_launcher_trial(
  root: string,
  paths: ReturnType<typeof mac_installation_paths>,
  run: Run,
) {
  assert.equal(process.env.GITHUB_ACTIONS, 'true');
  const previous = join(root, 'previous');
  const manifest = JSON.parse(await readFile(join(previous, 'mac-runtime.json'), 'utf8'));
  const assets = (
    await import(pathToFileURL(join(previous, 'server/executor/mac/platform.ts')).href)
  ).mac_installation_assets({
    home: paths.home,
    revision: manifest.application_revision,
    executor_id: 'older',
    server_url: 'https://localhost',
    backend_path: '/usr/bin',
  });
  const candidate = await digest_machine_build(paths.app);
  const source = join(root, 'previous-launcher.c');
  await writeFile(source, assets.files['launcher.c'].text);
  await writeFile(
    join(paths.app, 'Contents/Info.plist'),
    assets.files['Panels TypeScript.app/Contents/Info.plist'].text,
  );
  await run(['/usr/bin/clang', '-arch', 'arm64', '-O2', source, '-o', paths.app_executable]);
  await run(['/usr/bin/codesign', '--force', '--sign', '-', paths.app]);
  const old = await digest_machine_build(paths.app);
  assert.notEqual(candidate, old);
  const updates = join(paths.state, 'updates');
  const selection = join(updates, 'selection.json');
  await assert.rejects(readFile(selection), { code: 'ENOENT' });
  const helper = pathToFileURL(
    join(paths.supervisor, 'server/executor/mac/runtime/launcher_trial.ts'),
  ).href;
  for (const result of ['rollback', 'damaged-backup', 'commit']) {
    const id = 'rehearsal-' + result,
      key = createHash('sha256').update(id).digest('hex');
    const job = 'gui/' + process.getuid!() + '/' + paths.job + '.update.' + key;
    try {
      atomic_write_json(join(updates, 'pending.json'), {
        request_id: id,
        runtime_path: paths.supervisor,
      });
      await run([
        paths.node,
        '--input-type=module',
        '-e',
        'const {admit_launcher}=await import(process.argv[1]);await admit_launcher(process.argv[2],process.argv[3]);',
        helper,
        paths.state,
        paths.supervisor,
      ]);
      assert.equal(await digest_machine_build(paths.app), candidate);
      const staged = join(dirname(paths.app), '.' + paths.job + '.update.' + key + '.app');
      const retained = staged + '.fixture-copy';
      if (result === 'rollback') await run(['/bin/launchctl', 'kill', 'SIGKILL', job]);
      if (result === 'damaged-backup') {
        await rename(staged, retained);
        await mkdir(staged);
      }
      if (result === 'commit')
        atomic_write_json(selection, { request_id: id, runtime_path: paths.supervisor });
      await rm(join(updates, 'pending.json'));
      const signal = AbortSignal.timeout(60_000);
      if (result === 'damaged-backup') {
        while ((await run(['/bin/launchctl', 'print', job], undefined, true)).code === 0)
          await delay(200, undefined, { signal });
        const journal = JSON.parse(await readFile(join(updates, 'launcher-trial.json'), 'utf8'));
        assert.match(journal.error, /Cannot identify the previous signed Mac launcher/);
        assert.equal(await digest_machine_build(paths.app), candidate);
        assert.equal(await digest_machine_build(retained), old);
        await assert.rejects(
          readFile(join(dirname(paths.plist), job.split('/').at(-1) + '.plist')),
          { code: 'ENOENT' },
        );
        // Repair only this disposable fault and ask the real helper to finish rollback.
        await rm(staged, { recursive: true });
        await rename(retained, staged);
        await run([
          paths.node,
          join(paths.supervisor, 'server/executor/mac/runtime/launcher_trial_main.ts'),
          id,
        ]);
      }
      for (;;) {
        if (
          !(await readFile(join(updates, 'launcher-trial.json')).then(
            () => true,
            (error) => {
              if (error.code === 'ENOENT') return false;
              throw error;
            },
          ))
        )
          break;
        await delay(200, undefined, { signal });
      }
      assert.equal(await digest_machine_build(paths.app), result === 'commit' ? candidate : old);
      await run(['/usr/bin/codesign', '--verify', '--strict', paths.app]);
    } finally {
      await run(['/bin/launchctl', 'bootout', job], undefined, true);
      await rm(selection, { force: true });
    }
  }
  // Enter exactly as the permanent supervisor's versioned child does. Observe
  // the process which actually owns the re-entered runtime, outside Node.
  const requester = await run([
    paths.node,
    '--input-type=module',
    '-e',
    `import { execFileSync } from 'node:child_process';
     import { writeFileSync } from 'node:fs';
     const {enter_signed_runtime} = await import(process.argv[1]);
     await enter_signed_runtime(process.argv[2], process.env, async () => writeFileSync(process.argv[3], 'admitted', {flag:'wx'}));
     console.log(execFileSync('/bin/ps', ['-p', String(process.ppid), '-o', 'comm='], {encoding:'utf8'}).trim());`,
    helper,
    paths.state,
    join(root, 'requester-admission'),
  ]);
  assert.equal(await readFile(join(root, 'requester-admission'), 'utf8'), 'admitted');
  assert.equal(requester.stdout.trim(), paths.app_executable);
  return {
    active_requester: requester.stdout.trim(),
    previous_revision: manifest.application_revision,
    previous_launcher_sha256: old,
    candidate_launcher_sha256: candidate,
    forced_rollback: true,
    stopped_after_damaged_backup: true,
    requester_admissions: 1,
    committed: true,
  };
}
