/** Digest-checked permanent machine Releases: the supported floor and newest publication. */
import { createWriteStream } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { mkdir, readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import type { ReadableStream } from 'node:stream/web';
import { is_record } from '../../core/records.ts';
import { sha256_file } from '../backup/evidence.ts';
import { with_zip_member } from '../updates/zip.ts';
import type {
  MachineBaseline,
  MachineBaselinePin,
  MachinePlatform,
} from './machine_compatibility_contracts.ts';

function validate_pin(value: unknown): MachineBaselinePin {
  if (
    !is_record(value) ||
    typeof value.source_sha !== 'string' ||
    !/^[0-9a-f]{40}$/.test(value.source_sha) ||
    typeof value.zip_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.zip_sha256) ||
    typeof value.archive_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.archive_sha256) ||
    !Number.isSafeInteger(value.archive_size) ||
    Number(value.archive_size) < 1
  )
    throw new Error('Invalid retained machine baseline pin');
  return value as unknown as MachineBaselinePin;
}
/** The supported floor is explicit. The previous baseline always comes from the release catalog. */
async function baseline_contract() {
  return JSON.parse(await readFile(new URL('./machine_baselines.json', import.meta.url), 'utf8'));
}
export async function machine_baseline_pin(platform: MachinePlatform): Promise<MachineBaselinePin> {
  return validate_pin((await baseline_contract()).oldest?.[platform]);
}
export async function extract_machine_baseline(
  zip: string,
  pin: MachineBaselinePin,
  platform: MachinePlatform,
  output: string,
): Promise<MachineBaseline> {
  if ((await sha256_file(zip)) !== pin.zip_sha256)
    throw new Error('Machine baseline ZIP digest differs from retained pin');
  await mkdir(output, { recursive: true, mode: 0o700 });
  const archive_path = join(
    output,
    platform === 'linux' ? 'panels-linux-executor.tar.gz' : 'panels-mac-runtime.tar.gz',
  );
  await with_zip_member(
    zip,
    platform === 'linux' ? 'panels-linux-executor.tar.gz' : 'panels-mac-runtime.tar.gz',
    (stream) => pipeline(stream, createWriteStream(archive_path, { flags: 'wx', mode: 0o600 })),
  );
  if (
    (await stat(archive_path)).size !== pin.archive_size ||
    (await sha256_file(archive_path)) !== pin.archive_sha256
  )
    throw new Error('Machine baseline archive differs from retained pin');
  return {
    source_sha: pin.source_sha,
    archive_sha256: pin.archive_sha256,
    archive_size: pin.archive_size,
    archive_path,
    zip_sha256: pin.zip_sha256,
  };
}
/** Read one baseline directly from a permanent Release, without an Actions or cache fallback. */
export async function load_machine_baseline(
  platform: MachinePlatform,
  role: 'oldest' | 'previous',
  root: string,
  repository: string,
  token: string,
  signal: AbortSignal,
): Promise<MachineBaseline> {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid baseline repository');
  const headers = { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' };
  const get = async (url: string, binary = false) => {
    const response = await fetch(url, {
      headers: { ...headers, ...(binary ? { Accept: 'application/octet-stream' } : {}) },
      signal,
    });
    if (!response.ok)
      throw new Error(`Permanent machine baseline is unavailable (${response.status})`);
    return response;
  };
  let release: unknown;
  if (role === 'oldest') {
    const contract = await baseline_contract();
    if (typeof contract.oldest?.release_tag !== 'string')
      throw new Error('Missing permanent machine floor Release');
    release = await (
      await get(
        `https://api.github.com/repos/${repository}/releases/tags/${encodeURIComponent(contract.oldest.release_tag)}`,
      )
    ).json();
  } else {
    const releases: unknown[] = [];
    for (let page = 1; ; page++) {
      const batch = await (
        await get(`https://api.github.com/repos/${repository}/releases?per_page=100&page=${page}`)
      ).json();
      if (!Array.isArray(batch)) throw new Error('Invalid release baseline catalog');
      releases.push(...batch);
      if (batch.length < 100) break;
    }
    release = releases
      .filter(
        (item) =>
          is_record(item) &&
          item.draft === false &&
          item.prerelease === false &&
          typeof item.tag_name === 'string' &&
          item.tag_name.startsWith('server-'),
      )
      .sort((a, b) => {
        if (
          !is_record(a) ||
          !is_record(b) ||
          typeof a.published_at !== 'string' ||
          typeof b.published_at !== 'string' ||
          !Number.isFinite(Date.parse(a.published_at)) ||
          !Number.isFinite(Date.parse(b.published_at))
        )
          throw new Error('Invalid published baseline date');
        return Date.parse(b.published_at) - Date.parse(a.published_at);
      })[0];
  }
  if (
    !is_record(release) ||
    release.draft !== false ||
    release.prerelease !== false ||
    !Array.isArray(release.assets)
  )
    throw new Error('Invalid permanent baseline assets');
  const asset = release.assets.find((item) => is_record(item) && item.name === 'publication.json');
  if (!is_record(asset) || typeof asset.browser_download_url !== 'string')
    throw new Error('Machine Release has no checked publication');
  const receipt_response = await get(
    `https://api.github.com/repos/${repository}/releases/assets/${asset.id}`,
    true,
  );
  const receipt = await receipt_response.json();
  if (!is_record(receipt) || !is_record(receipt.manifest))
    throw new Error('Invalid machine publication');
  const update = platform === 'linux' ? receipt.linux_executor : receipt.mac;
  const zip_asset = release.assets.find(
    (item) =>
      is_record(item) && item.name === (platform === 'linux' ? 'linux-executor.zip' : 'mac.zip'),
  );
  if (
    !is_record(update) ||
    !is_record(zip_asset) ||
    typeof zip_asset.browser_download_url !== 'string' ||
    typeof zip_asset.digest !== 'string'
  )
    throw new Error('Machine Release archive is missing');
  const checked_pin = validate_pin({
    source_sha: receipt.manifest.app_sha,
    archive_sha256: update.archive_sha256,
    archive_size: update.archive_size,
    zip_sha256: zip_asset.digest.replace(/^sha256:/, ''),
  });
  if (role === 'oldest' && !isDeepStrictEqual(checked_pin, await machine_baseline_pin(platform)))
    throw new Error('Permanent machine floor differs from its retained pin');
  await mkdir(root, { recursive: true, mode: 0o700 });
  const zip = join(root, `${role}.zip`);
  const response = await get(
    `https://api.github.com/repos/${repository}/releases/assets/${zip_asset.id}`,
    true,
  );
  if (!response.body) throw new Error('Machine Release archive download has no body');
  await pipeline(
    Readable.fromWeb(response.body as ReadableStream),
    createWriteStream(zip, { flags: 'wx', mode: 0o600 }),
    { signal },
  );
  return extract_machine_baseline(zip, checked_pin, platform, join(root, role));
}
export async function load_machine_baselines(
  platform: MachinePlatform,
  root: string,
  repository: string,
  token: string,
  signal: AbortSignal,
): Promise<{ oldest: MachineBaseline; previous: MachineBaseline }> {
  const [oldest, previous] = await Promise.all([
    load_machine_baseline(platform, 'oldest', root, repository, token, signal),
    load_machine_baseline(platform, 'previous', root, repository, token, signal),
  ]);
  return { oldest, previous };
}
