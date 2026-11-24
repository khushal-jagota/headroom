/** Relative names and lowercase SHA256 values, shared by backup and server moves. */
export type ChecksumManifest = Record<string, string>;

export interface ServerStatePaths {
  readonly server_root: string;
  readonly database_path: string;
  readonly credential_key_path: string;
  readonly apns_key_path?: string | null;
  readonly update_token_path?: string | null;
}

export interface ManifestOptions {
  /** Backup-v2 historically ignores links. Complete state inventories refuse them. */
  readonly strict?: boolean;
  /** Relative paths that do not belong to this inventory. Directories prune their subtree. */
  readonly exclude?: (relative: string) => boolean;
}

export interface ServerStateManifestOptions {
  /**
   * For a running server. Absent: the database itself, which must be checkpointed. A path: the
   * database entry digests this checked copy under the database's name, and the live WAL is
   * allowed. Null: no database entry, and the live WAL is allowed.
   */
  readonly database_copy?: string | null;
}
