/** A home payload has its own native inventory, never an executor runtime identity. */
import type { AppManifest } from './contracts.ts';

export interface MacHomeBuildOptions {
  readonly source_root: string;
  readonly requested_sha: string;
  readonly output_directory: string;
  readonly production_node_modules: string;
  readonly agent_backends_root: string;
  readonly web_dist: string;
}
export interface MacHomeBuildResult {
  readonly manifest: AppManifest;
  readonly archive: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
  readonly inventory_sha256: string;
  readonly node_version: string;
}
export interface DarwinHomeMachO {
  readonly path: string;
  readonly sha256: string;
  readonly format: 'Mach-O';
  readonly active: true;
  readonly architectures: readonly ['arm64'];
  readonly minimum_macos: readonly string[];
  readonly rpaths: readonly string[];
  readonly providers: Readonly<Record<string, string>>;
}
export interface DarwinHomeInactiveNative {
  readonly path: string;
  readonly sha256: string;
  readonly active: false;
  readonly format: 'ELF' | 'PE';
  readonly exclusion: 'Linux asset' | 'Windows asset';
}
export interface DarwinHomeInventory {
  readonly version: 2;
  readonly system: 'darwin';
  readonly architecture: 'arm64';
  readonly role: 'home';
  readonly node_major: 22;
  readonly native_files: readonly (DarwinHomeMachO | DarwinHomeInactiveNative)[];
}
