import { counter } from '../logic/counters.ts';
/** Ordered receipt custody and bounded file publication through the invocation write door. */
import { basename, dirname } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import type { Database, ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { ExecutorIdentity } from '../../execution_identity.ts';
import type { DirectoryHandle, FileOperationOptions, WireValue } from '../contracts.ts';
import type { FileVersion } from '../files.ts';
import { directory_descriptor, file_version_at, FileCleanupError, verified_version } from '../files.ts';
import { same_version } from '../mac/file_metadata.ts';
import { MAX_FILES_PER_PUBLICATION_TRANSACTION } from '../configuration.ts';
import { as_object, decode_json, decode_value, ExecutorValidationError } from '../protocol.ts';
import { FileStaging } from '../vps_files.ts';
import { require_current_incarnation_in_transaction } from '../vps_presence_store.ts';
import { open_child_assignment } from './child_custody.ts';
import { is_record as is_json_object } from '../../core/records.ts';
import { artifact_changed, PublicationGroup } from './publication.ts';

export type TransactionalWriter = (
  tx: TransactionSql,
  envelope: WireValue<'QueuedEnvelope'>,
) => WireValue<'JsonObject'>;
export class MissingFiles extends Error {
  readonly manifests: readonly WireValue<'FileManifest'>[];
  constructor(manifests: readonly WireValue<'FileManifest'>[]) {
    super('File dependencies are not durably published');
    this.name = 'MissingFiles';
    this.manifests = manifests;
  }
}
export interface PublishedFile {
  readonly manifest: WireValue<'FileManifest'>;
  readonly target: string;
  readonly parent: DirectoryHandle;
  readonly leaf: string;
  readonly version: FileVersion;
}
/** Caller owns these exact parents through acceptance, then awaits close in finally. */
export class PublishedFiles {
  readonly files: readonly PublishedFile[];
  private readonly parents: readonly DirectoryHandle[];
  private cleanup: Promise<void> | null = null;
  constructor(files: readonly PublishedFile[], parents: readonly DirectoryHandle[]) {
    this.files = files;
    this.parents = parents;
  }
  close(): Promise<void> {
    return (this.cleanup ??= close_parents(this.parents));
  }
}
async function close_parents(parents: readonly DirectoryHandle[]): Promise<void> {
  const errors: unknown[] = [];
  for (const parent of [...parents].reverse()) {
    try {
      await parent.close();
    } catch (error) {
      errors.push(error);
    }
  }
  if (errors.length) throw new FileCleanupError(errors, errors);
}
function invalid(message: string): PlannerError {
  return new PlannerError('validation', message);
}
function record(value: WireValue<'AcceptanceReceipt'>): string {
  return JSON.stringify(as_object(value, 'AcceptanceReceipt'));
}
interface InboxPosition {
  sequence: string;
  result_json: string;
}
export interface AcceptOptions {
  identity: ExecutorIdentity;
  check_files?: boolean;
  published?: PublishedFiles | null;
}
export class InboxStore {
  readonly database: Database;
  readonly staging: FileStaging;
  constructor(database: Database, staging: FileStaging) {
    this.database = database;
    this.staging = staging;
  }
  static receipt(row: { result_json: string }): WireValue<'AcceptanceReceipt'> {
    return decode_json(row.result_json, 'AcceptanceReceipt');
  }
  file_target(manifest: WireValue<'FileManifest'>): Promise<string> {
    return this.staging.file_target(manifest);
  }
  staged_file(manifest: WireValue<'FileManifest'>): Promise<string> {
    return this.staging.staged_file(manifest);
  }
  accept(
    envelope: WireValue<'QueuedEnvelope'>,
    writer: TransactionalWriter,
    now_ms: number,
    options: AcceptOptions,
  ): WireValue<'AcceptanceReceipt'> {
    return this.database.write(
      (tx) => this.accept_in_transaction(tx, envelope, writer, now_ms, options)[0],
    );
  }
  accept_in_transaction(
    tx: TransactionSql,
    envelope: WireValue<'QueuedEnvelope'>,
    writer: TransactionalWriter,
    now_ms: number,
    options: AcceptOptions,
  ): readonly [WireValue<'AcceptanceReceipt'>, boolean] {
    require_current_incarnation_in_transaction(tx, options.identity.executor_incarnation);
    const previous = this.prior_receipt(tx, envelope, options.identity);
    if (previous !== null) return [previous, false];
    const check_files = options.check_files ?? true;
    if (check_files) this.require_unchanged_files(envelope, options.published ?? null);
    let disposition: WireValue<'AcceptanceReceipt'>['disposition'] = 'accepted';
    let result: WireValue<'JsonObject'>;
    try {
      result = tx.savepoint((domain) => {
        if (check_files) this.require_file_owner(domain, envelope);
        return writer(domain, envelope);
      });
    } catch (error) {
      if (!(
        error instanceof PlannerError ||
        error instanceof ExecutorValidationError ||
        error instanceof TypeError
      ))
        throw error;
      disposition = 'refused';
      result = (
        error instanceof PlannerError ? error : invalid(error.message)
      ).toPayload() as WireValue<'JsonObject'>;
    }
    if (disposition === 'accepted' && result.fate === 'uncertain') disposition = 'uncertain';
    const selected: WireValue<'AcceptanceReceipt'> = {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      disposition,
      result,
      accepted_at_vps_ms: now_ms,
    };
    this.write_position(tx, envelope, selected);
    return [selected, disposition !== 'refused'];
  }
  write_position(
    tx: TransactionSql,
    envelope: WireValue<'QueuedEnvelope'>,
    receipt: WireValue<'AcceptanceReceipt'>,
    options: { dispatching?: boolean } = {},
  ): void {
    tx.run(
      'INSERT INTO mac_inbox_positions VALUES(?,?,?,?,?,?) ON CONFLICT(sender_id,conversation_id) DO UPDATE SET sequence=excluded.sequence,submission_id=excluded.submission_id,disposition=excluded.disposition,result_json=excluded.result_json',
      [
        envelope.sender_id,
        envelope.conversation_id,
        (envelope.sequence),
        envelope.submission_id,
        options.dispatching ? 'dispatching' : receipt.disposition,
        record(receipt),
      ],
    );
  }
  preflight(
    envelope: WireValue<'QueuedEnvelope'>,
    options: { identity: ExecutorIdentity },
  ): WireValue<'AcceptanceReceipt'> | null {
    return this.database.read((sql) => this.prior_receipt(sql, envelope, options.identity));
  }
  prior_receipt(
    sql: ReadSql,
    envelope: WireValue<'QueuedEnvelope'>,
    _identity: ExecutorIdentity,
  ): WireValue<'AcceptanceReceipt'> | null {
    const previous = sql.get<InboxPosition>(
      'SELECT CAST(sequence AS TEXT) AS sequence,result_json FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?',
      [envelope.sender_id, envelope.conversation_id],
    );
    if (!previous || (envelope.sequence) > counter(previous.sequence)) return null;
    const stored = InboxStore.receipt(previous);
    if ((envelope.sequence) === counter(previous.sequence)) {
      if (stored.submission_id !== envelope.submission_id)
        throw invalid('Submission identity conflicts with its first receipt');
      return stored;
    }
    return {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      disposition: 'accepted',
      result: {},
      accepted_at_vps_ms: stored.accepted_at_vps_ms,
    };
  }
  require_file_owner(sql: ReadSql, envelope: WireValue<'QueuedEnvelope'>): void {
    if (!envelope.file_dependencies?.length) return;
    const direct = envelope.payload.child;
    const ask = envelope.payload.ask;
    const child = is_json_object(direct) ? direct : is_json_object(ask) ? ask.child : null;
    const reference = decode_value(child, 'ChildReference');
    const assignment = open_child_assignment(sql, reference);
    if (assignment === null && ['backend_event', 'cli_write'].includes(envelope.kind))
      throw new PlannerError('agent_forbidden', 'child has no VPS assignment', {});
    for (const manifest of envelope.file_dependencies)
      if (
        reference.conversation_id !== envelope.conversation_id ||
        assignment === null ||
        assignment[0] !== 'ticket' ||
        assignment[1] !== manifest.owner_id
      )
        throw invalid('artifact belongs to another Ticket');
  }
  require_unchanged_files(
    envelope: WireValue<'QueuedEnvelope'>,
    published: PublishedFiles | null,
  ): void {
    const dependencies = envelope.file_dependencies ?? [];
    if (!dependencies.length) return;
    if (
      published === null ||
      published.files.length !== dependencies.length ||
      published.files.some(
        (file, index) =>
          !isDeepStrictEqual(
            as_object(file.manifest, 'FileManifest'),
            as_object(dependencies[index]!, 'FileManifest'),
          ),
      )
    )
      throw new Error('file dependencies must be published before their item');
    if (
      published.files.some(
        (file) => !same_version(file_version_at(file.parent, file.leaf), file.version),
      )
    )
      throw artifact_changed();
  }
  async store_upload(
    manifest: WireValue<'FileManifest'>,
    source: Parameters<FileStaging['store_upload']>[1],
    options: FileOperationOptions = {},
  ): Promise<void> {
    await this.staging.store_upload(manifest, source, options);
  }
  /** The caller holds its per-Ticket publication guard across this method and acceptance. */
  async publish_file_dependencies(
    envelope: WireValue<'QueuedEnvelope'>,
    identity: ExecutorIdentity,
  ): Promise<PublishedFiles> {
    this.database.read((sql) => this.require_file_owner(sql, envelope));
    type Prepared = {
      manifest: WireValue<'FileManifest'>;
      target: string;
      source: string | null;
      version: FileVersion;
    };
    const ready: Prepared[] = [];
    const missing: WireValue<'FileManifest'>[] = [];
    for (const manifest of envelope.file_dependencies ?? []) {
      const target = await this.staging.file_target(manifest);
      let version = await verified_version(target, manifest);
      if (version !== null) {
        ready.push({ manifest, target, source: null, version });
        continue;
      }
      const source = await this.staging.staged_file(manifest);
      version = await verified_version(source, manifest);
      if (version === null) missing.push(manifest);
      else ready.push({ manifest, target, source, version });
    }
    if (missing.length) throw new MissingFiles(missing);
    const parents = new Map<string, DirectoryHandle>();
    const parent = async (path: string, create: boolean) => {
      const existing = parents.get(path);
      if (existing) return existing;
      const opened = await directory_descriptor(path, { create });
      parents.set(path, opened);
      return opened;
    };
    try {
      const files: PublishedFile[] = [];
      const uploads: {
        index: number;
        entry: Parameters<typeof PublicationGroup.prepare>[0][number];
      }[] = [];
      for (const file of ready) {
        const target_parent = await parent(dirname(file.target), file.source !== null);
        files.push({
          manifest: file.manifest,
          target: file.target,
          parent: target_parent,
          leaf: basename(file.target),
          version: file.version,
        });
        if (file.source !== null)
          uploads.push({
            index: files.length - 1,
            entry: {
              source: {
                parent: await parent(dirname(file.source), false),
                leaf: basename(file.source),
                path: file.source,
              },
              target: { parent: target_parent, leaf: basename(file.target), path: file.target },
              version: file.version,
            },
          });
      }
      // Every parent is acquired above, before the first group enters database.write.
      for (let start = 0; start < uploads.length; start += MAX_FILES_PER_PUBLICATION_TRANSACTION) {
        const items = uploads.slice(start, start + MAX_FILES_PER_PUBLICATION_TRANSACTION);
        const group = await PublicationGroup.prepare(items.map((item) => item.entry));
        let failure: unknown;
        let failed = false;
        try {
          const versions = this.database.write((tx) => {
            require_current_incarnation_in_transaction(tx, identity.executor_incarnation);
            if (this.prior_receipt(tx, envelope, identity) !== null)
              throw new PlannerError('retry_later', 'This item was saved meanwhile');
            this.require_file_owner(tx, envelope);
            return group.publish();
          });
          items.forEach((item, index) => {
            files[item.index] = { ...files[item.index]!, version: versions[index]! };
          });
        } catch (error) {
          failed = true;
          failure = error;
        }
        try {
          await group.close();
        } catch (cleanup) {
          throw new FileCleanupError(
            failed ? [failure, cleanup] : [cleanup],
            cleanup instanceof FileCleanupError ? cleanup.cleanup_errors : [cleanup],
          );
        }
        if (failed) throw failure;
      }
      return new PublishedFiles(files, [...parents.values()]);
    } catch (error) {
      try {
        await close_parents([...parents.values()]);
      } catch (cleanup) {
        throw new FileCleanupError(
          [error, cleanup],
          cleanup instanceof FileCleanupError ? cleanup.cleanup_errors : [cleanup],
        );
      }
      throw error;
    }
  }
}
