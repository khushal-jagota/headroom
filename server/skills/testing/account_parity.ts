/** One export-only witness. No server, provider, or network connection. */
import {
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  readdirSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';
import { account_skill_provisioning_conflict, provision_account_skills } from '../account.ts';
import { decode_native_path, native_path_bytes } from '../../executor/mac/platform.ts';
export function account_parity(root: string): unknown {
  const first = join(root, 'first'),
    second = join(root, 'second');
  const homes = ['hermes', 'codex', 'claude'].map((name) => join(root, name));
  const preflight = account_skill_provisioning_conflict(first, homes);
  const untouched = readdirSync(root).sort();
  const published = provision_account_skills(first, homes);
  const link = join(homes[0], 'skills', 'panels'),
    inode = lstatSync(link).ino;
  symlinkSync(
    join(first, 'skills', 'panels-worker-personal-task'),
    join(homes[0], 'skills', 'panels-worker-personal-task'),
  );
  symlinkSync(join(first, 'skills', 'gone'), join(homes[0], 'skills', 'gone'));
  mkdirSync(join(homes[0], 'skills', 'user'));
  const byte_entries: Buffer[] = [],
    byte_inodes: number[] = [];
  let byte_foreign_target = Buffer.alloc(0);
  if (process.platform === 'linux') {
    const raw = (parent: string, byte: number) =>
      Buffer.concat([Buffer.from(parent + '/'), Buffer.from([byte])]);
    byte_entries.push(
      raw(join(homes[0], 'skills'), 0x80),
      raw(join(homes[0], 'skills'), 0x81),
      Buffer.from(join(homes[0], 'skills', '\ufffd')),
    );
    byte_foreign_target = raw(join(root, 'foreign'), 0x81);
    writeFileSync(byte_entries[0]!, 'raw foreign file');
    symlinkSync(byte_foreign_target, byte_entries[1]!);
    writeFileSync(byte_entries[2]!, 'Unicode sibling');
    symlinkSync(raw(join(first, 'skills'), 0x82), raw(join(homes[0], 'skills'), 0x82));
    byte_inodes.push(...byte_entries.map((path) => lstatSync(path).ino));
  }
  const repeated = provision_account_skills(first, homes);
  const byte_entries_unchanged = byte_entries.map(
    (path, index) =>
      lstatSync(path).ino === byte_inodes[index] &&
      (index === 1
        ? readlinkSync(path, { encoding: 'buffer' }).equals(byte_foreign_target)
        : readFileSync(path, 'utf8') === (index === 0 ? 'raw foreign file' : 'Unicode sibling')),
  );
  const stable = inode === lstatSync(link).ino;
  const other = provision_account_skills(second, [join(root, 'unused'), ...homes]);
  const legacy = join(root, 'legacy');
  mkdirSync(legacy);
  symlinkSync(join(first, 'skills'), join(legacy, 'skills'));
  const converted = provision_account_skills(first, [legacy]);
  const foreign = join(homes[0], 'skills', 'panels-rollover');
  symlinkSync(join(root, 'foreign'), foreign);
  const conflict = provision_account_skills(first, homes);
  const unreadable = join(root, 'unreadable');
  writeFileSync(unreadable, 'not a directory');
  const unreadable_ownership = account_skill_provisioning_conflict(unreadable, homes)?.includes(
    'belongs to unreadable ownership (',
  );
  const unreadable_publication = provision_account_skills(unreadable, homes)?.includes(
    'belongs to an unreadable or conflicting account entry (',
  );
  const tree = (path: string): unknown[] =>
    readdirSync(native_path_bytes(path), { encoding: 'buffer' })
      .map(decode_native_path)
      .sort()
      .flatMap((name) => {
        const full = join(path, name),
          metadata = lstatSync(native_path_bytes(full));
        if (metadata.isSymbolicLink())
          return [
            [
              full.slice(root.length),
              'link',
              decode_native_path(
                readlinkSync(native_path_bytes(full), { encoding: 'buffer' }),
              ).replaceAll(root, '<root>'),
            ],
          ];
        if (metadata.isDirectory()) return [[full.slice(root.length), 'dir'], ...tree(full)];
        return [[full.slice(root.length), 'file']];
      });
  return {
    preflight,
    untouched,
    published,
    repeated,
    byte_entries_unchanged,
    stable,
    other: other?.replaceAll(root, '<root>'),
    converted,
    conflict: conflict?.replaceAll(root, '<root>'),
    unreadable_ownership,
    unreadable_publication,
    tree: tree(root),
  };
}
