/** Evidence from execution of the sealed TypeScript Mac runtime on Darwin. */
export interface MacRuntimePublication {
  readonly schema_version: 1;
  readonly application_revision: string;
  readonly platform: 'darwin';
  readonly architecture: 'arm64';
  readonly publication_run_id: string;
  readonly run_attempt: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly node_version: string;
  readonly node_sha256: string;
  readonly proof: {
    readonly relocated_archive: 'passed';
    readonly executor_cli: 'passed';
    readonly native_files: 'passed';
    readonly sqlite: 'passed';
    readonly host_import: 'passed';
    readonly duration_seconds: number;
  };
}
