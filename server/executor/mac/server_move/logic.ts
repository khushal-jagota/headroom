/** Rules of a server move on the target machine. No I/O. */
import { join, posix } from 'node:path';
import type { ChecksumManifest } from '../../../environments/server_state/contracts.ts';
import type {
  ServerMoveCommitReply,
  ServerMoveFiles,
  ServerMoveInstallRequest,
  ServerMoveInstallResult,
  ServerMoveInstruction,
  ServerMovePackage,
  ServerMovePhase,
  ServerMovePlatform,
  ServerMoveProbeJob,
  ServerMoveReportReply,
} from '../../../server_move/contracts.ts';
import { SERVER_MOVE_DEFAULT_SERVICE } from '../../../server_move/contracts.ts';
import { local_server_address } from '../../../server_link/logic.ts';
import { is_record } from '../../../core/records.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import { failure_detail } from '../failure_detail.ts';
import {
  MAX_SERVER_MOVE_PACKAGE_BYTES,
  MAX_SERVER_MOVE_REASON_CHARACTERS,
} from './configuration.ts';
import type { ServerMoveRunRecord } from './contracts.ts';
import { ServerMoveFailure } from './contracts.ts';

const PHASES: readonly ServerMovePhase[] = [
  'preparing',
  'copying',
  'paused',
  'committing',
  'moved',
  'failed',
];
const SHA256 = /^[0-9a-f]{64}$/;
const REVISION = /^[0-9a-f]{40}$/;

/** A move id names a folder, so it is one plain path component. */
export function valid_move_id(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
}
function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== '';
}
function invalid(what: string): never {
  throw new ServerMoveFailure(`The server sent an invalid ${what}.`);
}
function parse_package(value: unknown): ServerMovePackage | null {
  if (value === null) return null;
  if (
    !is_record(value) ||
    (value.platform !== 'darwin' && value.platform !== 'linux') ||
    typeof value.revision !== 'string' ||
    !REVISION.test(value.revision) ||
    typeof value.archive_sha256 !== 'string' ||
    !SHA256.test(value.archive_sha256) ||
    !Number.isSafeInteger(value.archive_size) ||
    (value.archive_size as number) < 1 ||
    (value.archive_size as number) > MAX_SERVER_MOVE_PACKAGE_BYTES
  )
    invalid('server package');
  return {
    platform: value.platform,
    revision: value.revision,
    archive_sha256: value.archive_sha256,
    archive_size: value.archive_size as number,
  };
}
export function parse_instruction(value: unknown): ServerMoveInstruction {
  if (
    !is_record(value) ||
    !valid_move_id(value.move_id) ||
    !PHASES.includes(value.phase as ServerMovePhase) ||
    !text(value.server_url) ||
    !Number.isSafeInteger(value.port) ||
    (value.port as number) < 1 ||
    (value.port as number) > 65535 ||
    !text(value.server_id) ||
    !Number.isSafeInteger(value.move_number) ||
    (value.move_number as number) < 0
  )
    invalid('move instruction');
  const ingress = value.trusted_ingress;
  if (
    ingress !== null &&
    !(is_record(ingress) && text(ingress.provider) && typeof ingress.allowed_login === 'string')
  )
    invalid('move instruction');
  return {
    move_id: value.move_id,
    phase: value.phase as ServerMovePhase,
    server_url: value.server_url,
    port: value.port as number,
    server_id: value.server_id,
    move_number: value.move_number as number,
    package: parse_package(value.package),
    trusted_ingress:
      ingress === null
        ? null
        : { provider: ingress.provider as string, allowed_login: ingress.allowed_login as string },
  };
}
/**
 * A listed name is a plain relative path inside the staged folder: no absolute path, no empty,
 * `.` or `..` part, no backslash and no control character.
 */
