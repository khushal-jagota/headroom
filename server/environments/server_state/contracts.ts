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
