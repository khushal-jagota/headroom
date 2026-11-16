/** Gaps: installer copy tests do not enumerate package ownership or interpret SEE
 * declarations. Two filesystem cases reuse this fixture and the existing Vitest host.
 */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { inventory_package_licenses } from './package_licenses.ts';

const hash = (bytes: string) => createHash('sha256').update(bytes).digest('hex');
async function fixture(
  use: (root: string, put: (name: string, text: string) => Promise<void>) => Promise<void>,
): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'panels-license-'));
  const put = async (name: string, text: string) => {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), text);
  };
  try {
    await use(root, put);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}
test('inventories every metadata root without borrowing nested package licenses', async () => {
  await fixture(async (root, put) => {
    const outer = 'server/node_modules/outer';
    for (const [name, text] of Object.entries({
      LICENSE: 'source license',
      COPYRIGHT: 'source copyright',
      'server/package.json': '{"name":"workspace","license":"MIT"}',
      'web/package.json': '{"name":"web"}',
      'web/LICENSE': 'web license',
      'agent_backends/package.json': '{"name":"agents"}',
      'agent_backends/LICENCE': 'agent license',
      [outer + '/package.json']:
        '{"name":"outer","version":"1.2.3","license":" SEE LICENSE IN custom-terms "}',
      [outer + '/docs/LICENCE.md']: 'outer license',
      [outer + '/docs/terms.txt']: 'declared terms',
      [outer + '/LICENSE_😀']: 'astral',
      [outer + '/LICENSE_\ue000']: 'private-use',
      [outer + '/node_modules/nested/package.json']: '{"name":"nested","version":"2"}',
      [outer + '/node_modules/nested/LICENSE']: 'nested license',
      [outer + '/fixtures/named/package.json']: '{"name":"fixture-name","version":"3"}',
      [outer + '/fixtures/named/COPYING']: 'fixture license',
      [outer + '/types/package.json']: '{"type":"module"}',
      [outer + '/types/LICENSE']: 'type license',
      'server/node_modules/notice-only/package.json':
        '{"name":"notice-only","version":"4","license":"Apache-2.0"}',
      'server/node_modules/notice-only/NOTICE': 'notice',
      'server/node_modules/notice-only/COPYRIGHT.txt': 'copyright',
    }))
      await put(name, text);
    await symlink('docs/terms.txt', join(root, outer, 'custom-terms'));
    await mkdir(join(root, 'agent_backends/node_modules'));
    await symlink(
      '../../server/node_modules/outer',
      join(root, 'agent_backends/node_modules/outer'),
    );
    const inventory = await inventory_package_licenses(root);
    expect(inventory.packages.map(({ root, kind, version }) => [root, kind, version])).toEqual([
      ['agent_backends', 'workspace', null],
      ['server', 'workspace', null],
      ['server/node_modules/notice-only', 'installed', '4'],
      [outer, 'installed', '1.2.3'],
      [outer + '/fixtures/named', 'embedded', '3'],
      [outer + '/node_modules/nested', 'installed', '2'],
      [outer + '/types', 'descriptor', null],
      ['web', 'workspace', null],
    ]);
    const record = inventory.packages.find((entry) => entry.name === 'outer')!;
    expect(record.package_json_sha256).toBe(
      hash('{"name":"outer","version":"1.2.3","license":" SEE LICENSE IN custom-terms "}'),
    );
    expect(record.license_declaration).toBe(' SEE LICENSE IN custom-terms ');
    expect(record.license_files.map((file) => file.path)).toEqual([
      outer + '/LICENSE_\ue000',
      outer + '/LICENSE_😀',
      outer + '/custom-terms',
      outer + '/docs/LICENCE.md',
    ]);
    expect(record.license_files.find((file) => file.path.endsWith('/custom-terms'))).toEqual({
      path: outer + '/custom-terms',
      sha256: hash('declared terms'),
      link: { target: 'docs/terms.txt', resolved_path: outer + '/docs/terms.txt' },
    });
    expect(record.declared_file).toEqual({ path: 'custom-terms', finding: 'retained' });
    expect(record.installed_slots).toEqual(['agent_backends/node_modules/outer', outer]);
    expect(record.aliases).toEqual([
      {
        path: 'agent_backends/node_modules/outer',
        target: '../../server/node_modules/outer',
        resolved_path: outer,
      },
    ]);
    expect(
      inventory.packages
        .find((entry) => entry.name === 'notice-only')!
        .notice_files.map((file) => file.path),
    ).toEqual([
      'server/node_modules/notice-only/COPYRIGHT.txt',
      'server/node_modules/notice-only/NOTICE',
    ]);
    expect(inventory.missing_own_licenses).toEqual([
      { root: 'server', name: 'workspace', version: null, installed_slots: [] },
      {
        root: 'server/node_modules/notice-only',
        name: 'notice-only',
        version: '4',
        installed_slots: ['server/node_modules/notice-only'],
      },
    ]);
    expect(inventory.source_license_files).toEqual([
      { path: 'LICENSE', sha256: hash('source license'), link: null },
    ]);
    expect(inventory.source_notice_files).toEqual([
      { path: 'COPYRIGHT', sha256: hash('source copyright'), link: null },
    ]);
  });
});
test('fails malformed metadata and preserves unsafe or outside-owner SEE findings', async () => {
  await fixture(async (root, put) => {
    await put('server/package.json', '{');
    await expect(inventory_package_licenses(root)).rejects.toBeInstanceOf(SyntaxError);
    await put('server/package.json', '{"name":"workspace"}');
    const outer = 'server/node_modules/outer';
    await put(outer + '/package.json', '{"name":"outer","license":"SEE LICENSE IN nested/alias"}');
    await put(outer + '/TERMS', 'parent terms');
    await put(
      outer + '/nested/package.json',
      '{"name":"inner","license":"SEE LICENSE IN ../TERMS"}',
    );
    await symlink('../TERMS', join(root, outer, 'nested/alias'));
    const inventory = await inventory_package_licenses(root);
    expect(inventory.packages.find((entry) => entry.name === 'outer')!.declared_file).toEqual({
      path: 'nested/alias',
      finding: 'outside-package',
    });
    expect(inventory.packages.find((entry) => entry.name === 'inner')!.declared_file).toEqual({
      path: '../TERMS',
      finding: 'unsafe',
    });
    expect(
      inventory.packages.every(
        (entry) => entry.missing_own_license && entry.license_files.length === 0,
      ),
    ).toBe(true);
    expect(inventory.missing_own_licenses.map((entry) => entry.name)).toEqual([
      'workspace',
      'outer',
      'inner',
    ]);
    await symlink('nested', join(root, outer, 'directory-alias'));
    await put(
      outer + '/package.json',
      '{"name":"outer","license":"SEE LICENSE IN directory-alias/alias"}',
    );
    const aliased = await inventory_package_licenses(root);
    expect(aliased.packages.find((entry) => entry.name === 'outer')!.declared_file).toEqual({
      path: 'directory-alias/alias',
      finding: 'outside-package',
    });
  });
});
