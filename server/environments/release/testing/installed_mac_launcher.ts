/** Inspect the actual joined app in the disposable account. Only the PR fixture
 * changes its signing identity. Production admission always uses the published bytes. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { promisify } from 'node:util';
import { exchange_names } from '../../../core/locks/flock.ts';
import {
  MAC_RUNTIME_LAUNCHER_APP,
  mac_installation_paths,
} from '../../../executor/mac/platform.ts';
import { digest_machine_build } from '../machine_identity.ts';

const execute = promisify(execFile);
const bundleId = 'com.panels.executor.typescript.app';
const run = (file: string, argv: string[]) => execute(file, argv, { timeout: 30_000 });
const developer_id = (app: string) =>
  run('/usr/bin/codesign', [
    '--verify',
    '--strict',
    '-R',
    '=anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists',
    app,
  ]);

async function inspect(app: string) {
  await run('/usr/bin/codesign', [
    '--verify',
    '--strict',
    '-R',
    '=identifier "' + bundleId + '"',
    app,
  ]);
  const { stderr } = await run('/usr/bin/codesign', ['-dv', '--verbose=4', app]);
  const binary = join(app, 'Contents/MacOS/Panels');
  // Only the signature is allowed to change when adapting the joined app.
  // otool prints section contents without addresses/headers with -X.
  const { stdout } = await run('/usr/bin/otool', ['-X', '-s', '__TEXT', '__text', binary]);
  const bytes = stdout
    .split('\n')
    .filter((line) => /^\s*[\da-f]+(?:\s+[\da-f]+)*\s*$/i.test(line))
    .join('')
    .replace(/\s/g, '');
  assert.match(bytes, /^(?:[\da-f]{2}){16,}$/i);
  return {
    bundle_sha256: await digest_machine_build(app),
    code_sha256: createHash('sha256').update(Buffer.from(bytes, 'hex')).digest('hex'),
    info_sha256: createHash('sha256')
      .update(await readFile(join(app, 'Contents/Info.plist')))
      .digest('hex'),
    team: /^TeamIdentifier=(.+)$/m.exec(stderr)?.[1] ?? null,
    authority: /^Authority=(.+)$/m.exec(stderr)?.[1] ?? null,
    adhoc: /^Signature=adhoc$/m.test(stderr),
  };
}
type Signature = Awaited<ReturnType<typeof inspect>>;

export async function installed_mac_launcher(
  base: string,
  paths: ReturnType<typeof mac_installation_paths>,
) {
  assert.equal(process.platform, 'darwin');
  assert.equal(process.env.RUNNER_ENVIRONMENT, 'github-hosted');
  assert.equal(base, join(process.env.RUNNER_TEMP!, 'qualification'));
  const mode = process.env.MAC_LAUNCHER_QUALIFICATION;
  assert.ok(mode === 'developer-id' || mode === 'ad-hoc');
  assert.equal(
    mode === 'developer-id',
    process.env.GITHUB_EVENT_NAME === 'push' && process.env.GITHUB_REF === 'refs/heads/main',
  );
  const inputs = JSON.parse(
    await readFile(join(base, 'evidence/qualification-inputs.json'), 'utf8'),
  );
  const original = await inspect(paths.app);
  assert.equal(
    original.bundle_sha256,
    await digest_machine_build(join(paths.supervisor, MAC_RUNTIME_LAUNCHER_APP)),
  );
  if (mode === 'developer-id' || inputs.mode === 'public predecessor release gate') {
    await developer_id(paths.app);
    assert.match(original.authority ?? '', /^Developer ID Application: /);
    assert.match(original.team ?? '', /^[A-Z0-9]{10}$/);
  }
  const evidence: {
    mode: string;
    predecessor: Signature;
    joined?: Signature;
    after_home_rollback?: Signature;
    candidate?: Signature;
  } = { mode, predecessor: original };
  const save = () =>
    writeFile(
      join(base, 'evidence/mac-launcher-signatures.json'),
      JSON.stringify(evidence, null, 2),
    );
  await save();
  if (mode === 'ad-hoc' && !original.adhoc) {
    const temporary = await mkdtemp(join(dirname(paths.app), '.qualification-'));
    try {
      const adapted = join(temporary, basename(paths.app));
      await run('/usr/bin/ditto', [paths.app, adapted]);
      await run('/usr/bin/codesign', ['--force', '--sign', '-', '--identifier', bundleId, adapted]);
      const signature = await inspect(adapted);
      assert.equal(signature.adhoc, true);
      assert.equal(signature.code_sha256, original.code_sha256);
      assert.equal(signature.info_sha256, original.info_sha256);
      exchange_names(adapted, paths.app);
    } finally {
      await rm(temporary, { recursive: true, force: true });
    }
  }
  const joined = await inspect(paths.app);
  evidence.joined = joined;
  await save();
  return {
    async verify_rollback() {
      evidence.after_home_rollback = await inspect(paths.app);
      assert.deepEqual(evidence.after_home_rollback, joined);
      await save();
    },
    async verify_current() {
      let runtime = paths.supervisor;
      try {
        runtime = JSON.parse(
          await readFile(join(paths.state, 'updates/selection.json'), 'utf8'),
        ).runtime_path;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      }
      const manifest = JSON.parse(await readFile(join(runtime, 'mac-runtime.json'), 'utf8'));
      const publication = JSON.parse(await readFile(join(base, 'feed/publication.json'), 'utf8'));
      assert.equal(manifest.build_id, publication.parts.machines['darwin-arm64'].build_id);
      const candidate = await inspect(paths.app);
      evidence.candidate = candidate;
      await save();
      assert.equal(
        candidate.bundle_sha256,
        await digest_machine_build(join(runtime, MAC_RUNTIME_LAUNCHER_APP)),
      );
      if (mode === 'developer-id') {
        await developer_id(paths.app);
        assert.equal(joined.bundle_sha256, original.bundle_sha256);
        assert.match(candidate.authority ?? '', /^Developer ID Application: /);
        assert.equal(candidate.team, original.team);
      } else assert.equal(candidate.adhoc, true);
    },
  };
}
