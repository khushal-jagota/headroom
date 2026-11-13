/** Application identity and the pure database frontier. */
import { AppManifestValidationFailure } from '../command_contracts.ts';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
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
export interface AppValidationOptions {
  expected_sha?: string;
  require_runtime?: boolean;
  verify_artifact_digest?: boolean;
}
export interface MigrationGraph {
  readonly version: 1;
  readonly source_digest: string;
  readonly revisions: Readonly<Record<string, readonly string[]>>;
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

/** Null deadline is explicit standalone build policy, never a server default. */
export interface ReleaseExecution {
  readonly child_processes: OwnedChildProcesses;
  readonly signal: AbortSignal;
  readonly deadline: number | null;
}

export type ArchiveUnpackRequest = { readonly spool_root: string } & (
  | {
      readonly purpose: 'application';
      readonly destination: string;
      readonly archive_sha256: string;
      readonly expected_sha: string;
    }
  | {
      readonly purpose: 'mac-inspection';
      readonly archive_sha256: string;
      readonly archive_size: number;
    }
);
/** These two kinds alone enter the Mac service's deliberate conflict boundary. */
export class MacArchiveInspectionError extends Error {
  readonly kind: 'archive' | 'refusal';
  constructor(kind: 'archive' | 'refusal', message: string) {
    super(message);
    this.kind = kind;
  }
}
export class MacArchiveJsonError extends Error {
  readonly original: Error;
  constructor(original: Error) {
    super(original.message);
    this.original = original;
  }
}
export interface MacArchiveInspection {
  readonly manifest: unknown;
  readonly python: boolean;
}
export interface MacInspectionExecution {
  readonly child_processes: OwnedChildProcesses;
  readonly signal: AbortSignal;
  readonly deadline: number;
}
/** The observer ended while this promise still owns child, descriptor, and work cleanup. */
export class MacArchiveInspectionUnsettled extends Error {
  readonly settled: Promise<void>;
  readonly cause: unknown;
  constructor(settled: Promise<void>, cause: unknown) {
    super('Mac archive inspection cleanup is still pending');
    this.settled = settled;
    this.cause = cause;
  }
}
export type MacInspectionFailure = { type: 'mac-inspection-error'; message: string } & (
  | { kind: 'archive' | 'refusal' }
  | {
      kind: 'os';
      name: string;
      code: string;
      errno: number;
      syscall?: string;
      path?: string;
      dest?: string;
    }
  | { kind: 'json' | 'unexpected'; name: string }
);
