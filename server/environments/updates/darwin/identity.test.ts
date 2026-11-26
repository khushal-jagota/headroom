import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it } from 'vitest';
import { pin_existing, release_path, select_release } from '../installer/selection.ts';
import { read_home_identity } from './identity.ts';

it('authenticates the same Mac installation before pinning and through a trial and rollback', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'panels-home-identity-')));
  const from = 'a'.repeat(40),
    to = 'b'.repeat(40),
    uid = process.getuid!(),
    label = 'com.panels.home';
  const node = join(root, 'app/node/bin/node');
  try {
    for (const folder of [join(root, 'app'), release_path(root, to)]) {
      mkdirSync(join(folder, 'node/bin'), { recursive: true });
      writeFileSync(join(folder, 'node/bin/node'), 'node', { mode: 0o700 });
    }
    mkdirSync(join(root, 'data'));
    writeFileSync(
      join(root, 'data/mac-home-installation.json'),
      JSON.stringify({
        version: 2,
        uid,
        root,
        label,
        home_plist: join(root, 'agents', label + '.plist'),
        backup_plist: join(root, 'agents', label + '.backup.plist'),
        installer_node: node,
      }),
    );
    const read = () => read_home_identity(root, 'gui/' + uid + '/' + label, uid);
    const initial = read();
    pin_existing(root, from);
    assert.deepEqual(read(), initial);
    select_release(root, to);
    assert.deepEqual(read(), initial);
    chmodSync(node, 0o600);
    assert.throws(read, /EACCES/);
    select_release(root, from);
    assert.deepEqual(read(), initial);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
