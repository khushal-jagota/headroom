export const HOME_HEALTH_TIMEOUT_SECONDS = 60;
export const HOME_SERVICE_COMMAND_TIMEOUT_SECONDS = 30;
export const HOME_SERVICE_OBSERVATION_TIMEOUT_SECONDS = 10;
export const HOME_CLEANUP_TIMEOUT_SECONDS = 120;
/** Scheduled jobs beside the home and its backup, named by suffix on both systems. */
export const HOME_SCHEDULE_SUFFIXES = ['.maintenance', '.stall-watchdog'] as const;
/** The account's `panels` command, relative to the account home. */
export const HOME_COMMAND = '.local/bin/panels';
/** The update credential, relative to the installation root, inside the server's private secrets folder. */
export const HOME_UPDATE_TOKEN_FILE = 'data/secrets/update-token';
