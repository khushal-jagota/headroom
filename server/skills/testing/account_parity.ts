/** One export-only witness. No server, provider, or network connection. */
import { lstatSync, mkdirSync, readlinkSync, readdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { account_skill_provisioning_conflict, provision_account_skills } from '../account.ts';
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
  const repeated = provision_account_skills(first, homes);
  const stable = inode === lstatSync(link).ino;
  const other = provision_account_skills(second, [join(root, 'unused'), ...homes]);
  const legacy = join(root, 'legacy');
  mkdirSync(legacy);
  symlinkSync(join(first, 'skills'), join(legacy, 'skills'));
  const converted = provision_account_skills(first, [legacy]);
  const foreign = join(homes[0], 'skills', 'panels-rollover');
  symlinkSync(join(root, 'foreign'), foreign);
  const conflict = provision_account_skills(first, homes);
  const tree = (path: string): unknown[] =>
    readdirSync(path)
      .sort()
      .flatMap((name) => {
        const full = join(path, name),
          metadata = lstatSync(full);
        if (metadata.isSymbolicLink())
          return [[full.slice(root.length), 'link', readlinkSync(full).replaceAll(root, '<root>')]];
        if (metadata.isDirectory()) return [[full.slice(root.length), 'dir'], ...tree(full)];
        return [[full.slice(root.length), 'file']];
      });
  return {
    preflight,
    untouched,
    published,
    repeated,
    stable,
    other: other?.replaceAll(root, '<root>'),
    converted,
    conflict: conflict?.replaceAll(root, '<root>'),
    tree: tree(root),
  };
}
