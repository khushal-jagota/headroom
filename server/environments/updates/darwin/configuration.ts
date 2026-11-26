export const DARWIN_HOME_COMMAND_SECONDS = 120;
export const DARWIN_HOME_OBSERVATION_SECONDS = 10;
export const DARWIN_HOME_POLL_MILLISECONDS = 500;
// Each stall watchdog run changes its process twice, at start and at exit, 30 seconds apart.
// A stop pass takes under a second, so at most two passes can meet a change. Five leave margin.
export const DARWIN_HOME_STOP_CAPTURE_PASSES = 5;
export const DARWIN_HOME_SCHEDULE_SUFFIXES = ['.maintenance', '.stall-watchdog'] as const;
export const DARWIN_HOME_AUXILIARY_ENVIRONMENT = [
  'HOME',
  'PATH',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_WORKSPACE_ROOT',
  'PLAN_PORT',
] as const;