export function valid_relative_path(value: string): boolean {
  return (
    value !== '' &&
    !value.startsWith('/') &&
    !value.includes('\\') &&
    !/[\u0000-\u001f\u007f]/u.test(value) &&
    value.split('/').every((part) => part !== '' && part !== '.' && part !== '..') &&
    posix.normalize(value) === value
  );
}
export function parse_files(value: unknown, complete: boolean): ServerMoveFiles {
  if (!is_record(value) || value.complete !== complete || !is_record(value.manifest))
    invalid('file list');
  const manifest: ChecksumManifest = Object.create(null);
  for (const [path, digest] of Object.entries(value.manifest)) {
    if (!valid_relative_path(path) || typeof digest !== 'string' || !SHA256.test(digest))
      invalid('file list');
    manifest[path] = digest;
  }
  return { complete, manifest };
}
export function parse_report_reply(value: unknown): ServerMoveReportReply {
  if (!is_record(value) || typeof value.commit !== 'boolean') invalid('report answer');
  return { commit: value.commit };
}
export function parse_probe_job(value: unknown): ServerMoveProbeJob {
  if (!is_record(value) || !valid_move_id(value.job_id) || !text(value.nonce)) invalid('test job');
  return { job_id: value.job_id, nonce: value.nonce };
}
export function parse_commit_reply(value: unknown): ServerMoveCommitReply {
  if (!is_record(value) || !text(value.server_id) || !Number.isSafeInteger(value.move_number))
    invalid('commit answer');
  return { server_id: value.server_id, move_number: value.move_number as number };
}
export function parse_install_result(value: unknown, root: string): ServerMoveInstallResult {
  if (
    !is_record(value) ||
    value.root !== root ||
    !text(value.data_directory) ||
    typeof value.executor_link !== 'string'
  )
    throw new ServerMoveFailure('The installer gave an invalid answer.');
  let link: string | null;
  try {
    link = local_server_address(value.executor_link);
  } catch {
    link = null;
  }
  if (link === null) throw new ServerMoveFailure('The installer gave an invalid executor link.');
  return { root, data_directory: value.data_directory, executor_link: link };
}
/** Why this package cannot be installed here, or null. */
export function package_problem(
  pack: ServerMovePackage,
  platform: ServerMovePlatform,
): string | null {
  return pack.platform === platform
    ? null
    : `The server package is for ${pack.platform}, but this machine runs ${platform}.`;
}
/** Staged names that the complete list no longer has. */
export function extra_staged_files(
  staged: readonly string[],
  manifest: ChecksumManifest,
): string[] {
  return staged.filter((path) => !Object.hasOwn(manifest, path));
}
/** The platform default root of a first server installation. */
export function server_install_root(platform: ServerMovePlatform, home: string): string {
  return platform === 'darwin'
    ? join(home, 'Library', 'Application Support', 'PanelsHome')
    : join(home, '.local', 'share', 'panels-home');
}
/** The Node that runs the installer: Homebrew's on Apple Silicon, this machine code's on Linux. */
export function host_node(platform: ServerMovePlatform, own_node: string): string {
  return platform === 'darwin' ? '/opt/homebrew/bin/node' : own_node;
}
export function install_request(
  record: ServerMoveRunRecord,
  paths: { readonly root: string; readonly archive: string; readonly data_directory: string },
  machine_id: string,
): ServerMoveInstallRequest {
  const { instruction } = record;
  return {
    command: 'install',
    root: paths.root,
    service: SERVER_MOVE_DEFAULT_SERVICE,
    port: instruction.port,
    archive: paths.archive,
    archive_sha256: instruction.package.archive_sha256,
    revision: instruction.package.revision,
    data_directory: paths.data_directory,
    trusted_ingress: instruction.trusted_ingress,
    held: {
      move_id: instruction.move_id,
      target_machine_id: machine_id,
      server_url: instruction.server_url,
      server_id: instruction.server_id,
      move_number: instruction.move_number,
    },
  };
}
/** The plain reason that the old server shows the owner. */
export function failure_reason(error: unknown): string {
  const reason =
    error instanceof ServerMoveFailure
      ? error.message
      : error instanceof ExecutorHttpError
        ? `A server answered ${error.status}${error.message ? `: ${error.message}` : '.'}`
        : `The move failed on the machine: ${failure_detail(error)}`;
  return Array.from(reason).slice(0, MAX_SERVER_MOVE_REASON_CHARACTERS).join('');
}
