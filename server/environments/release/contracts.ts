/** Application identity and the static native inventory. */
import type { AppManifest } from '../updates/contracts.ts';
import { AppManifestValidationFailure } from '../command_contracts.ts';
export type {
  AppManifest,
  NodeRuntimeContract,
  PythonRuntimeContract,
  RuntimeContract,
} from '../updates/contracts.ts';

export class AppValidationError extends AppManifestValidationFailure {
  constructor(message: string) {
    super('value', message);
    this.name = 'AppValidationError';
  }
}

export interface AppManifestDocument extends AppManifest {
  format: 'panels-app-v1';
}
export interface AppValidationOptions {
  expected_sha?: string;
  require_runtime?: boolean;
  verify_artifact_digest?: boolean;
}
export interface NativeElfFile {
  readonly path: string;
  readonly format: 'ELF';
  readonly active: boolean;
  readonly exclusion: string | null;
  readonly role: 'addon' | 'executable' | 'library';
  readonly origin: string;
  readonly sha256: string;
  readonly providers: Readonly<Record<string, string>>;
  readonly interpreter: string | null;
  readonly loader_paths: readonly string[];
  readonly glibc_versions: readonly string[];
  readonly gnu_property_notes: readonly string[];
}
export type InactiveNativeFile = {
  readonly path: string;
  readonly active: false;
  readonly sha256: string;
} & (
  | { readonly format: 'PE'; readonly exclusion: 'Windows asset' }
  | { readonly format: 'Mach-O'; readonly exclusion: 'macOS asset' }
);
export interface NativeInventory {
  readonly version: 2;
  readonly node_major: 22;
  readonly package_metadata: 'npm package.json files remain in payload';
  readonly cpu_policy: 'conservative x86-64-v3 support boundary, not vendor minimum';
  readonly native_files: readonly (NativeElfFile | InactiveNativeFile)[];
  readonly copied_components: Readonly<Record<string, CopiedNativeComponent>>;
  readonly packaging_tools: readonly ['readelf', 'patchelf'];
}
export interface CopiedNativeComponent {
  readonly package: string;
  readonly version: string;
  readonly license: string;
  readonly source: string;
  readonly source_sha256: string;
}
export interface ElfDynamic {
  readonly needed: readonly string[];
  readonly rpath: readonly string[];
  readonly runpath: readonly string[] | null;
  readonly interpreter: string | null;
}
export interface ElfObject {
  readonly path: string;
  readonly dynamic: ElfDynamic;
}
export interface MigrationGraph {
  readonly version: 1;
  readonly source_digest: string;
  readonly revisions: Readonly<Record<string, readonly string[]>>;
}
