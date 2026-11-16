/** A short descendant sweep never changes launchd's liveness decision. */
export const WORKER_SWEEP_SECONDS = 1;
export const WORKER_SWEEP_TERM_SECONDS = 0.2;
export const WORKER_SWEEP_LIMIT_NOTICE =
  'Mac Worker cleanup is best effort: a process that clears PANELS_WORKER_JOB cannot be identified by this sweep.';
export const WORKER_JOB_ENVIRONMENT_KEY = 'PANELS_WORKER_JOB';
