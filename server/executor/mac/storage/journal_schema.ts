/** The Python command journal, format one. */
export { COMMAND_JOURNAL_FORMAT_VERSION } from '../../wire.generated.ts';
export const JOURNAL_NAME = 'commands.sqlite3';
export const JOURNAL_REFUSAL =
  'unsupported command journal format; preserve the spool for compatible Panels';
export const JOURNAL_SCHEMA = [
  `CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    command_sequence INTEGER NOT NULL,
    executor_incarnation TEXT NOT NULL,
    mailbox_command_json BLOB NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('recorded','finished')),
    result_json BLOB,
    result_reported INTEGER NOT NULL DEFAULT 0 CHECK(result_reported IN (0,1)),
    outputs_published INTEGER NOT NULL DEFAULT 1 CHECK(outputs_published IN (0,1)),
    recorded_at_ms INTEGER NOT NULL)`,
  "CREATE INDEX unreported ON commands(command_sequence) WHERE phase='finished' AND result_reported=0",
] as const;
