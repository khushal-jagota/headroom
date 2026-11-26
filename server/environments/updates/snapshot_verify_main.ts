/** Installer-only entry, run after the new home passes health: the snapshot's full check. */
import { verify_update_snapshot } from '../backup/capture.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
const args = process.argv.slice(2);
if (args.length !== 2) {
  process.stderr.write('expected snapshot and backup directory\n');
  process.exitCode = 1;
} else {
  const [snapshot, backup_directory] = args as [string, string];
  for (const path of [snapshot, backup_directory]) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  await verify_update_snapshot(snapshot, backup_directory);
}
