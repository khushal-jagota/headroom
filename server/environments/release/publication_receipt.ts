/** Same-run artifact binding. The workflow owns its real qualification job needs. */
import { mkdir, readFile, realpath, rm, stat } from 'node:fs/promises';
import { dirname, isAbsolute, join } from 'node:path';
import { check as check_ios_compatibility } from '../../ios_compatibility/check.ts';
import { JsonNumber, numeric_value, parse_numeric_json } from '../../core/http/numeric_json.ts';
import { sha256_file } from '../backup/evidence.ts';
import { inside } from '../ticket_scratch/logic.ts';
import { is_object } from '../updates/codec.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { parse_optional_runtime_contract } from '../updates/logic/runtime_contract.ts';
import {
  SUPPORTED_UPDATE_SCHEMA_FLOOR,
  UPDATE_ARTIFACT_RETENTION_DAYS,
} from '../updates/configuration.ts';
import { MAX_MAC_RUNTIME_ARCHIVE_BYTES } from '../updates/mac/configuration.ts';
import type { PublishedMacUpdate, PublishedUpdate } from '../updates/contracts.ts';
import {
  APP_ARCHIVE_NAME,
  APP_FORMAT,
  ARCHIVE_MAX_COMPRESSED_BYTES,
  PUBLICATION_RECEIPT_NAME,
} from './configuration.ts';
import type { ReleaseExecution } from './contracts.ts';
import { manifest_identity, python_json_equal } from './logic/manifest.ts';
import { migration_frontier, read_migration_graph } from './migrations.ts';
import { assert_release_execution, run_release_command } from './process.ts';
import { publication_id, require_publication_run } from './publication_identity.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';

