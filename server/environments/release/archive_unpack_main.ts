/** Fixed private entry. It never imports candidate code or discovers another owner. */
import { unpack_archive_child, write_archive_message } from './archive_unpack.ts';
import type { UnpackRequest } from './archive_unpack.ts';
try {
  const inherited_umask = process.umask();
  process.umask(0o077);
  const request: unknown = JSON.parse(process.argv[2] ?? '');
  if (
    !request ||
    typeof request !== 'object' ||
    !['spool_root', 'destination'].every(
      (key) => typeof (request as Record<string, unknown>)[key] === 'string',
    ) ||
    !('purpose' in request) ||
    !(
      request.purpose === 'source-export' ||
      (request.purpose === 'application' &&
        'archive_sha256' in request &&
        typeof request.archive_sha256 === 'string' &&
        'expected_sha' in request &&
        typeof request.expected_sha === 'string')
    )
  )
    throw new Error('invalid unpack request');
  await unpack_archive_child(
    request as UnpackRequest,
    process.stdin,
    process.stdout,
    inherited_umask,
  );
} catch (error) {
  process.exitCode = 1;
  try {
    await write_archive_message(process.stdout, {
      type: 'error',
      message: error instanceof Error ? error.message : String(error),
    });
  } catch {
    /* Parent-channel loss already makes this invocation unsuccessful. */
  }
} finally {
  process.stdin.destroy();
}
