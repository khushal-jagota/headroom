import {
  HOME_MACHINE_ID,
  ORIGINAL_MAC_MACHINE_ID,
  same_machine_build,
  valid_uuid,
} from '../../../execution_identity.ts';
import { counter } from '../../../executor/logic/counters.ts';
import { same_identity } from '../../../executor/vps_presence_store.ts';
/** Pinned Mac bytes and reports survive publication expiry and later VPS failures. */
import { createHash, randomUUID } from 'node:crypto';
import { closeSync, constants, fsyncSync, openSync, readFileSync, statSync } from 'node:fs';
import { mkdir, mkdtemp, open, readdir, rename, rm, unlink } from 'node:fs/promises';
import { join } from 'node:path';
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
import type { PublishedUpdate, UpdateAttempt, MachineUpdateState } from '../contracts.ts';
import { UPDATE_AVAILABILITY_REFRESH_SECONDS } from '../configuration.ts';
import { with_zip_member } from '../zip.ts';
import type {
  MacUpdateCheck,
  MacUpdateCompositionContext,
  MacUpdateHealthRead,
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
  MAC_SIGNING_PROOF_NAME,
  MAX_MAC_SIGNING_PROOF_BYTES,
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
/** Main-branch signing adds its proof next to the runtime and its publication record. */
async function zip_runtime_size(
  path: string,
  signal: AbortSignal,
  archive_name: string,
  proof_name: string,
  signing_proof_name: string | null,
): Promise<{ size: number; signing_proof_size?: number }> {
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
    return await new Promise<{ size: number; signing_proof_size?: number }>((resolve, reject) => {
      const names = new Set<string>();
      const allowed = [
        archive_name,
        proof_name,
        ...(signing_proof_name ? [signing_proof_name] : []),
      ];
      let duplicate = false,
        size: number | undefined,
        signing_proof_size: number | undefined;
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
        if (entry.fileName === signing_proof_name) signing_proof_size = entry.uncompressedSize;
        archive.readEntry();
      });
      archive.once('end', () => {
        if (duplicate || size === undefined || [...names].some((name) => !allowed.includes(name)))
          reject(new UpdateConflict('The Mac runtime artifact is invalid.'));
        else resolve(signing_proof_size === undefined ? { size } : { size, signing_proof_size });
      });
      if (signal.aborted) abort();
      else archive.readEntry();
    });
  } finally {
    if (abort) signal.removeEventListener('abort', abort);
    archive.close();
  }
}
/** A present signing proof must name the exact package and build that the receipt publishes. */
async function check_signing_proof(
  path: string,
  size: number,
  release: PublishedUpdate,
  archive_sha256: string,
): Promise<void> {
  if (size > MAX_MAC_SIGNING_PROOF_BYTES)
    throw new UpdateConflict('The Mac signing proof is invalid.');
  let proof: unknown;
  try {
    const chunks: Buffer[] = [];
    await with_zip_member(path, MAC_SIGNING_PROOF_NAME, async (stream) => {
      for await (const chunk of stream) chunks.push(chunk as Buffer);
    });
    proof = decode_json(Buffer.concat(chunks));
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError)
      throw new UpdateConflict('The Mac signing proof is invalid.', { cause: error });
    return archive_failure(error);
  }
  const build_id = release.parts?.machines['darwin-arm64']?.build_id;
  if (
    !is_object(proof) ||
    proof.signed_archive_sha256 !== archive_sha256 ||
    build_id == null ||
    proof.signed_build_id !== build_id ||
    typeof proof.team !== 'string' ||
    !proof.team ||
    typeof proof.notarization_id !== 'string' ||
    !proof.notarization_id
  )
    throw new UpdateConflict('The Mac signing proof does not match this publication.');
}
export class MacUpdateService implements MacUpdateServiceContract {
  readonly root: string;
  readonly machine_id: string;
  readonly archive_root: string;
  readonly platform: string;
  private readonly context: MacUpdateCompositionContext;
  private readonly machines = new Map<string, MacUpdateService>();
  private linux_archive_owner: MacUpdateService | undefined;
  private archive_owner: MacUpdateService | undefined;
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
  private readonly prepared_again = new Set<string>();
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
    if (this.platform === 'linux' && this.archive_root === this.root) {
      const selected = this.selected_attempt();
      if (selected !== null && file(this.archive_path(selected.release)))
        this.schedule_linux_preparation(selected.release);
    }
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
      });
      kept.linux_archive_owner = linux;
      kept.archive_owner = linux ?? this;
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
    if (attempt === null || attempt.mac_attempt_id === null) return attempt;
    return this.with_machine_request(attempt);
  }
  private with_machine_request(attempt: UpdateAttempt, retained = false): UpdateAttempt {
    let retry: unknown;
    try {
      retry = json(join(this.root, 'retry.json'));
    } catch (error) {
      if (!missing(error)) throw error;
    }
    const request_id =
      is_object(retry) &&
      retry.attempt_id === attempt.attempt_id &&
      typeof retry.request_id === 'string'
        ? retry.request_id
        : retained || this.machine_id === ORIGINAL_MAC_MACHINE_ID
          ? attempt.mac_attempt_id
          : `${attempt.mac_attempt_id}-${this.machine_id}`;
    return { ...attempt, mac_attempt_id: request_id };
  }
  private unchanged(release: PublishedUpdate): boolean {
    const machine = this.context.machines().find((item) => item.machine_id === this.machine_id);
    const build = this.build_identity(release).build_id;
    // Older retained publications have no content identity to compare.
    return build === undefined
      ? machine?.identity?.application_revision === release.manifest.app_sha
      : machine?.identity?.build_id === build;
  }
  private preparation_error(release: PublishedUpdate): string | null {
    if (this.artifact(release) === null) return null;
    this.archive_path(release);
    try {
      const value = json(
        join(this.root, 'preparation', this.artifact(release)!.archive_sha256 + '.json'),
      );
      return is_object(value) &&
        value.archive_sha256 === this.artifact(release)?.archive_sha256 &&
        typeof value.detail === 'string'
        ? value.detail
        : null;
    } catch (error) {
      if (missing(error)) return null;
      throw error;
    }
  }
  private save_preparation(release: PublishedUpdate, detail: string | null): void {
    if (this.artifact(release) === null) return;
    this.archive_path(release);
    atomic_write_json(
      join(this.root, 'preparation', this.artifact(release)!.archive_sha256 + '.json'),
      {
        archive_sha256: this.artifact(release)?.archive_sha256 ?? null,
        detail,
        home_revision: this.current_app_sha,
      },
    );
    this.emit();
  }
  /** The previous home prepares a release before it installs, so its refusal can be its own defect. */
  private stale_preparation(release: PublishedUpdate): boolean {
    if (this.preparation_error(release) === null) return false;
    const value = json(
      join(this.root, 'preparation', this.artifact(release)!.archive_sha256 + '.json'),
    );
    return is_object(value) && value.home_revision !== this.current_app_sha;
  }
  // The saved record changes only with an outcome. An interrupted retry stays stale for the next start.
  private prepare_again(release: PublishedUpdate): void {
    const owner = this.archive_owner ?? this;
    void owner.queue_preparation(async () => {
      try {
        await owner.prepare_once(release);
        this.signal.throwIfAborted();
        if (this.platform === 'linux' && release.linux_executor)
          this.linux_owner().ready_archives.add(release.linux_executor.archive_sha256);
        this.save_preparation(release, null);
      } catch (error) {
        if (this.signal.aborted) return;
        this.save_preparation(release, error instanceof Error ? error.message : String(error));
      }
    });
  }
  retry_machine(machine_id: string, attempt_id: string): void {
    const inventory = this.context.machines();
    if (!inventory.some((machine) => machine.machine_id === machine_id))
      throw new UpdateConflict('This machine is no longer registered.');
    const service = this.for_machine(machine_id) as MacUpdateService;
    const attempt = service.selected_attempt();
    if (attempt === null || attempt.attempt_id !== attempt_id || attempt.state !== 'done')
      throw new UpdateConflict('The machine update changed or is not ready to retry.');
    if (service.unchanged(attempt.release))
      throw new UpdateConflict('This machine already has the target build.');
    const status = service.status();
    if (!status || !['failed', 'reverted', 'waiting', 'unavailable'].includes(status.phase))
      throw new UpdateConflict('This machine does not need a retry.');
    atomic_write_json(join(service.root, 'retry.json'), {
      attempt_id,
      request_id: randomUUID().replaceAll('-', ''),
    });
    service.save_preparation(attempt.release, null);
    service.prepare_again(attempt.release);
  }
  machine_states(release: PublishedUpdate | null = null): MachineUpdateState[] {
    return this.context.machines().map((machine) => {
      const service = this.for_machine(machine.machine_id) as MacUpdateService;
      const attempt = service.selected_attempt();
      const active = service.attempt();
      const selected =
        release ??
        attempt?.release ??
        (active !== null && target_states.has(active.state) ? active.release : null);
      const status = service.status();
      const changed = selected !== null && !service.unchanged(selected);
      const preparation_error = selected === null ? null : service.preparation_error(selected);
      let state: MachineUpdateState['state'] = 'current';
      if (selected !== null && service.artifact(selected) === null) state = 'unavailable';
      else if (changed && preparation_error !== null) state = 'failed';
      else if (
        release === null &&
        (status?.phase === 'downloading' || status?.phase === 'ready_to_restart')
      )
        state = machine.present ? 'updating' : 'waiting';
      else if (changed) {
        if (release !== null) state = 'available';
        else if (status?.phase === 'failed' || status?.phase === 'reverted') state = 'failed';
        else if (!machine.present) state = 'waiting';
        else if (status?.phase === 'unavailable') state = 'unavailable';
        else state = 'updating';
      }
      return {
        machine_id: machine.machine_id,
        name: machine.name,
        platform: machine.platform,
        present: machine.present,
        build_id: machine.identity?.build_id ?? null,
        target_build_id:
          selected === null ? null : (service.build_identity(selected).build_id ?? null),
        changed,
        state,
        attempt_id: release !== null ? null : (attempt?.attempt_id ?? active?.attempt_id ?? null),
        request_id: release !== null ? null : (status?.request_id ?? null),
        detail:
          preparation_error ??
          (release === null && (state !== 'current' || status?.phase === 'current')
            ? (status?.detail ?? null)
            : null),
        can_retry:
          release === null &&
          attempt?.state === 'done' &&
          selected !== null &&
          service.artifact(selected) !== null &&
          ['failed', 'waiting', 'unavailable'].includes(state),
      };
    });
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
    const { manifest, node, entrypoint, native_linux } = inspected;
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
      !native
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
      attempt.to_sha === this.current_app_sha
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
  /** Wait for this home's required package while other machines download independently. */
  async prepare_home(release: PublishedUpdate): Promise<void> {
    if (!this.context.machines().some((machine) => machine.machine_id === HOME_MACHINE_ID)) return;
    const local = this.for_machine(HOME_MACHINE_ID) as MacUpdateService;
    if (local.unchanged(release)) return;
    if (!local.artifact(release))
      throw new UpdateConflict('The release has no package for this home machine.');
    const owner = local.archive_owner ?? local;
    await owner.queue_preparation(async () => {
      const digest = local.artifact(release)!.archive_sha256;
      if (!owner.ready_archives.has(digest)) {
        await owner.prepare_once(release);
        owner.ready_archives.add(digest);
      }
      local.save_preparation(release, null);
    });
  }
  prepare(release: PublishedUpdate): Promise<void> {
    this.signal.throwIfAborted();
    const machines = this.context.machines();
    const changed = machines.filter(
      (machine) => !(this.for_machine(machine.machine_id) as MacUpdateService).unchanged(release),
    );
    const platforms = new Set(changed.map((machine) => machine.platform));
    return Promise.all(
      [...platforms].map((platform) => {
        const owner = platform === 'linux' ? this.linux_owner() : this;
        return owner.queue_preparation(async () => {
          let detail: string | null = null;
          try {
            await owner.prepare_once(release);
            owner.signal.throwIfAborted();
            if (platform === 'linux' && release.linux_executor)
              owner.ready_archives.add(release.linux_executor.archive_sha256);
          } catch (error) {
            if (owner.signal.aborted) throw error;
            detail = error instanceof Error ? error.message : String(error);
          }
          for (const machine of changed.filter((machine) => machine.platform === platform))
            (this.for_machine(machine.machine_id) as MacUpdateService).save_preparation(
              release,
              detail,
            );
        });
      }),
    ).then(() => {});
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
        await this.provider.download_artifact(
          mac.artifact_id,
          downloaded,
          this.signal,
          mac.artifact_source,
        );
        this.signal.throwIfAborted();
        try {
          const contents = await zip_runtime_size(
            downloaded,
            this.signal,
            this.archive_name,
            this.proof_name,
            this.platform === 'linux' ? null : MAC_SIGNING_PROOF_NAME,
          );
          if (contents.size !== mac.archive_size)
            throw new UpdateConflict('The Mac runtime artifact size differs.');
          if (contents.signing_proof_size !== undefined)
            await check_signing_proof(
              downloaded,
              contents.signing_proof_size,
              release,
              mac.archive_sha256,
            );
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
    return retained !== null && retained.to_sha === this.current_app_sha
      ? this.with_machine_request(retained, true)
      : null;
  }
  private build_identity(release: PublishedUpdate): { build_id?: string } {
    const key = this.platform === 'linux' ? 'linux-x86_64' : 'darwin-arm64';
    const build_id = release.parts?.machines[key]?.build_id;
    return build_id == null ? {} : { build_id };
  }
  private target(): MacUpdateTarget | null {
    const attempt = this.selected_attempt();
    if (attempt === null) return null;
    const mac = this.artifact(attempt.release)!,
      request_id = attempt.mac_attempt_id!;
    return {
      request_id,
      application_revision: attempt.to_sha,
      ...this.build_identity(attempt.release),
      mode: attempt.confirm_activity ? attempt.mode : 'idle',
      activate: attempt.state === 'done' && attempt.to_sha === this.current_app_sha,
      archive_sha256: mac.archive_sha256,
      archive_size: counter(mac.archive_size),
      archive_path: `/api/executor/update/${request_id}/archive`,
    };
  }
  private prefetch_release(): PublishedUpdate | null {
    const release = this.ready_release();
    if (release === null || this.artifact(release) === null || this.unchanged(release)) return null;
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
    const selected = this.selected_attempt();
    const release = selected?.release ?? this.ready_release();
    // The machine asks again after a newer home replaced the code that refused its package.
    if (
      selected !== null &&
      !this.unchanged(selected.release) &&
      this.stale_preparation(selected.release)
    ) {
      const digest = this.artifact(selected.release)!.archive_sha256;
      if (!this.prepared_again.has(digest)) {
        this.prepared_again.add(digest);
        this.prepare_again(selected.release);
      }
    } else if (
      this.platform === 'linux' &&
      release !== null &&
      !this.unchanged(release) &&
      this.preparation_error(release) === null
    )
      this.schedule_linux_preparation(release);
    const prefetched = this.prefetch_release();
    const mac = prefetched === null ? null : this.artifact(prefetched);
    let target = this.target();
    if (target !== null && selected !== null) {
      const phase = this.saved_status(target.request_id)?.phase;
      const needs_acknowledgment = phase === 'downloading' || phase === 'ready_to_restart';
      if (
        this.unchanged(selected.release)
          ? !needs_acknowledgment
          : this.preparation_error(selected.release) !== null ||
            !file(this.archive_path(selected.release)) ||
            (this.platform === 'linux' &&
              !this.linux_owner().ready_archives.has(target.archive_sha256))
      )
        target = null;
    }
    return {
      schema_version: MAC_UPDATE_SCHEMA_VERSION,
      target,
      prefetch:
        prefetched && mac
          ? {
              application_revision: prefetched.manifest.app_sha,
              ...this.build_identity(prefetched),
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
    // An adjacent release can save fields this one does not know.
    return {
      request_id: status.request_id,
      target_revision: status.target_revision,
      phase: status.phase,
      detail: status.detail ?? null,
    } as MacUpdateStatus;
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
  report(report: MacUpdateReport, health: MacUpdateHealthRead | null): MacUpdateStatus {
    const detail = report.detail ?? null,
      identity =
        report.identity == null
          ? null
          : {
              ...report.identity,
              connection_generation: report.identity.connection_generation ?? 0,
            },
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
        (target.build_id !== undefined
          ? identity.build_id !== target.build_id
          : !same_machine_build(identity, target)) ||
        !same_identity(leased, identity))
    )
      throw new UpdateConflict('The target Mac runtime is not the present, leased one.');
    if (
      report.phase === 'ready_to_restart' &&
      (identity === null || !same_identity(leased, identity))
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
