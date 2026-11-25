/** Sign the qualified Mac executable and compare the final archive against the qualified payload. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import {
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  readlink,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { create as tar } from 'tar';
import { exchange_names } from '../../core/locks/flock.ts';
import { MAC_RUNTIME_ARCHIVE_NAME } from '../../executor/mac/configuration.ts';
import { MAC_RUNTIME_LAUNCHER_APP } from '../../executor/mac/platform.ts';
import { mac_designated_requirement } from '../../executor/mac/runtime/launcher_trial.ts';
import { WORKER_LAUNCHER_MODE } from '../../platform_runtime/darwin/launchd/configuration.ts';
import { hash_archive, write_hardlinks_as_files } from './archive.ts';
import { ARCHIVE_GZIP_LEVEL } from './configuration.ts';
import { app_tree_paths } from './identity.ts';
import { digest_machine_build } from './machine_identity.ts';
import type { MacRuntimePublication } from './mac_publication_contracts.ts';

const run = promisify(execFile);
const binary = MAC_RUNTIME_LAUNCHER_APP + '/Contents/MacOS/Panels';
const seal = MAC_RUNTIME_LAUNCHER_APP + '/Contents/_CodeSignature';
const marker = 'mac-runtime.json';
const receiptName = 'mac-runtime-publication.json';

async function unsignedHash(executable: string, work: string) {
  const copy = join(work, 'unsigned');
  await cp(executable, copy);
  await run('/usr/bin/codesign', ['--remove-signature', copy]);
  return hash_archive(copy);
}

// This comparison includes provenance bytes normally excluded from machine identity.
export async function compareSignedPayload(
  original: string,
  signed: string,
  oldId: string,
  newId: string,
) {
  const names = async (root: string) =>
    (await app_tree_paths(root))
      .map((p) => relative(root, p))
      .filter((p) => p !== seal && p !== seal + '/CodeResources');
  const before = await names(original);
  assert.deepEqual(await names(signed), before);
  for (const name of before) {
    const a = join(original, name),
      b = join(signed, name);
    const info = await lstat(a);
    assert.equal((await lstat(b)).mode, info.mode, name);
    if (info.isSymbolicLink()) assert.equal(await readlink(b), await readlink(a), name);
    else if (name === marker) {
      const raw = await readFile(a, 'utf8');
      const field = '"build_id":"' + oldId + '"';
      assert.equal(raw.split(field).length, 2);
      assert.equal(await readFile(b, 'utf8'), raw.replace(field, '"build_id":"' + newId + '"'));
    } else if (info.isFile() && name !== binary) {
      assert.equal(await hash_archive(b), await hash_archive(a), name);
    } else assert.ok(info.isDirectory() || name === binary, name);
  }
}

async function promote(input: string, output: string, identity: string, previousApp: string) {
  assert.equal(process.platform, 'darwin');
  const production = identity !== '-';
  if (production) {
    assert.equal(process.env.GITHUB_REF, 'refs/heads/main');
    assert.equal(process.env.GITHUB_EVENT_NAME, 'push');
  }
  const source = join(input, MAC_RUNTIME_ARCHIVE_NAME);
  const qualified: MacRuntimePublication = JSON.parse(
    await readFile(join(input, receiptName), 'utf8'),
  );
  assert.equal(await hash_archive(source), qualified.archive_sha256);
  const work = await mkdtemp(join(tmpdir(), 'panels-launcher-sign-'));
  try {
    const original = join(work, 'qualified'),
      signed = join(work, 'signed'),
      final = join(work, 'final');
    for (const dir of [original, signed, final]) await mkdir(dir);
    const extract = (archive: string, dir: string) =>
      run('/usr/bin/tar', ['-xpf', archive, '-C', dir]);
    await extract(source, original);
    await extract(source, signed);
    assert.equal(await digest_machine_build(original), qualified.build_id);
    const app = join(signed, MAC_RUNTIME_LAUNCHER_APP);
    const verify = async (bundle: string, requirement: string) =>
      run('/usr/bin/codesign', ['--verify', '--strict', '-R', '=' + requirement, bundle]);
    const identifier = 'identifier "com.panels.executor.typescript.app"';
    await verify(app, identifier);
    assert.equal(await digest_machine_build(app), qualified.launcher_sha256);
    const unsigned = await unsignedHash(join(original, binary), work);
    await run('/usr/bin/codesign', [
      '--force',
      '--sign',
      identity,
      '--options',
      'runtime',
      ...(production ? ['--timestamp'] : []),
      app,
    ]);
    let notarization_id: string | null = null;
    let team: string | null = null;
    if (production) {
      const requirement =
        identifier +
        ' and anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists';
      await verify(previousApp, requirement);
      const old = await run('/usr/bin/codesign', ['-dv', '--verbose=4', '-r-', previousApp]);
      await verify(app, mac_designated_requirement(old.stdout + '\n' + old.stderr));
      await verify(app, requirement);
      team = /^TeamIdentifier=([A-Z0-9]{10})$/m.exec(old.stderr)?.[1] ?? null;
      assert.ok(team);
      const details = await run('/usr/bin/codesign', ['-dv', '--verbose=4', app]);
      assert.equal(/^TeamIdentifier=(.+)$/m.exec(details.stderr)?.[1], team);
      const zip = join(work, 'launcher.zip');
      await run('/usr/bin/ditto', ['-c', '-k', '--keepParent', app, zip]);
      const ticket = await run('/usr/bin/xcrun', [
        'notarytool',
        'submit',
        zip,
        '--key',
        process.env.NOTARY_KEY_PATH!,
        '--key-id',
        process.env.APPLE_NOTARY_API_KEY_ID!,
        '--issuer',
        process.env.APPLE_NOTARY_API_ISSUER_ID!,
        '--wait',
        '--output-format',
        'json',
      ]);
      const result = JSON.parse(ticket.stdout);
      assert.equal(result.status, 'Accepted');
      assert.match(result.id, /^[0-9a-f-]{36}$/i);
      notarization_id = result.id;
    }
    const newId = await digest_machine_build(signed);
    const rawMarker = await readFile(join(original, marker), 'utf8');
    assert.equal(JSON.parse(rawMarker).build_id, qualified.build_id);
    await writeFile(
      join(signed, marker),
      rawMarker.replace('"build_id":"' + qualified.build_id + '"', '"build_id":"' + newId + '"'),
    );
    await mkdir(output, { recursive: true });
    const archive = join(output, MAC_RUNTIME_ARCHIVE_NAME);
    await tar(
      {
        cwd: signed,
        file: archive,
        gzip: { level: ARCHIVE_GZIP_LEVEL },
        portable: true,
        onWriteEntry: write_hardlinks_as_files,
      },
      (await readdir(signed)).sort(),
    );
    await extract(archive, final);
    const finalApp = join(final, MAC_RUNTIME_LAUNCHER_APP);
    await verify(finalApp, identifier);
    assert.equal(await unsignedHash(join(final, binary), work), unsigned);
    await compareSignedPayload(original, final, qualified.build_id, newId);
    assert.equal(await digest_machine_build(final), newId);
    const launcherHash = await digest_machine_build(finalApp);
    assert.equal(launcherHash, await digest_machine_build(app));
    // Exchange the two complete bundles in a disposable installation and start a worker through it.
    const installed = join(work, 'installed.app');
    await run('/usr/bin/ditto', [previousApp, installed]);
    exchange_names(installed, finalApp);
    assert.equal(await digest_machine_build(installed), launcherHash);
    const probe = await run(
      join(installed, 'Contents/MacOS/Panels'),
      [WORKER_LAUNCHER_MODE, join(final, 'node/bin/node'), '-p', '"panels-worker-ready"'],
      { timeout: 30_000 },
    );
    assert.equal(probe.stdout.trim(), 'panels-worker-ready');
    const archiveHash = await hash_archive(archive);
    await writeFile(
      join(output, receiptName),
      JSON.stringify({
        ...qualified,
        archive_sha256: archiveHash,
        archive_size: (await stat(archive)).size,
        build_id: newId,
        repeat_build_id: undefined,
        launcher_sha256: launcherHash,
      }) + '\n',
    );
    await writeFile(
      join(output, 'signing-proof.json'),
      JSON.stringify({
        qualified_build_id: qualified.build_id,
        qualified_archive_sha256: qualified.archive_sha256,
        signed_build_id: newId,
        signed_archive_sha256: archiveHash,
        qualified_launcher_sha256: qualified.launcher_sha256,
        signed_launcher_sha256: launcherHash,
        unsigned_executable_sha256: unsigned,
        team,
        notarization_id,
      }) + '\n',
    );
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [input, output, identity, previous] = process.argv.slice(2);
  assert.ok(
    input && output && identity && previous,
    'Expected qualified directory, signed directory, signing identity and previous launcher path',
  );
  await promote(resolve(input), resolve(output), identity, resolve(previous));
}
