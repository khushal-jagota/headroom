/** Installer-only entry, after confirmed stop; ordinary restore owns validation/rollback. */
import { restore_database_snapshot } from '../backup/restore.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
const args = process.argv.slice(2);
if (args.length !== 2) {
  process.stderr.write('expected snapshot and destination database\n');
  process.exitCode = 1;
} else {
  for (const path of args) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  await restore_database_snapshot(args[0]!, args[1]!, { live_stopped: true });
}
