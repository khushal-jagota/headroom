/** Existing Python backup-v2 format and host-local operator contracts. */
export type ManagedRootName = 'files' | 'worker-settings' | 'skills';
export type CapturedRootName = Exclude<ManagedRootName, 'files'>;

/** On-disk evidence is decoded as unknown before the existing verifier accepts it. */
export interface ManagedFilesEvidence {
  readonly roots: readonly string[];
  readonly manifest_sha256: string;
}
export interface SnapshotMetadata {
  readonly format: 'panels-backup-v2';
  readonly created_at: string;
  readonly deployed_revision: string;
  readonly sha256: string;
  readonly verified: true;
  readonly managed_files?: ManagedFilesEvidence;
}
export type ManagedFilesManifest = Record<string, Record<string, string>>;

/** Explicit stopped-destination assertion. False is refused before mutation. */
export interface RestoreOptions {
  readonly live_stopped: boolean;
}

/** Public operations mirror Python; capture excludes files, legacy restore retains it. */
export interface BackupOperations {
  managed_file_roots(databasePath: string): Promise<Record<ManagedRootName, string>>;
  create_database_backup(
    sourceDatabase: string,
    backupDirectory: string,
    deployedRevision: string,
  ): Promise<string>;
  restore_database_snapshot(
    snapshot: string,
    destinationDatabase: string,
    options: RestoreOptions,
  ): Promise<void>;
  apply_verified_snapshot_retention(
    backupDirectory: string,
    candidates: readonly string[],
  ): Promise<void>;
}
