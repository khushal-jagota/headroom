/** Decode the home inventory without loading a native package or a build tool. */
import { posix } from 'node:path';
import { is_record } from '../../core/records.ts';
import type { DarwinHomeInventory } from './mac_home_contracts.ts';
import { MAC_HOME_MINIMUM_OS, MAC_HOME_SYSTEM_LIBRARIES } from './mac_home_configuration.ts';

export function mac_home_inventory_path(value: unknown): string {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes('\0') ||
    value.startsWith('/') || value.split('/').some((part) => !part || part === '.' || part === '..'))
    throw new Error('Invalid Darwin home native path');
  return value;
}
export function mac_home_system_library(value: string): boolean {
  return posix.normalize(value) === value && MAC_HOME_SYSTEM_LIBRARIES.some((prefix) => value.startsWith(prefix));
}
export function mac_home_minimum_versions(text: string): string[] {
  const builds = [...text.matchAll(/cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform (\S+)\s+minos ([0-9.]+)/g)];
  if (builds.some((match) => !['1', 'MACOS'].includes(match[1]!.toUpperCase())))
    throw new Error('Mac native binary targets a non-macOS platform');
  const versions = [
    ...builds.map((match) => match[2]!),
    ...[...text.matchAll(/cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([0-9.]+)/g)].map((match) => match[1]!),
  ];
  validate_minimum_versions(versions);
  return versions;
}
function validate_minimum_versions(versions: readonly string[]): void {
  if (!versions.length) throw new Error('Mac native binary lacks minimum macOS metadata');
  const minimum = MAC_HOME_MINIMUM_OS.split('.').map(Number);
  for (const version of versions) {
    if (!/^\d+(?:\.\d+){0,2}$/.test(version)) throw new Error('Invalid minimum macOS version');
    const found = version.split('.').map(Number);
    for (let index = 0; index < 3; index++) {
      const left = found[index] ?? 0, right = minimum[index] ?? 0;
      if (left > right) throw new Error(`Mac native binary requires newer than macOS ${MAC_HOME_MINIMUM_OS}`);
      if (left < right) break;
    }
  }
}
function fields(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}
export function parse_mac_home_inventory(value: unknown): DarwinHomeInventory {
  if (!is_record(value) || !fields(value, ['version', 'system', 'architecture', 'role', 'node_major', 'native_files']) || value.version !== 2 || value.system !== 'darwin' || value.architecture !== 'arm64' || value.role !== 'home' || value.node_major !== 22 || !Array.isArray(value.native_files))
    throw new Error('Invalid Darwin home native inventory');
  const names = new Set<string>();
  const active_names = new Set<string>();
  for (const entry of value.native_files) {
    if (!is_record(entry)) throw new Error('Invalid Darwin home native record');
    const name = mac_home_inventory_path(entry.path);
    if (names.has(name) || typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256))
      throw new Error('Duplicate path or invalid hash in Darwin home native inventory');
    names.add(name);
    if (entry.format === 'Mach-O' && entry.active === true) {
      if (!fields(entry, ['path', 'sha256', 'format', 'active', 'architectures', 'minimum_macos', 'rpaths', 'providers']) || !Array.isArray(entry.architectures) || entry.architectures.length !== 1 || entry.architectures[0] !== 'arm64' || !Array.isArray(entry.minimum_macos) || !entry.minimum_macos.every((item) => typeof item === 'string') || !Array.isArray(entry.rpaths) || !entry.rpaths.every((item) => typeof item === 'string' && !item.includes('\0')) || !is_record(entry.providers))
        throw new Error('Invalid active Darwin home native record');
      validate_minimum_versions(entry.minimum_macos);
      active_names.add(name);
      for (const [needed, provider] of Object.entries(entry.providers)) {
        if (!needed || needed.includes('\0') || typeof provider !== 'string') throw new Error('Invalid Darwin home native dependency');
        if (provider.startsWith('host:')) {
          if (provider !== 'host:' + needed || !mac_home_system_library(needed)) throw new Error('Unadmitted Darwin home system dependency');
        } else mac_home_inventory_path(provider);
      }
    } else if (!fields(entry, ['path', 'sha256', 'format', 'active', 'exclusion']) || entry.active !== false || !((entry.format === 'ELF' && entry.exclusion === 'Linux asset') || (entry.format === 'PE' && entry.exclusion === 'Windows asset')))
      throw new Error('Invalid inactive Darwin home native record');
  }
  for (const entry of value.native_files)
    if (entry.format === 'Mach-O')
      for (const provider of Object.values(entry.providers))
        if (typeof provider !== 'string' || (!provider.startsWith('host:') && !active_names.has(provider)))
          throw new Error('Darwin home native provider is not inventoried');
  return value as unknown as DarwinHomeInventory;
}
