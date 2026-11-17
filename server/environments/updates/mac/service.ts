import { ORIGINAL_MAC_MACHINE_ID, valid_uuid } from '../../../execution_identity.ts';
import { counter } from '../../../executor/logic/counters.ts';
/** Pinned Mac bytes and reports survive publication expiry and later VPS failures. */
import { createHash } from 'node:crypto';
import { closeSync, constants, fsyncSync, openSync, readFileSync, statSync } from 'node:fs';
import { mkdir, mkdtemp, open, readdir, rename, rm, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import yauzl from 'yauzl';
import type { ZipFile } from 'yauzl';
import { inspect_mac_archive } from '../../release/archive.ts';
import {
  MacArchiveInspectionError,
  MacArchiveInspectionUnsettled,
  AppValidationError,
} from '../../release/contracts.ts';
import { atomic_write_json, missing, UpdateConflict } from '../attempts.ts';
import { attempt_to_dict, is_object, parse_attempt } from '../codec.ts';
import type { PublishedUpdate, UpdateAttempt } from '../contracts.ts';
import { UPDATE_AVAILABILITY_REFRESH_SECONDS } from '../configuration.ts';
import { with_zip_member } from '../zip.ts';
import type {
  MacUpdateCheck,
  MacUpdateCompositionContext,
  MacUpdateHealthRead,
  MacUpdatePhase,
  MacUpdateReport,
  MacUpdateServiceContract,
  MacUpdateStatus,
  MacUpdateTarget,
} from './contracts.ts';
import {
  MAC_RUNTIME_ARCHIVE_NAME,
  LINUX_RUNTIME_ARCHIVE_NAME,
  LINUX_RUNTIME_PROOF_NAME,
  MAC_UPDATE_SCHEMA_VERSION,
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
  MAX_MAC_UPDATE_DETAIL_CHARACTERS,
  MAC_RUNTIME_INSPECTION_TIMEOUT_SECONDS,
} from './configuration.ts';

class InvalidMacArchive extends Error {}
const target_states = new Set(['preparing', 'installing', 'done']);
const file = (path: string): boolean => {
  try {
    return statSync(path).isFile();
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
};
function decode_json(bytes: Buffer): unknown {
  return JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes));
}
function json(path: string): unknown {
  return decode_json(readFileSync(path));
}
function fullmatch(pattern: RegExp, value: unknown): boolean {
  return typeof value === 'string' && pattern.exec(value)?.[0] === value;
}
/** A format failure is not a filesystem permission or read failure. */
function archive_failure(error: unknown): never {
  if (error instanceof Error && 'errno' in error && typeof error.errno === 'number') throw error;
  throw new InvalidMacArchive('The Mac runtime archive is invalid.', { cause: error });
}
async function zip_runtime_size(
  path: string,
  signal: AbortSignal,
  archive_name: string,
  proof_name: string,
): Promise<number> {
  signal.throwIfAborted();
  let archive: ZipFile;
  try {
    archive = await new Promise<ZipFile>((resolve, reject) =>
      yauzl.open(
        path,
        { lazyEntries: true, autoClose: false, validateEntrySizes: true },
        (error, value) => {
          if (error) reject(error);
          else if (signal.aborted) {
            try {
              value!.close();
              reject(signal.reason);
            } catch (failure) {
              reject(new AggregateError([signal.reason, failure], 'Mac ZIP cleanup failed'));
            }
          } else resolve(value!);
        },
      ),
    );
  } catch (error) {
    if ((signal.aborted && error === signal.reason) || error instanceof AggregateError) throw error;
    return archive_failure(error);
  }
  let abort: (() => void) | undefined;
  try {
    return await new Promise<number>((resolve, reject) => {
      const names = new Set<string>();
      let duplicate = false,
        size: number | undefined;
      abort = () => {
        try {
          archive.close();
          reject(signal.reason);
        } catch (error) {
          reject(new AggregateError([signal.reason, error], 'Mac ZIP cleanup failed'));
        }
      };
      signal.addEventListener('abort', abort);
      archive.on('error', (error) => {
        try {
          archive_failure(error);
        } catch (failure) {
          reject(failure);
        }
      });
      archive.on('entry', (entry: yauzl.Entry) => {
        if (signal.aborted) {
          abort!();
          return;
        }
        if (names.has(entry.fileName)) duplicate = true;
        names.add(entry.fileName);
        if (entry.fileName === archive_name) size = entry.uncompressedSize;
        archive.readEntry();
      });
      archive.once('end', () => {
        if (
          duplicate ||
          size === undefined ||
          names.size > 2 ||
          [...names].some((name) => ![archive_name, proof_name].includes(name))
        )
          reject(new UpdateConflict('The Mac runtime artifact is invalid.'));
        else resolve(size);
      });
      if (signal.aborted) abort();
      else archive.readEntry();
    });
  } finally {
    if (abort) signal.removeEventListener('abort', abort);
    archive.close();
  }
}
export class MacUpdateService implements MacUpdateServiceContract {
  readonly root: string;
  readonly machine_id: string;
  readonly archive_root: string;
  readonly platform: string;
  private readonly context: MacUpdateCompositionContext;
  private readonly machines = new Map<string, MacUpdateService>();
  private linux_archive_owner: MacUpdateService | undefined;
  readonly attempts: MacUpdateCompositionContext['attempts'];
  readonly provider: MacUpdateCompositionContext['provider'];
  readonly current_app_sha: string | null;
  private readonly child_processes: MacUpdateCompositionContext['child_processes'];
  private readonly signal: AbortSignal;
  private readonly ready_release: () => PublishedUpdate | null;
  private readonly applied_path: string;
  private readonly emit: () => void;
  private preparing: Promise<void> = Promise.resolve();
  private linux_preparing = false;
  private last_preparation: { digest: string; at: number } | undefined;
  private readonly ready_archives = new Set<string>();
  private rejected_archive_digest: string | undefined;
  constructor(context: MacUpdateCompositionContext) {
    this.context = context;
    this.root = context.root;
    this.machine_id = context.machine_id ?? ORIGINAL_MAC_MACHINE_ID;
    this.archive_root = context.archive_root ?? context.root;
    this.platform = context.platform ?? 'darwin';
    this.attempts = context.attempts;
    this.provider = context.provider;
    this.current_app_sha = context.current_app_sha;
    this.child_processes = context.child_processes;
    this.signal = context.signal;
    this.ready_release = context.ready_release;
    this.emit = context.emit_change;
    this.applied_path = join(this.root, 'applied.json');
    this.signal.throwIfAborted();
    this.retain_applied(this.attempt());
  }
  for_machine(machine_id: string): MacUpdateServiceContract {
    if (machine_id === this.machine_id) return this;
    if (!valid_uuid(machine_id)) throw new TypeError('machine_id must be UUID');
    const platform = this.context.platform_for?.(machine_id) ?? 'darwin';
    let kept = this.machines.get(machine_id);
    if (kept === undefined || kept.platform !== platform) {
      const linux = platform === 'linux' ? this.linux_owner() : undefined;
      kept = new MacUpdateService({
        ...this.context,
        root: join(linux?.root ?? this.root, 'machines', machine_id),
        machine_id,
        archive_root: linux?.root ?? this.archive_root,
        platform,
        linux_registered: () => false,
      });
      kept.linux_archive_owner = linux;
      this.machines.set(machine_id, kept);
    }
    return kept;
  }
  private linux_owner(): MacUpdateService {
    if (this.linux_archive_owner !== undefined) return this.linux_archive_owner;
    if (this.platform === 'linux') return this;
    return (this.linux_archive_owner ??= new MacUpdateService({
      ...this.context,
      root: join(this.root, 'linux'),
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      archive_root: join(this.root, 'linux'),
      platform: 'linux',
    }));
  }
  private attempt(): UpdateAttempt | null {
    const attempt = this.attempts.read();
    return attempt === null ||
      this.machine_id === ORIGINAL_MAC_MACHINE_ID ||
      attempt.mac_attempt_id === null
      ? attempt
      : { ...attempt, mac_attempt_id: `${attempt.mac_attempt_id}.${this.machine_id}` };
  }
  private artifact(release: PublishedUpdate) {
    return this.platform === 'linux' ? (release.linux_executor ?? null) : release.mac;
  }
  private get archive_name(): string {
    return this.platform === 'linux' ? LINUX_RUNTIME_ARCHIVE_NAME : MAC_RUNTIME_ARCHIVE_NAME;
  }
  private get proof_name(): string {
    return this.platform === 'linux' ? LINUX_RUNTIME_PROOF_NAME : 'mac-runtime-publication.json';
  }
  private archive_path(release: PublishedUpdate): string {
    const mac = this.artifact(release);
    if (
      mac === null ||
      !Number.isSafeInteger(mac.artifact_id) ||
      !(mac.artifact_id >= 1) ||
      !fullmatch(/[0-9a-f]{64}/, mac.archive_sha256) ||
      !Number.isSafeInteger(mac.archive_size) ||
      !(mac.archive_size > 0 && mac.archive_size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES) ||
      !fullmatch(/[0-9a-f]{40}/, release.manifest.app_sha)
    )
      throw new UpdateConflict('The Mac publication identity is invalid.');
    return join(this.archive_root, 'archives', mac.archive_sha256 + '.tar.gz');
  }
  private async validate_archive(path: string, release: PublishedUpdate): Promise<void> {
    const mac = this.artifact(release)!;
    let inspected;
    try {
      inspected = await inspect_mac_archive(path, mac.archive_sha256, mac.archive_size, {
        child_processes: this.child_processes,
        signal: this.signal,
        deadline: performance.now() / 1000 + MAC_RUNTIME_INSPECTION_TIMEOUT_SECONDS,
        platform: this.platform === 'linux' ? 'linux' : 'darwin',
      });
    } catch (error) {
      if (!(error instanceof MacArchiveInspectionUnsettled)) throw error;
      try {
        await error.settled;
      } catch (failure) {
        if (failure !== error.cause)
          throw new AggregateError([error.cause, failure], 'Mac inspection cleanup failed');
      }
      throw error.cause;
    }
    const { manifest, python, node, entrypoint, native_linux } = inspected;
    const linux = this.platform === 'linux';
    const native = linux
      ? native_linux?.version === 2 &&
        native_linux.app_sha === release.manifest.app_sha &&
        native_linux.panels &&
        entrypoint
      : node && entrypoint;
    if (
      !is_object(manifest) ||
      manifest.schema_version !== 1 ||
      manifest.application_revision !== release.manifest.app_sha ||
      manifest.platform !== (linux ? 'linux' : 'darwin') ||
      manifest.architecture !== (linux ? 'x86_64' : 'arm64') ||
      (linux && manifest.role !== 'executor') ||
      !(python || native)
    )
      throw new UpdateConflict('The Mac runtime does not match its source and platform.');
  }
  private read_applied(): UpdateAttempt | null {
    let source: Buffer;
    try {
      source = readFileSync(this.applied_path);
    } catch (error) {
      if (missing(error)) return null;
      throw error;
    }
    try {
      const attempt = parse_attempt(decode_json(source));
      return this.artifact(attempt.release) === null ? null : attempt;
    } catch (error) {
      console.warn('The applied update attempt record is invalid', this.applied_path, error);
      return null;
    } // Source tolerates legacy request/invalid attempt JSON here only.
  }
  private retain_applied(attempt: UpdateAttempt | null): void {
    if (
      attempt !== null &&
      attempt.state === 'done' &&
      attempt.mac_attempt_id !== null &&
      this.artifact(attempt.release) !== null &&
      attempt.to_sha === this.current_app_sha &&
      file(this.archive_path(attempt.release))
    ) {
      const retained = this.read_applied();
      if (
        retained === null ||
        retained.attempt_id !== attempt.attempt_id ||
        retained.mac_attempt_id !== attempt.mac_attempt_id
      )
        atomic_write_json(this.applied_path, attempt_to_dict(attempt));
    }
  }
  private queue_preparation(work: () => Promise<void>): Promise<void> {
    const actual = this.preparing.then(work);
    this.preparing = actual.catch(() => {});
    return actual;
  }
  async settled(): Promise<void> {
    const seen = new Set<MacUpdateService>();
    const drain = async (service: MacUpdateService): Promise<void> => {
      if (seen.has(service)) return;
      seen.add(service);
      await service.preparing;
      await Promise.all([
        ...[...service.machines.values()].map(drain),
        ...(service.linux_archive_owner ? [drain(service.linux_archive_owner)] : []),
      ]);
    };
    await drain(this);
  }
  prepare(release: PublishedUpdate): Promise<void> {
    this.signal.throwIfAborted();
    const owner = this.platform === 'linux' ? this.linux_owner() : this;
    return owner.queue_preparation(async () => {
      this.signal.throwIfAborted();
      await owner.prepare_once(release);
      if (owner.platform === 'darwin' && !owner.signal.aborted) {
        try {
          if (owner.context.linux_registered?.()) owner.schedule_linux_preparation(release);
        } catch (error) {
          console.warn('Linux executor archive preparation could not start', error);
        }
      }
    });
  }
  private schedule_linux_preparation(release: PublishedUpdate | null): void {
    if (this.signal.aborted || release?.linux_executor == null) return;
    const owner = this.linux_owner(),
      digest = release.linux_executor.archive_sha256;
    if (
      owner.ready_archives.has(digest) ||
      owner.rejected_archive_digest === digest ||
      owner.linux_preparing
    )
      return;
    const now = performance.now() / 1000;
    if (
      owner.last_preparation?.digest === digest &&
      now - owner.last_preparation.at < UPDATE_AVAILABILITY_REFRESH_SECONDS
    )
      return;
    owner.last_preparation = { digest, at: now };
    owner.linux_preparing = true;
    const actual = owner.queue_preparation(async () => {
      try {
        owner.signal.throwIfAborted();
        await owner.prepare_once(release);
        owner.signal.throwIfAborted();
        owner.retain_applied(owner.attempt());
        await owner.prune_linux_archives(release);
        owner.signal.throwIfAborted();
        owner.ready_archives.add(digest);
        owner.emit();
      } finally {
        owner.linux_preparing = false;
      }
    });
    void actual.catch((error) => {
      console.warn('Linux executor archive preparation failed', error);
    });
  }
  private async prune_linux_archives(release: PublishedUpdate): Promise<void> {
    const digest = release.linux_executor!.archive_sha256;
    const retained_path = join(this.root, 'retained-archives.json');
    let previous: string[];
    try {
      previous = (json(retained_path) as { archives: string[] }).archives;
    } catch (error) {
      if (!missing(error)) throw error;
      previous = [];
    }
    const active = this.read_applied();
    const active_digest =
      active?.to_sha === this.current_app_sha
        ? active?.release.linux_executor?.archive_sha256
        : undefined;
    const other =
      (active_digest !== digest ? active_digest : undefined) ??
      previous.find((value) => value !== digest);
    const keep = other === undefined ? [digest] : [digest, other];
    this.signal.throwIfAborted();
    atomic_write_json(retained_path, { archives: keep });
    for (const value of this.ready_archives)
      if (!keep.includes(value)) this.ready_archives.delete(value);
    for (const name of await readdir(join(this.archive_root, 'archives'))) {
      this.signal.throwIfAborted();
      if (name.endsWith('.tar.gz') && !keep.includes(name.slice(0, -7)))
        await unlink(join(this.archive_root, 'archives', name));
    }
  }
  private async prepare_once(release: PublishedUpdate): Promise<void> {
    try {
      this.retain_applied(this.attempt());
      const mac = this.artifact(release);
      if (mac === null) return;
      const destination = this.archive_path(release);
      if (file(destination)) {
        await this.validate_archive(destination, release);
        return;
      }
      if (this.provider === null)
        throw new UpdateConflict('The Mac runtime provider is unavailable.');
      const parent = join(this.archive_root, 'archives');
      await mkdir(parent, { recursive: true, mode: 0o700 });
      this.signal.throwIfAborted();
      const temporary = await mkdtemp(join(parent, '.download-'));
      try {
        this.signal.throwIfAborted();
        const downloaded = join(temporary, 'artifact.zip');
        await this.provider.download_artifact(mac.artifact_id, downloaded, this.signal);
        this.signal.throwIfAborted();
        try {
          if (
            (await zip_runtime_size(
              downloaded,
              this.signal,
              this.archive_name,
              this.proof_name,
            )) !== mac.archive_size
          )
            throw new UpdateConflict('The Mac runtime artifact size differs.');
          this.signal.throwIfAborted();
          const candidate = join(temporary, this.archive_name);
          const output = await open(candidate, 'wx');
          try {
            try {
              await with_zip_member(downloaded, this.archive_name, async (source) => {
                const interruption =
                  this.signal.reason instanceof Error
                    ? this.signal.reason
                    : new DOMException('Mac prepare stopped', 'AbortError');
                const abort = () => source.destroy(interruption);
                this.signal.addEventListener('abort', abort);
                try {
                  this.signal.throwIfAborted();
                  for await (const chunk of source) {
                    this.signal.throwIfAborted();
                    await output.writeFile(chunk);
                  }
                  this.signal.throwIfAborted();
                  await output.sync();
                } catch (error) {
                  if (this.signal.aborted && error === interruption) throw this.signal.reason;
                  throw error;
                } finally {
                  this.signal.removeEventListener('abort', abort);
                }
              });
            } catch (error) {
              if (this.signal.aborted && error === this.signal.reason) throw error;
              archive_failure(error);
            }
          } finally {
            await output.close();
          }
          this.signal.throwIfAborted();
          await this.validate_archive(candidate, release);
        } catch (error) {
          if (
            this.platform === 'linux' &&
            !this.signal.aborted &&
            (error instanceof UpdateConflict ||
              error instanceof InvalidMacArchive ||
              error instanceof MacArchiveInspectionError ||
              error instanceof AppValidationError ||
              error instanceof SyntaxError)
          )
            this.rejected_archive_digest = mac.archive_sha256;
          throw error;
        }
        this.signal.throwIfAborted();
        await rename(join(temporary, this.archive_name), destination);
        const directory = openSync(parent, constants.O_RDONLY);
        try {
          fsyncSync(directory);
        } finally {
          closeSync(directory);
        }
      } finally {
        await rm(temporary, { recursive: true, force: true });
      }
    } catch (error) {
      if (error instanceof MacArchiveInspectionError)
        throw new UpdateConflict(
          error.kind === 'archive' ? 'The Mac runtime archive is invalid.' : error.message,
          { cause: error },
        );
      if (error instanceof InvalidMacArchive)
        throw new UpdateConflict('The Mac runtime archive is invalid.', { cause: error });
      throw error;
    }
  }
  private selected_attempt(): UpdateAttempt | null {
    const attempt = this.attempt();
    this.retain_applied(attempt);
    if (
      attempt !== null &&
      target_states.has(attempt.state) &&
      this.artifact(attempt.release) !== null &&
      attempt.mac_attempt_id !== null &&
      (attempt.state !== 'done' || attempt.to_sha === this.current_app_sha)
    )
      return attempt;
    const retained = this.read_applied();
    return retained !== null && retained.to_sha === this.current_app_sha ? retained : null;
  }
  private target(): MacUpdateTarget | null {
    const attempt = this.selected_attempt();
    if (attempt === null) return null;
    const mac = this.artifact(attempt.release)!,
      request_id = attempt.mac_attempt_id!;
    if (this.platform === 'linux' && !this.linux_owner().ready_archives.has(mac.archive_sha256))
      return null;
    if (!file(this.archive_path(attempt.release))) {
      if (this.platform === 'linux') return null;
      throw new UpdateConflict('The Mac runtime archive for this update is unavailable.');
    }
    return {
      request_id,
      application_revision: attempt.to_sha,
      mode: attempt.confirm_activity ? attempt.mode : 'idle',
      activate: attempt.state === 'done' && attempt.to_sha === this.current_app_sha,
      archive_sha256: mac.archive_sha256,
      archive_size: counter(mac.archive_size),
      archive_path: `/api/executor/update/${request_id}/archive`,
    };
  }
  private prefetch_release(): PublishedUpdate | null {
    const release = this.ready_release();
    if (release === null || this.artifact(release) === null) return null;
    if (
      this.platform === 'linux' &&
      !this.linux_owner().ready_archives.has(this.artifact(release)!.archive_sha256)
    )
      return null;
    try {
      return file(this.archive_path(release)) ? release : null;
    } catch (error) {
      if (error instanceof UpdateConflict) return null;
      throw error;
    }
  }
  check(): MacUpdateCheck {
    if (this.platform === 'linux')
      this.schedule_linux_preparation(this.selected_attempt()?.release ?? this.ready_release());
    const release = this.prefetch_release(),
      mac = release === null ? null : this.artifact(release);
    return {
      schema_version: MAC_UPDATE_SCHEMA_VERSION,
      target: this.target(),
      prefetch:
        release && mac
          ? {
              application_revision: release.manifest.app_sha,
              archive_sha256: mac.archive_sha256,
              archive_size: counter(mac.archive_size),
              archive_path: `/api/executor/update/prefetch/${mac.archive_sha256}`,
            }
          : null,
    };
  }
  private report_path(request_id: string): string {
    return join(
      this.root,
      'reports',
      createHash('sha256').update(request_id).digest('hex') + '.json',
    );
  }
  private saved_status(request_id: string): MacUpdateStatus | null {
    let value: unknown;
    try {
      value = json(this.report_path(request_id));
    } catch (error) {
      if (missing(error)) return null;
      throw error;
    }
    if (!is_object(value) || !is_object(value.status))
      throw new TypeError('invalid Mac status record');
    const status = value.status;
    for (const key of ['request_id', 'target_revision', 'phase'])
      if (!Object.hasOwn(status, key)) throw new TypeError(`missing Mac status field: ${key}`);
    for (const key of Object.keys(status))
      if (!['request_id', 'target_revision', 'phase', 'detail'].includes(key))
        throw new TypeError(`unknown Mac status field: ${key}`);
    return { ...status, detail: status.detail ?? null } as unknown as MacUpdateStatus;
  }
  status(): MacUpdateStatus | null {
    const current = this.attempt();
    if (
      current !== null &&
      target_states.has(current.state) &&
      this.artifact(current.release) === null
    )
      return {
        request_id: current.mac_attempt_id || current.attempt_id,
        target_revision: current.to_sha,
        phase: 'unavailable',
        detail: 'This publication has no runtime for this machine. A manual update is required.',
      };
    const attempt = this.selected_attempt();
    if (attempt === null || attempt.mac_attempt_id === null) return null;
    return (
      this.saved_status(attempt.mac_attempt_id) ?? {
        request_id: attempt.mac_attempt_id,
        target_revision: attempt.to_sha,
        phase: 'waiting',
        detail: 'This machine waits for this update.',
      }
    );
  }
  phase(attempt_id: string): MacUpdatePhase | null {
    const saved = this.saved_status(attempt_id);
    return saved === null || saved.phase === 'unavailable' ? null : saved.phase;
  }
  reported_at(attempt_id: string): number | null {
    try {
      return statSync(this.report_path(attempt_id)).mtimeMs / 1000;
    } catch (error) {
      if (missing(error)) return null;
      throw error;
    }
  }
  report(report: MacUpdateReport, health: MacUpdateHealthRead | null): MacUpdateStatus {
    const detail = report.detail ?? null,
      identity = report.identity ?? null,
      leased = health?.presence === 'present' ? health.identity : null,
      target = this.target();
    if (
      target === null ||
      report.request_id !== target.request_id ||
      report.application_revision !== target.application_revision ||
      (detail !== null && Array.from(detail).length > MAX_MAC_UPDATE_DETAIL_CHARACTERS)
    )
      throw new UpdateConflict('The Mac update report does not match its target.');
    if (
      report.phase === 'current' &&
      (!target.activate ||
        identity === null ||
        identity.application_revision !== target.application_revision ||
        !isDeepStrictEqual(leased, identity))
    )
      throw new UpdateConflict('The target Mac runtime is not the present, leased one.');
    if (
      report.phase === 'ready_to_restart' &&
      (identity === null || !isDeepStrictEqual(leased, identity))
    )
      throw new UpdateConflict('The Mac stop report does not match its present process.');
    const result = {
      request_id: target.request_id,
      target_revision: target.application_revision,
      phase: report.phase,
      detail,
    };
    atomic_write_json(this.report_path(target.request_id), {
      status: result,
      identity,
    });
    this.emit();
    return result;
  }
  archive(request_id: string): string {
    for (const attempt of [this.selected_attempt(), this.read_applied()])
      if (attempt !== null && attempt.mac_attempt_id === request_id) {
        const path = this.archive_path(attempt.release);
        if (file(path)) return path;
      }
    throw new UpdateConflict('The requested Mac runtime is not an update target.');
  }
  prefetch_archive(archive_sha256: string): string {
    const release = this.prefetch_release();
    if (release === null || this.artifact(release)?.archive_sha256 !== archive_sha256)
      throw new UpdateConflict('The requested Mac runtime is not the ready release.');
    return this.archive_path(release);
  }
}
