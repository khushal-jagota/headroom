/** Bind native Mac evidence on the receipt runner without executing the Mac payload. */
import { stat } from 'node:fs/promises';
import { basename, isAbsolute, join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { is_record } from '../../core/records.ts';
import { sha256_file } from '../backup/evidence.ts';
import { extract_app_archive } from './archive.ts';
import { validate_app_manifest } from './identity.ts';
import { MAC_HOME } from './contracts.ts';
import type { AppManifest, ReleaseExecution } from './contracts.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';
import {
  APP_ARCHIVE_NAME,
  ARCHIVE_MAX_COMPRESSED_BYTES,
  RUNTIME_INVENTORY_PATH,
} from './configuration.ts';
import { MAC_HOME_PUBLICATION_METADATA } from './mac_home_configuration.ts';
import { publication_id, publication_json_file, publication_text } from './publication_identity.ts';
import { assert_release_execution } from './process.ts';
import { require_minimum_node } from './logic/host.ts';

export const MAC_HOME_RECEIPT_FIELDS = [
  'mac_home_artifact_zip',
  'mac_home_build_artifact_zip',
  'mac_home_artifact_id',
  'mac_home_build_artifact_id',
  'mac_home_build_result',
] as const;
export interface MacHomeReceiptOptions {
  readonly mac_home_artifact_zip: string;
  readonly mac_home_build_artifact_zip: string;
  readonly mac_home_artifact_id: string;
  readonly mac_home_build_artifact_id: string;
  readonly mac_home_build_result: string;
}
export interface MacHomeReceipt {
  readonly artifact_id: number;
  readonly archive_sha256: string;
  readonly manifest: AppManifest;
}
export function mac_home_receipt_options(
  value: Record<string, unknown>,
): MacHomeReceiptOptions | undefined {
  const count = MAC_HOME_RECEIPT_FIELDS.filter((key) => Object.hasOwn(value, key)).length;
  if (count === 0) return undefined;
  if (count !== MAC_HOME_RECEIPT_FIELDS.length)
    throw new Error('Mac home receipt inputs must be supplied together');
  const options = Object.fromEntries(
    MAC_HOME_RECEIPT_FIELDS.map((key) => [key, publication_text(value[key], key)]),
  ) as unknown as MacHomeReceiptOptions;
  for (const path of [options.mac_home_artifact_zip, options.mac_home_build_artifact_zip])
    if (!isAbsolute(path)) throw new Error('Mac home publication paths must be absolute');
  for (const id of [options.mac_home_artifact_id, options.mac_home_build_artifact_id])
    if (!Number.isSafeInteger(Number(publication_id(id, 'Mac home artifact ID'))))
      throw new Error('Mac home artifact ID must be a safe integer');
  if (options.mac_home_build_result !== 'success')
    throw new Error('Mac home build did not succeed');
  return options;
}
function record(value: unknown): Record<string, unknown> {
  if (!is_record(value)) throw new Error('Invalid Mac home publication evidence');
  return value;
}
function seconds(value: unknown): void {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0)
    throw new Error('Mac home proof duration is invalid');
}
function digest(value: unknown): string {
  if (typeof value !== 'string' || /^[0-9a-f]{64}$/.exec(value)?.[0] !== value)
    throw new Error('Mac home proof SHA256 is invalid');
  return value;
}
export async function validate_mac_home_receipt(
  options: MacHomeReceiptOptions,
  source_run: PublicationSourceRun,
  work: string,
  execution: ReleaseExecution,
  member: (zip: string, name: string, destination: string) => Promise<void>,
): Promise<MacHomeReceipt> {
  assert_release_execution(execution);
  const archive = join(work, APP_ARCHIVE_NAME);
  await member(options.mac_home_artifact_zip, APP_ARCHIVE_NAME, archive);
  for (const name of [MAC_HOME_PUBLICATION_METADATA, 'admission.json', 'boot-proof.json'])
    await member(options.mac_home_build_artifact_zip, name, join(work, name));
  const build = record(await publication_json_file(join(work, MAC_HOME_PUBLICATION_METADATA))),
    run = record(build.build_run),
    manifest = record(build.manifest),
    admission = record(await publication_json_file(join(work, 'admission.json'))),
    boot = record(await publication_json_file(join(work, 'boot-proof.json')));
  if (
    build.version !== 1 ||
    run.source_sha !== source_run.source_sha ||
    run.run_id !== source_run.run_id ||
    run.run_attempt !== source_run.run_attempt ||
    run.run_id === null ||
    run.run_attempt === null
  )
    throw new Error('Mac home build differs from the current publication attempt');
  for (const [field, filename] of [
    ['admission', 'admission.json'],
    ['boot', 'boot-proof.json'],
  ] as const) {
    const reference = record(build[field]);
    if (
      reference.filename !== filename ||
      (await sha256_file(join(work, filename))) !== digest(reference.sha256)
    )
      throw new Error('Mac home proof bytes changed');
  }
  const hash = digest(build.archive_sha256),
    info = await stat(archive);
  if (
    typeof build.archive !== 'string' ||
    basename(build.archive) !== APP_ARCHIVE_NAME ||
    !info.isFile() ||
    !Number.isSafeInteger(info.size) ||
    info.size <= 0 ||
    info.size > ARCHIVE_MAX_COMPRESSED_BYTES ||
    build.archive_size !== info.size ||
    (await sha256_file(archive)) !== hash
  )
    throw new Error('Mac home archive bytes changed');
  for (const proof of [admission, boot]) {
    if (
      proof.requested_sha !== source_run.source_sha ||
      proof.archive_sha256 !== hash ||
      !isDeepStrictEqual(proof.manifest, manifest)
    )
      throw new Error('Mac home proof differs from the sealed archive identity');
    seconds(proof.seconds);
  }
  const health = record(boot.health),
    exit = record(boot.supervisor_exit);
  if (
    admission.status !== 'passed' ||
    health.ready !== true ||
    health.app_sha !== source_run.source_sha ||
    exit.code !== 0 ||
    exit.signal !== null
  )
    throw new Error('Mac home lacks successful native admission and clean boot proof');
  const sealed = join(work, 'sealed'),
    extracted = await extract_app_archive(
      archive,
      sealed,
      hash,
      source_run.source_sha,
      execution.child_processes,
      undefined,
      execution,
      MAC_HOME,
    );
  const actual = await validate_app_manifest(join(sealed, 'manifest.json'), {
    expected_sha: source_run.source_sha,
    expected_target: MAC_HOME,
    require_runtime: true,
  });
  const contract = actual.runtime_contract;
  if (
    !isDeepStrictEqual(extracted, actual) ||
    !isDeepStrictEqual(actual, manifest) ||
    contract?.version !== 2 ||
    contract.system !== 'darwin' ||
    contract.inventory_sha256 !== digest(build.inventory_sha256) ||
    (await sha256_file(join(sealed, RUNTIME_INVENTORY_PATH))) !== build.inventory_sha256
  )
    throw new Error('Mac home sealed manifest or inventory differs from build evidence');
  require_minimum_node(publication_text(build.node_version, 'Mac home Node version'), contract);
  assert_release_execution(execution);
  return {
    artifact_id: Number(options.mac_home_artifact_id),
    archive_sha256: hash,
    manifest: actual,
  };
}
