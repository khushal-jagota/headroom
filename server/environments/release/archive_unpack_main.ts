/** Fixed private entry. It never imports candidate code or discovers another owner. */
import {
  mac_inspection_failure,
  unpack_archive_child,
  write_archive_message,
} from './archive_unpack.ts';
import type { UnpackRequest } from './archive_unpack.ts';
let purpose: unknown;
try {
  process.umask(0o077);
  const request: unknown = JSON.parse(process.argv[2] ?? '');
  purpose =
    request && typeof request === 'object' && 'purpose' in request ? request.purpose : undefined;
  if (
    !request ||
    typeof request !== 'object' ||
    !['spool_root'].every((key) => typeof (request as Record<string, unknown>)[key] === 'string') ||
    !('purpose' in request) ||
    !(
      (request.purpose === 'mac-inspection' &&
        'archive_size' in request &&
        Number.isSafeInteger(request.archive_size) &&
        Number(request.archive_size) > 0 &&
        'archive_sha256' in request &&
        typeof request.archive_sha256 === 'string') ||
      ('destination' in request &&
        typeof request.destination === 'string' &&
        request.purpose === 'application' &&
        'archive_sha256' in request &&
        typeof request.archive_sha256 === 'string' &&
        'expected_sha' in request &&
        typeof request.expected_sha === 'string')
    )
  )
    throw new Error('invalid unpack request');
  await unpack_archive_child(request as UnpackRequest, process.stdin, process.stdout);
} catch (error) {
  process.exitCode = 1;
  try {
    await write_archive_message(
      process.stdout,
      purpose === 'mac-inspection'
        ? mac_inspection_failure(error)
        : {
            type: 'error',
            message: error instanceof Error ? error.message : String(error),
          },
    );
  } catch {
    /* Parent-channel loss already makes this invocation unsuccessful. */
  }
} finally {
  process.stdin.destroy();
}
