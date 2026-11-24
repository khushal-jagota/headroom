/** A short descendant sweep never changes launchd's liveness decision. */
export const WORKER_SWEEP_SECONDS = 1;
export const WORKER_SWEEP_TERM_SECONDS = 0.2;
export const WORKER_SWEEP_LIMIT_NOTICE =
  'Mac Worker cleanup is best effort: a process that clears PANELS_WORKER_JOB cannot be identified by this sweep.';
export const WORKER_JOB_ENVIRONMENT_KEY = 'PANELS_WORKER_JOB';

/** Also embedded in the signed executable, so old launchers are never probed as a CLI. */
export const WORKER_LAUNCHER_MODE = '--panels-worker-spawn-v2';
export const WORKER_LAUNCHER_FALLBACK_NOTICE =
  'Mac Worker app launcher is unavailable or unusable. Starting through bare Node.';

/** Reserve the rest of the existing start budget for bare Node fallback. */
export const WORKER_APP_START_SECONDS = 2;
