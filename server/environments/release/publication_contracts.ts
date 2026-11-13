/** Build evidence is not an eligible publication or a production-start verdict. */
import type { AppManifest } from './contracts.ts';
import type { PackageLicenseInventory } from './package_license_contracts.ts';

export interface PublicationSourceRun {
  readonly repository: string;
  readonly source_sha: string;
  readonly event: string;
  readonly ref: string;
  readonly workflow_path: string;
  readonly workflow_ref: string;
  readonly run_id: string;
  readonly run_attempt: string;
}
export interface LinuxPublicationOptions {
  readonly source_root: string;
  readonly requested_sha: string;
  readonly output_directory: string;
}
export interface LinuxPublicationBuild {
  readonly version: 1;
  readonly source_run: PublicationSourceRun;
  readonly manifest: AppManifest;
  readonly archive: { readonly filename: string; readonly sha256: string; readonly size: number };
  readonly schema_frontier: readonly string[];
  readonly schema_floor: string;
  readonly runtime_inventory_sha256: string;
  readonly licenses: {
    readonly filename: string;
    readonly sha256: string;
    readonly package_count: number;
    readonly missing_own_licenses: PackageLicenseInventory['missing_own_licenses'];
  };
  readonly admission: { readonly status: 'passed'; readonly seconds: number };
  readonly producer_node: string;
  readonly phases_seconds: Readonly<Record<string, number>>;
  readonly unproven: readonly string[];
}
