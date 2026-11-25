import { WORKER_FILE_MASK } from './config.ts';

/** Only the synchronous backend launch inherits this mask; journal writes retain their mask. */
export function with_worker_file_mask<T>(launch: () => T): T {
  const previous = process.umask(WORKER_FILE_MASK);
  try {
    return launch();
  } finally {
    process.umask(previous);
  }
}
