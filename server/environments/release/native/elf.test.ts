/** A package selector and readelf's machine field must both prove exclusion. */
import { expect, test } from 'vitest';
import { elf_architecture_exclusion } from './elf.ts';

test('foreign SQLite ARM64 prebuilds remain inactive while target and unknown ELF admission stay strict', () => {
  const root = 'server/node_modules/better-sqlite3/prebuilds/';
  const x64 = 'ELF Header:\n  Machine: Advanced Micro Devices X86-64\n';
  const arm64 = 'ELF Header:\n  Machine: AArch64\n';
  expect(elf_architecture_exclusion(root + 'linux-x64.node', x64)).toBeNull();
  for (const name of ['linux-arm64.node', 'linuxmusl-arm64.node'])
    expect(elf_architecture_exclusion(root + name, arm64)).toBe('foreign architecture prebuild');
  for (const [name, header] of [
    [root + 'linux-x64.node', arm64],
    [root + 'linuxmusl-arm64.node', 'ELF Header:\n  Machine: <unknown>\n'],
    ['runtime/lib/foreign.so', arm64],
  ])
    expect(() => elf_architecture_exclusion(name, header)).toThrow('unknown ELF architecture');
});
