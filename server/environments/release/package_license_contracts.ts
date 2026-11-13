/** Shipped-file evidence, without a legal compatibility or publication verdict. */
export interface RetainedLicenseFile {
  readonly path: string;
  readonly sha256: string;
  readonly link: { readonly target: string; readonly resolved_path: string } | null;
}
export interface DeclaredLicenseFile {
  readonly path: string;
  readonly finding: 'retained' | 'unsafe' | 'missing' | 'not-file' | 'outside-package';
}
export interface PackageLicenseRecord {
  readonly root: string;
  readonly kind: 'workspace' | 'installed' | 'embedded' | 'descriptor';
  readonly name: string | null;
  readonly version: string | null;
  readonly installed_slots: readonly string[];
  readonly aliases: readonly {
    readonly path: string;
    readonly target: string;
    readonly resolved_path: string;
  }[];
  readonly package_json_sha256: string;
  readonly license_declaration: unknown;
  readonly license_files: readonly RetainedLicenseFile[];
  readonly notice_files: readonly RetainedLicenseFile[];
  readonly declared_file: DeclaredLicenseFile | null;
  readonly missing_own_license: boolean;
}
export interface PackageLicenseInventory {
  readonly version: 1;
  readonly packages: readonly PackageLicenseRecord[];
  readonly source_license_files: readonly RetainedLicenseFile[];
  readonly source_notice_files: readonly RetainedLicenseFile[];
  readonly missing_own_licenses: readonly {
    readonly root: string;
    readonly name: string | null;
    readonly version: string | null;
    readonly installed_slots: readonly string[];
  }[];
}
