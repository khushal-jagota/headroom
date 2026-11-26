/** Installer-only entry, run after the new home passes health: the snapshot's full check. */
import { UpdateSnapshotCheckFailure, verify_update_snapshot } from '../backup/capture.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
import { InstallationRecord } from './installer/record.ts';
const args = process.argv.slice(2);
if (args.length !== 5) {
  process.stderr.write(
    'expected snapshot, backup directory, live database, data directory and attempt\n',
  );
  process.exitCode = 1;
} else {
  const [snapshot, backup_directory, database, data_directory, attempt_id] = args as [
    string,
    string,
    string,
    string,
    string,
  ];
  for (const path of [snapshot, backup_directory, database, data_directory]) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  const record = new InstallationRecord({ data_directory, attempt_id });
  try {
    await verify_update_snapshot(snapshot, backup_directory, {
      database,
      timed: (part, seconds) => record.snapshot_timed({ [part]: seconds }),
    });
    record.record('snapshot_verified');
  } catch (error) {
    // The home already serves on top of the stepped database, so nothing is undone.
    if (!(error instanceof UpdateSnapshotCheckFailure)) throw error;
    console.error(error);
    record.record('snapshot_check_failed');
  }
}
