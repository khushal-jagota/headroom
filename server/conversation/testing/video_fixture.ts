/** A real MP4 for upload placement, playback, and deferred Worker delivery proofs. */
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/** The size of the fixture picture, which a browser reads from the file. */
export const VIDEO_FIXTURE_SIZE = { width: 160, height: 96 } as const;

export function create_video_fixture(folder: string): string {
  mkdirSync(folder, { recursive: true });
  const path = join(folder, 'screen-recording.mp4');
  const result = spawnSync(
    'ffmpeg',
    [
      '-v',
      'error',
      '-f',
      'lavfi',
      '-i',
      `testsrc=size=${VIDEO_FIXTURE_SIZE.width}x${VIDEO_FIXTURE_SIZE.height}:rate=10`,
      '-t',
      '4',
      '-c:v',
      'libx264',
      '-pix_fmt',
      'yuv420p',
      '-movflags',
      '+faststart',
      path,
    ],
    { timeout: 10_000, encoding: 'utf8' },
  );
  if (result.status !== 0)
    throw new Error(`ffmpeg fixture failed: ${result.error ?? ''} ${result.stderr}`);
  return path;
}
