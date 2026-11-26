/** Update-only entry, run by the serving home before the stop. Prints the staged copy. */
import { stage_database_copy } from '../backup/capture.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
const args = process.argv.slice(2);
if (args.length !== 2) {
  process.stderr.write('expected database and backup directory\n');
  process.exitCode = 1;
} else {
  const [database, backup_directory] = args as [string, string];
  for (const path of [database, backup_directory]) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  process.stdout.write((await stage_database_copy(database, backup_directory)) + '\n');
}
