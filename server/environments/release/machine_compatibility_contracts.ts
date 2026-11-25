/** A native proof binds historical runtimes to the exact candidate publication. */
import type { PublicationSourceRun } from './publication_contracts.ts';
export type MachinePlatform = 'linux' | 'darwin';
export interface MachineBaseline {
  readonly source_sha: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly archive_path: string;
  readonly zip_sha256: string;
}
export interface MachineBaselinePin {
  readonly source_sha: string;
  readonly zip_sha256: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
}
export interface MachineCompatibilityProof {
  readonly version: 1;
  readonly platform: MachinePlatform;
  readonly source_run: PublicationSourceRun;
  readonly candidate: {
    readonly archive_sha256: string;
    readonly build_id: string;
    readonly repeat_build_id: string;
  };
  readonly oldest: Omit<MachineBaseline, 'archive_path'>;
  readonly previous: Omit<MachineBaseline, 'archive_path'>;
  readonly checks: {
    readonly previous_lease_run_result: 'passed';
    readonly oldest_channel_update: 'passed';
    readonly wire_compatibility: 'passed';
    readonly channel_version: 1;
  };
  readonly seconds: number;
}
