/** Installer-only entry, run after the new home passes health: the snapshot's full check. */
import { verify_update_snapshot } from '../backup/capture.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
import { InstallationRecord } from './installer/record.ts';
const args = process.argv.slice(2);
if (args.length !== 4) {
  process.stderr.write('expected snapshot, backup directory, data directory and attempt\n');
  process.exitCode = 1;
} else {
  const [snapshot, backup_directory, data_directory, attempt_id] = args as [
    string,
    string,
    string,
    string,
  ];
  for (const path of [snapshot, backup_directory, data_directory]) {
    assert_test_database_path(path);
    assert_test_database_path(expand_path(path));
  }
  const record = new InstallationRecord({ data_directory, attempt_id });
  await verify_update_snapshot(snapshot, backup_directory, (part, seconds) =>
    record.snapshot_timed({ [part]: seconds }),
  );
}