export type PublicationReceipt = Omit<PublishedUpdate, 'proof_artifact_id' | 'mac'> & {
  readonly mac: PublishedMacUpdate;
};
export interface PublicationReceiptOptions {
  readonly source_root: string;
  readonly linux_root: string;
  readonly mac_root: string;
  readonly output_directory: string;
  readonly artifact_id: string;
  readonly mac_artifact_id: string;
  readonly mac_build_result: string;
}
function record(value: unknown): Record<string, unknown> {
  if (!is_object(value)) throw new Error('Invalid publication evidence object');
  return value;
}
function integer(value: unknown): bigint {
  if (!(value instanceof JsonNumber) || value.integer === null)
    throw new Error('Publication evidence requires an exact integer');
  return value.integer;
}
function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value) || value.length !== 64)
    throw new Error('Invalid publication SHA256');
  return value;
}
async function json_file(path: string): Promise<Record<string, unknown>> {
  return record(
    parse_numeric_json(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(path)),
    ),
  );
}
export async function write_publication_receipt(
  options: PublicationReceiptOptions,
  source_run: PublicationSourceRun,
  execution: ReleaseExecution,
): Promise<PublicationReceipt> {
  const fence = () => assert_release_execution(execution);
  fence();
  require_publication_run(source_run);
  const artifact_id = new JsonNumber(publication_id(options.artifact_id, 'artifact ID')),
    mac_artifact_id = new JsonNumber(publication_id(options.mac_artifact_id, 'Mac artifact ID'));
  for (const path of [
    options.source_root,
    options.linux_root,
    options.mac_root,
    options.output_directory,
  ])
    if (!isAbsolute(path) || path.includes('\0'))
      throw new Error('Publication paths must be absolute');
  const source = await realpath(options.source_root),
    linux = await realpath(options.linux_root),
    mac_root = await realpath(options.mac_root),
    output = options.output_directory;
  let owned = false;
  try {
    fence();
    await mkdir(output, { mode: 0o700 });
    owned = true;
    const environment = {
      PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin',
      HOME: output,
      TMPDIR: output,
      LANG: 'C.UTF-8',
      PYTHONDONTWRITEBYTECODE: '1',
      GIT_CONFIG_NOSYSTEM: '1',
      GIT_CONFIG_SYSTEM: '/dev/null',
      GIT_CONFIG_GLOBAL: '/dev/null',
      GIT_NO_REPLACE_OBJECTS: '1',
    };
    const head = await run_release_command(
      ['git', '-C', source, 'rev-parse', 'HEAD'],
      { timeout_seconds: null, environment },
      execution,
    );
    if (head.stdout.trim() !== source_run.source_sha)
      throw new Error('Receipt source HEAD differs from the event SHA');
    const build = await json_file(join(linux, 'linux-publication-build.json')),
      producer = record(build.source_run);
    if (integer(build.version) !== 1n) throw new Error('Unsupported Linux publication evidence');
    for (const field of [
      'repository',
      'source_sha',
      'event',
      'ref',
      'workflow_path',
      'workflow_ref',
      'run_id',
    ] as const)
      if (producer[field] !== source_run[field])
        throw new Error('Linux build differs from the publication run');
    publication_id(producer.run_attempt, 'producer run attempt');
    const manifest_value = record(build.manifest),
      identity = manifest_identity(
        { format: APP_FORMAT, ...manifest_value },
        source_run.source_sha,
      ),
      runtime_contract = parse_optional_runtime_contract(manifest_value.runtime_contract);
    if (
      runtime_contract?.version !== 2 ||
      digest(build.runtime_inventory_sha256) !== runtime_contract.inventory_sha256
    )
      throw new Error('Receipt requires the actual Node runtime inventory');
    const frontier = [...migration_frontier(await read_migration_graph(source))].sort();
    if (
      build.schema_floor !== SUPPORTED_UPDATE_SCHEMA_FLOOR ||
      !python_json_equal(build.schema_frontier, frontier)
    )
      throw new Error('Publication schema evidence differs from the selected source');
    const admission = record(build.admission),
      seconds = numeric_value(admission.seconds);
    if (
      admission.status !== 'passed' ||
      seconds === undefined ||
      !Number.isFinite(seconds) ||
      seconds < 0
    )
      throw new Error('Publication lacks successful sealed runtime admission');
    const archive = record(build.archive),
      licenses = record(build.licenses);
    if (archive.filename !== APP_ARCHIVE_NAME || licenses.filename !== 'package-licenses.json')
      throw new Error('Unexpected publication output filename');
    const license_path = await realpath(join(linux, 'package-licenses.json'));
    if (
      !inside(license_path, linux) ||
      (await sha256_file(license_path)) !== digest(licenses.sha256)
    )
      throw new Error('Publication license inventory bytes changed');
    const inventory = await json_file(license_path);
    if (
      integer(inventory.version) !== 1n ||
      !Array.isArray(inventory.packages) ||
      integer(licenses.package_count) !== BigInt(inventory.packages.length) ||
      !python_json_equal(licenses.missing_own_licenses, inventory.missing_own_licenses)
    )
      throw new Error('Publication license evidence differs from its inventory');
    fence();
    const checked_mac = await run_release_command(
      [
        'python3',
        '-I',
        '-S',
      '-B',
        join(source, 'scripts/typescript_publication_mac.py'),
        mac_root,
        source_run.source_sha,
        source_run.run_id,
        options.mac_artifact_id,
        options.mac_build_result,
      ],
      { timeout_seconds: null, environment, cwd: source },
      execution,
    );
    const mac = record(parse_numeric_json(checked_mac.stdout));
    if (
      integer(mac.artifact_id) !== mac_artifact_id.integer ||
      integer(mac.archive_size) <= 0n ||
      integer(mac.archive_size) > BigInt(MAX_MAC_RUNTIME_ARCHIVE_BYTES)
    )
      throw new Error('Mac validation differs from its actual artifact');
    const published_mac: PublishedMacUpdate = {
      artifact_id: mac_artifact_id,
      archive_sha256: digest(mac.archive_sha256),
      archive_size: mac.archive_size as JsonNumber,
    };
    await check_ios_compatibility(source);
    fence();
    const archive_path = await realpath(join(linux, APP_ARCHIVE_NAME));
    if (!inside(archive_path, linux))
      throw new Error('Publication archive escapes its downloaded root');
    const info = await stat(archive_path),
      archive_sha256 = digest(archive.sha256);
    if (
      !info.isFile() ||
      !Number.isSafeInteger(info.size) ||
      info.size <= 0 ||
      info.size > ARCHIVE_MAX_COMPRESSED_BYTES ||
      BigInt(info.size) !== integer(archive.size) ||
      (await sha256_file(archive_path)) !== archive_sha256
    )
      throw new Error('Publication archive bytes changed');
    const receipt: PublicationReceipt = {
      publication_run_id: new JsonNumber(source_run.run_id),
      artifact_id,
      manifest: { ...identity, runtime_contract },
      archive_sha256,
      mac: published_mac,
      schema_floor: SUPPORTED_UPDATE_SCHEMA_FLOOR,
      expires_at: new Date(Date.now() + UPDATE_ARTIFACT_RETENTION_DAYS * 86_400_000).toISOString(),
    };
    fence();
    replace_existing_json(
      join(output, PUBLICATION_RECEIPT_NAME),
      receipt as unknown as Record<string, unknown>,
    );
    fence();
    return receipt;
  } catch (error) {
    if (owned) {
      try {
        await rm(output, { recursive: true, force: true });
      } catch (cleanup) {
        throw new AggregateError([error, cleanup], 'Receipt cleanup failed', { cause: error });
      }
    }
    throw error;
  }
}
