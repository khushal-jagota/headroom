/** Linux executor installation and the shared launch contract. */
import type { AppManifest, ReleaseExecution } from '../release/contracts.ts';
export interface LinuxExecutorInstallation {
  readonly installation: string;
  readonly data: string;
  readonly executor_id: string;
  readonly server_url: string;
  readonly revision: string;
  readonly home: string;
  readonly node_executable: string;
}
export interface LinuxExecutorInstallRequest {
  readonly archive: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly credential_file: string;
  readonly unit_directory: string;
}
export interface LinuxExecutorJoinRequest {
  readonly archive: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly revision: string;
  readonly home_url: string;
  readonly machine_id: string;
  readonly credential: string;
  readonly root: string;
  readonly home: string;
  readonly node_executable: string;
  readonly isolated_providers: boolean;
}
export interface LinuxExecutorArchiveApproval {
  readonly expected_sha256: string;
  readonly expected_size: number;
  readonly expected_revision: string;
}
export interface LinuxExecutorArchiveOperations {
  admit(runtime: string, manifest: AppManifest, execution: ReleaseExecution): Promise<void>;
}
export interface LinuxExecutorBuildOptions {
  readonly source_root: string;
  readonly requested_sha: string;
  readonly output_directory: string;
}
export interface LinuxExecutorPublication {
  readonly schema_version: 1;
  readonly proof: {
    readonly native_startup: 'passed';
    readonly relocated_archive: 'passed';
    readonly executor_cli: 'passed';
  };
  readonly application_revision: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly platform: 'linux';
  readonly architecture: 'x86_64';
  readonly role: 'executor';
}
