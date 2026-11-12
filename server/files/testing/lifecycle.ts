/** Direct comparison of real filesystem boundaries; no server or provider is constructed. */
import {
  existsSync,
  lstatSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  renameSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import {
  quarantine_manager_files,
  restore_quarantined_files,
  purge_quarantined_files,
} from '../lifecycle.ts';
import { list_artifact_entries, list_files } from '../manager_files.ts';
import { createDatabase } from '../../core/database/index.ts';
import { InMemoryConversationSystem } from '../../conversation/in_memory_conversation_system.ts';
import { delete_manager } from '../../managers/service.ts';

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  function visit(directory: string, prefix: string): void {
    for (const name of readdirSync(directory).sort()) {
      const path = join(directory, name);
      const relative = prefix + name;
      const info = lstatSync(path);
      if (info.isSymbolicLink()) result[relative] = 'symlink:' + readlinkSync(path);
      else if (info.isDirectory()) visit(path, relative + '/');
      else result[relative] = readFileSync(path).toString('hex');
    }
  }
  visit(root, '');
  return result;
}

export async function manager_files_lifecycle(db_path: string) {
  const root = join(dirname(db_path), 'files');
  const managers = join(root, 'sprint-items');
  const owner = join(managers, 'si_proof');
  const outside = tree(join(root, 'outside'));
  const before = tree(root);
  const listed = await list_files(db_path, 'si_proof');
  const inventory = await list_artifact_entries(db_path, 'si_proof');
  const first = quarantine_manager_files(db_path, 'si_proof')!;
  const quarantined = tree(first.quarantine);
  const absent_during_quarantine = !existsSync(owner);
  restore_quarantined_files(first);
  const restored = tree(root);
  const second = quarantine_manager_files(db_path, 'si_proof');
  purge_quarantined_files(second);
  const purged = tree(root);
  const quarantine_empty = readdirSync(join(root, '.sprint-item-quarantine')).length === 0;

  renameSync(managers, managers + '-saved');
  symlinkSync('absent', managers);
  let root_refusal = '';
  try {
    quarantine_manager_files(db_path, 'si_proof');
  } catch (error) {
    root_refusal = (error as Error).message;
  }
  let root_inventory_refusal = '';
  try {
    await list_artifact_entries(db_path, 'si_proof');
  } catch (error) {
    root_inventory_refusal = (error as Error).message;
  }
  unlinkSync(managers);
  renameSync(managers + '-saved', managers);

  symlinkSync('../outside', owner);
  const owner_listing = await list_files(db_path, 'si_proof');
  const owner_inventory = await list_artifact_entries(db_path, 'si_proof');
  const link = quarantine_manager_files(db_path, 'si_proof')!;
  const quarantined_link = lstatSync(link.quarantine).isSymbolicLink();
  restore_quarantined_files(link);
  const restored_link = readlinkSync(owner);
  purge_quarantined_files(quarantine_manager_files(db_path, 'si_proof'));
  const outside_unchanged = JSON.stringify(tree(join(root, 'outside'))) === JSON.stringify(outside);
  writeFileSync(owner, 'Owner file instead of directory');
  purge_quarantined_files(quarantine_manager_files(db_path, 'si_proof'));
  const owner_file_purged = !existsSync(owner);
  const missing = quarantine_manager_files(db_path, 'si_proof');
  restore_quarantined_files(missing);
  purge_quarantined_files(missing);
  return {
    before,
    listed,
    inventory,
    quarantined,
    absent_during_quarantine,
    restored,
    purged,
    quarantine_empty,
    root_refusal,
    root_inventory_refusal,
    owner_listing,
    owner_inventory,
    quarantined_link,
    restored_link,
    outside_unchanged,
    owner_file_purged,
    missing,
    final: tree(root),
  };
}

export async function manager_deletion(db_path: string) {
  const database = createDatabase(db_path);
  const system = new InMemoryConversationSystem();
  const root = join(dirname(db_path), 'files');
  const snapshot = () => ({
    files: tree(root),
    managers: database.read((sql) => sql.all('SELECT * FROM managers ORDER BY id')),
    agents: database.read((sql) => sql.all('SELECT * FROM agents ORDER BY agent_key')),
  });
  try {
    const before = snapshot();
    let failure = '';
    try {
      await delete_manager(system, database, 'si_delete', db_path);
    } catch (error) {
      failure = (error as Error).message;
    }
    const rollback = snapshot();
    database.write((sql) => sql.run('DROP TRIGGER fail_manager_deletion'));
    const deleted = await delete_manager(system, database, 'si_delete', db_path);
    const after = snapshot();
    const quarantine_empty = readdirSync(join(root, '.sprint-item-quarantine')).length === 0;
    return { before, failure, rollback, deleted, after, quarantine_empty };
  } finally {
    database.close();
  }
}
