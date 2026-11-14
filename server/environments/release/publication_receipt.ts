/** Bind the actual uploaded ZIPs and inner sealed Linux payload before publication. */
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, realpath, stat } from 'node:fs/promises';
import { dirname, isAbsolute, join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { isDeepStrictEqual } from 'node:util';
import { check_head_review } from '../../ios_compatibility/check.ts';
import { is_record } from '../../core/records.ts';
import { sha256_file } from '../backup/evidence.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { with_zip_member } from '../updates/zip.ts';
import { parse_optional_runtime_contract } from '../updates/logic/runtime_contract.ts';
import {
  SUPPORTED_UPDATE_SCHEMA_FLOOR,
  UPDATE_ARTIFACT_RETENTION_DAYS,
} from '../updates/configuration.ts';
import {
  MAC_RUNTIME_ARCHIVE_NAME,
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
} from '../updates/mac/configuration.ts';
import type { PublishedMacUpdate, PublishedUpdate } from '../updates/contracts.ts';
import {
  APP_ARCHIVE_NAME,
  APP_FORMAT,
  ARCHIVE_MAX_COMPRESSED_BYTES,
  PUBLICATION_RECEIPT_NAME,
  RUNTIME_INVENTORY_PATH,
} from './configuration.ts';
import { PUBLICATION_MAC_VALIDATION_SECONDS } from './publication_configuration.ts';
import { extract_app_archive, remove_owned_tree } from './archive.ts';
import { validate_app_manifest } from './identity.ts';
import type { ReleaseExecution } from './contracts.ts';
import { manifest_identity } from './logic/manifest.ts';
import { migration_frontier, read_migration_graph } from './migrations.ts';
import { assert_release_execution, run_release_command } from './process.ts';
import {
  publication_id,
  publication_json_file,
  require_clean_publication_source,
  require_publication_run,
} from './publication_identity.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';

export type PublicationReceipt = Omit<PublishedUpdate, 'proof_artifact_id' | 'mac'> & {
  readonly mac: PublishedMacUpdate;
};
export interface PublicationReceiptOptions {
  readonly source_root: string;
  readonly linux_artifact_zip: string;
  readonly linux_build_artifact_zip: string;
  readonly mac_artifact_zip: string;
  readonly output_directory: string;
  readonly artifact_id: string;
  readonly linux_build_artifact_id: string;
  readonly mac_artifact_id: string;
  readonly mac_build_result: string;
}
function record(value: unknown): Record<string, unknown> {
  if (!is_record(value)) throw new Error('Invalid publication evidence object');
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value))
    throw new Error('Publication evidence requires a safe integer');
  return value;
}
function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value) || value.length !== 64)
    throw new Error('Invalid publication SHA256');
  return value;
}
async function json_file(path: string): Promise<Record<string, unknown>> {
  return record(await publication_json_file(path));
}
export async function write_publication_receipt(
  options: PublicationReceiptOptions,
  source_run: PublicationSourceRun,
  execution: ReleaseExecution,
  github_token: string,
): Promise<PublicationReceipt> {
  const fence = () => assert_release_execution(execution);
  fence();
  require_publication_run(source_run);
  if (execution.deadline === null || !Number.isFinite(execution.deadline))
    throw new Error('Receipt requires the original finite publication deadline');
  const signal = AbortSignal.any([
    execution.signal,
    AbortSignal.timeout(
      Math.max(0, Math.ceil((execution.deadline - performance.now() / 1000) * 1000)),
    ),
  ]);
  const artifact_id = integer(Number(publication_id(options.artifact_id, 'artifact ID'))),
    mac_artifact_id = integer(Number(publication_id(options.mac_artifact_id, 'Mac artifact ID')));
  publication_id(options.linux_build_artifact_id, 'Linux evidence artifact ID');
  if (!github_token) throw new Error('Artifact binding requires the current GitHub token');
  for (const path of [
    options.source_root,
    options.linux_artifact_zip,
    options.linux_build_artifact_zip,
    options.mac_artifact_zip,
    options.output_directory,
  ])
    if (!isAbsolute(path) || path.includes('\0'))
      throw new Error('Publication paths must be absolute');
  const source = await realpath(options.source_root),
    output = options.output_directory;
  let owned = false;
  try {
    fence();
    await mkdir(output, { mode: 0o700 });
    owned = true;
    await require_clean_publication_source(source, source_run.source_sha, execution);
    const artifacts = [];
    for (const input of [
      { path: options.linux_artifact_zip, id: options.artifact_id, name: 'panels-app' },
      {
        path: options.linux_build_artifact_zip,
        id: options.linux_build_artifact_id,
        name: 'panels-publication-build',
      },
      { path: options.mac_artifact_zip, id: options.mac_artifact_id, name: 'panels-mac-runtime' },
    ]) {
      fence();
      const response = await fetch(
        `https://api.github.com/repos/${source_run.repository}/actions/artifacts/${input.id}`,
        {
          headers: {
            Accept: 'application/vnd.github+json',
            Authorization: 'Bearer ' + github_token,
            'X-GitHub-Api-Version': '2022-11-28',
          },
          redirect: 'error',
          signal,
        },
      );
      if (!response.ok) {
        await response.body?.cancel().catch(() => {});
        throw new Error('GitHub artifact metadata is unavailable');
      }
      const metadata = record(JSON.parse(await response.text())),
        run = record(metadata.workflow_run),
        info = await stat(input.path),
        sha256 = await sha256_file(input.path);
      if (
        integer(metadata.id) !== Number(input.id) ||
        metadata.name !== input.name ||
        metadata.expired !== false ||
        integer(run.id) !== Number(source_run.run_id) ||
        run.head_sha !== source_run.source_sha ||
        run.head_branch !== 'main' ||
        integer(run.repository_id) !== integer(run.head_repository_id) ||
        !info.isFile() ||
        !Number.isSafeInteger(info.size) ||
        integer(metadata.size_in_bytes) !== info.size ||
        metadata.digest !== 'sha256:' + sha256
      )
        throw new Error('Artifact ZIP differs from trusted GitHub identity or bytes');
      artifacts.push({ ...input, sha256, metadata });
    }
    fence();
    const work = await mkdtemp(join(output, '.receipt-')),
      linux = join(work, 'linux'),
      mac_root = join(work, 'mac');
    await mkdir(linux, { mode: 0o700 });
    await mkdir(mac_root, { mode: 0o700 });
    const member = async (zip: string, name: string, destination: string) => {
      await with_zip_member(zip, name, (stream) =>
        pipeline(stream, createWriteStream(destination, { flags: 'wx', mode: 0o600 }), { signal }),
      );
      fence();
    };
    await member(options.linux_artifact_zip, APP_ARCHIVE_NAME, join(linux, APP_ARCHIVE_NAME));
    for (const name of ['linux-publication-build.json', 'package-licenses.json'])
      await member(options.linux_build_artifact_zip, name, join(linux, name));
    for (const name of [MAC_RUNTIME_ARCHIVE_NAME, 'mac-runtime-publication.json'])
      await member(options.mac_artifact_zip, name, join(mac_root, name));
    const build = await json_file(join(linux, 'linux-publication-build.json')),
      producer = record(build.source_run);
    if (integer(build.version) !== 1) throw new Error('Unsupported Linux publication evidence');
    for (const field of [
      'repository',
      'source_sha',
      'event',
      'ref',
      'workflow_path',
      'workflow_ref',
      'run_id',
      'run_attempt',
    ] as const)
      if (producer[field] !== source_run[field])
        throw new Error('Linux build differs from the current publication attempt');
    const manifest_value = record(build.manifest),
      identity = manifest_identity(
        { format: APP_FORMAT, ...manifest_value },
        source_run.source_sha,
      ),
      runtime_contract = parse_optional_runtime_contract(manifest_value.runtime_contract);
    if (runtime_contract?.version !== 2)
      throw new Error('Receipt requires the Node runtime contract');
    const manifest = { ...identity, runtime_contract },
      frontier = [...migration_frontier(await read_migration_graph(source))].sort();
    if (
      build.schema_floor !== SUPPORTED_UPDATE_SCHEMA_FLOOR ||
      !isDeepStrictEqual(build.schema_frontier, frontier)
    )
      throw new Error('Publication schema evidence differs from the selected source');
    const admission = record(build.admission),
      seconds = admission.seconds;
    if (
      admission.status !== 'passed' ||
      typeof seconds !== 'number' ||
      !Number.isFinite(seconds) ||
      seconds < 0
    )
      throw new Error('Publication lacks successful sealed runtime admission');
    const archive = record(build.archive),
      licenses = record(build.licenses),
      archive_path = join(linux, APP_ARCHIVE_NAME),
      info = await stat(archive_path),
      archive_sha256 = digest(archive.sha256);
    if (
      archive.filename !== APP_ARCHIVE_NAME ||
      licenses.filename !== 'package-licenses.json' ||
      !Number.isSafeInteger(info.size) ||
      info.size <= 0 ||
      info.size > ARCHIVE_MAX_COMPRESSED_BYTES ||
      info.size !== integer(archive.size) ||
      (await sha256_file(archive_path)) !== archive_sha256
    )
      throw new Error('Publication archive bytes changed');
    const sealed = join(work, 'sealed'),
      extracted = await extract_app_archive(
        archive_path,
        sealed,
        archive_sha256,
        source_run.source_sha,
        execution.child_processes,
        undefined,
        execution,
      );
    const actual_manifest = await validate_app_manifest(join(sealed, 'manifest.json'), {
      expected_sha: source_run.source_sha,
      require_runtime: true,
    });
    if (
      !isDeepStrictEqual(actual_manifest, manifest) ||
      !isDeepStrictEqual(extracted, actual_manifest) ||
      (await sha256_file(join(sealed, RUNTIME_INVENTORY_PATH))) !==
        digest(build.runtime_inventory_sha256) ||
      build.runtime_inventory_sha256 !== runtime_contract.inventory_sha256
    )
      throw new Error('Actual sealed manifest or runtime inventory differs from build evidence');
    if ((await sha256_file(join(linux, 'package-licenses.json'))) !== digest(licenses.sha256))
      throw new Error('Publication license inventory bytes changed');
    const inventory = await json_file(join(linux, 'package-licenses.json'));
    if (
      integer(inventory.version) !== 1 ||
      !Array.isArray(inventory.packages) ||
      integer(licenses.package_count) !== inventory.packages.length ||
      !isDeepStrictEqual(licenses.missing_own_licenses, inventory.missing_own_licenses)
    )
      throw new Error('Publication license evidence differs from its inventory');
    const mac_receipt = await json_file(join(mac_root, 'mac-runtime-publication.json'));
    if (mac_receipt.run_attempt !== source_run.run_attempt)
      throw new Error('Mac publication differs from the current attempt');
    fence();
    const environment = {
      PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin',
      HOME: work,
      TMPDIR: work,
      LANG: 'C.UTF-8',
      PYTHONDONTWRITEBYTECODE: '1',
    };
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
      { timeout_seconds: PUBLICATION_MAC_VALIDATION_SECONDS, environment, cwd: source },
      execution,
    );
    const mac = record(JSON.parse(checked_mac.stdout));
    if (
      integer(mac.artifact_id) !== mac_artifact_id ||
      integer(mac.archive_size) <= 0 ||
      integer(mac.archive_size) > MAX_MAC_RUNTIME_ARCHIVE_BYTES
    )
      throw new Error('Mac validation differs from its actual artifact');
    const published_mac: PublishedMacUpdate = {
      artifact_id: mac_artifact_id,
      archive_sha256: digest(mac.archive_sha256),
      archive_size: integer(mac.archive_size),
    };
    const ios_review = await check_head_review(source, source_run.source_sha, execution);
    for (const artifact of artifacts)
      if ((await sha256_file(artifact.path)) !== artifact.sha256)
        throw new Error('Bound artifact ZIP changed during receipt validation');
    await remove_owned_tree(work);
    fence();
    const receipt: PublicationReceipt = {
      publication_run_id: integer(Number(source_run.run_id)),
      artifact_id,
      manifest: actual_manifest,
      archive_sha256,
      mac: published_mac,
      schema_floor: SUPPORTED_UPDATE_SCHEMA_FLOOR,
      expires_at: new Date(Date.now() + UPDATE_ARTIFACT_RETENTION_DAYS * 86_400_000).toISOString(),
    };
    replace_existing_json(join(output, 'publication-audit.json'), {
      source_run,
      artifacts: artifacts.map(({ metadata }) => metadata),
      ios_review,
    });
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
        await remove_owned_tree(output);
      } catch (cleanup) {
        throw new AggregateError([error, cleanup], 'Receipt cleanup failed', { cause: error });
      }
    }
    throw error;
  }
}
