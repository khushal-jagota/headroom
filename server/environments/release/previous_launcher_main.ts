/** Read the last public Mac launcher for the release's Developer ID swap proof. */
import { createWriteStream } from 'node:fs';
import { mkdir, rm, stat, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { extract } from 'tar';
import { MAC_RUNTIME_LAUNCHER_APP } from '../../executor/mac/platform.ts';
import { GitHubUpdateProvider } from '../updates/provider.ts';
import { MAC_RUNTIME_ARCHIVE_NAME } from '../updates/mac/configuration.ts';
import { with_zip_member } from '../updates/zip.ts';
import { hash_archive } from './archive.ts';
import { MAC_HOME } from './contracts.ts';
import { RELEASE_REPOSITORY } from './source.ts';

if (process.argv.length !== 3) throw new Error('Expected a new work directory');
const work = resolve(process.argv[2]!);
await mkdir(work, { mode: 0o700 });
const zip = join(work, 'mac.zip');
const archive = join(work, MAC_RUNTIME_ARCHIVE_NAME);
const token = process.env.GH_TOKEN;
const token_file = token ? join(work, 'release-token') : null;
try {
  if (token_file && token) await writeFile(token_file, token, { mode: 0o600, flag: 'wx' });
  const provider = new GitHubUpdateProvider(
    process.env.GITHUB_REPOSITORY ?? RELEASE_REPOSITORY,
    token_file,
    {
      target: MAC_HOME,
    },
  );
  const release = await provider.available();
  const mac = release?.mac;
  if (!mac || mac.artifact_source !== 'github-release')
    throw new Error('The published Mac home has no permanent machine package.');
  await provider.download_artifact(mac.artifact_id, zip, undefined, mac.artifact_source);
  await with_zip_member(zip, MAC_RUNTIME_ARCHIVE_NAME, (input) =>
    pipeline(input, createWriteStream(archive, { flags: 'wx' })),
  );
  if (
    (await stat(archive)).size !== mac.archive_size ||
    (await hash_archive(archive)) !== mac.archive_sha256
  )
    throw new Error('The previous Mac machine archive differs from its publication.');
  await extract({ cwd: work, file: archive, strict: true }, [MAC_RUNTIME_LAUNCHER_APP]);
  const launcher = join(work, MAC_RUNTIME_LAUNCHER_APP);
  if (!(await stat(launcher)).isDirectory()) throw new Error('The previous launcher is missing.');
  console.log(launcher);
} finally {
  if (token_file) await rm(token_file, { force: true });
  await rm(zip, { force: true });
  await rm(archive, { force: true });
}
