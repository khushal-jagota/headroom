/** Installer-only entry, run from the current app after the stop and before the swap. */
import { create_database_backup } from '../backup/capture.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
import { InstallationRecord } from './installer/record.ts';
const args = process.argv.slice(2);
if (args.length !== 5) {
  process.stderr.write(
    'expected database, backup directory, deployed revision, data directory and attempt\n',
  );
  process.exitCode = 1;
} else {
  const [database, backup_directory, deployed_revision, data_directory, attempt_id] = args as [
    string,
    string,
    string,
    string,
    string,
  ];
  for (const path of [database, backup_directory, data_directory]) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  const snapshot = await create_database_backup(database, backup_directory, deployed_revision);
  new InstallationRecord({ data_directory, attempt_id }).snapshot_taken(snapshot);
}
