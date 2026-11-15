import assert from 'node:assert/strict';
import { test } from 'vitest';
import { mac_home_minimum_versions, parse_mac_home_inventory } from './mac_home_inventory.ts';

test('Mac home inventory refuses cross-platform binaries, external providers, and inactive provider substitutions', () => {
  // Existing ELF and target tests cannot prove Mach-O platform or dyld provider policy.
  const load = 'cmd LC_BUILD_VERSION\ncmdsize 32\nplatform MACOS\nminos 15.0\nsdk 15.5\n';
  assert.deepEqual(mac_home_minimum_versions(load), ['15.0']);
  assert.throws(() => mac_home_minimum_versions(load.replace('MACOS', '2')), /non-macOS/);
  assert.throws(() => mac_home_minimum_versions(load.replace('15.0', '15.1')), /newer than/);
  const record = (path: string, providers: Record<string, string>) => ({ path, sha256: 'a'.repeat(64), format: 'Mach-O', active: true, architectures: ['arm64'], minimum_macos: ['15.0'], rpaths: ['@loader_path'], providers });
  const addon = record('server/node_modules/koffi/addon.node', { '@rpath/libfixture.dylib': 'runtime/lib/libfixture.dylib' });
  const library = record('runtime/lib/libfixture.dylib', { '/usr/lib/libSystem.B.dylib': 'host:/usr/lib/libSystem.B.dylib' });
  const inventory = { version: 2, system: 'darwin', architecture: 'arm64', role: 'home', node_major: 22, native_files: [addon, library] };
  assert.equal(parse_mac_home_inventory(inventory), inventory);
  assert.throws(() => parse_mac_home_inventory({ ...inventory, native_files: [addon] }), /not inventoried/);
  assert.throws(() => parse_mac_home_inventory({ ...inventory, native_files: [addon, { path: library.path, sha256: library.sha256, format: 'ELF', active: false, exclusion: 'Linux asset' }] }), /not inventoried/);
  assert.throws(() => parse_mac_home_inventory({ ...inventory, native_files: [record(addon.path, { '/opt/homebrew/lib/foreign.dylib': 'host:/opt/homebrew/lib/foreign.dylib' }), library] }), /Unadmitted/);
  assert.throws(() => parse_mac_home_inventory({ ...inventory, native_files: [record(addon.path, { lib: '../foreign.dylib' }), library] }), /Invalid Darwin home native path/);
});
