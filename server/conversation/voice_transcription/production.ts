/** Only these production adapters can start ffmpeg or contact the provider. */
import { ChildProcesses, signal_child } from '../../lifecycle/owned_processes.ts';
import type { VoiceTranscriptionAdapters } from './contracts.ts';
import { FFMPEG_PATH, SILENCE_TRIM_FILTER, SILENCE_TRIM_TIMEOUT_SECONDS } from './configuration.ts';
import { silence_trim_result } from './logic.ts';
import { require_production_voice_io } from './io.ts';
import { post_voice_http } from './http.ts';
export async function trim_surrounding_silence(
  audio: Uint8Array,
  options: { signal?: AbortSignal } = {},
): Promise<readonly [Uint8Array, boolean]> {
  options.signal?.throwIfAborted();
  require_production_voice_io();
  const children = new ChildProcesses();
  const child = children.spawn(
    FFMPEG_PATH,
    [
      '-hide_banner',
      '-loglevel',
      'error',
      '-i',
      'pipe:0',
      '-af',
      SILENCE_TRIM_FILTER,
      '-c:a',
      'libopus',
      '-f',
      'ogg',
      'pipe:1',
    ],
    { stdio: ['pipe', 'pipe', 'pipe'], detached: true },
  );
  const chunks: Buffer[] = [];
  let failed = false;
  const kill_group = () => {
    try {
      signal_child(child, 'SIGKILL');
    } catch (error) {
      if (
        !(error instanceof Error && 'code' in error && error.code === 'ESRCH') &&
        child.exitCode === null &&
        child.signalCode === null
      )
        signal_child(child, 'SIGKILL', false);
    }
  };
  const stop = () => {
    child.stdin.destroy();
    child.stdout.destroy();
    child.stderr.destroy();
    kill_group();
  };
  const exited = new Promise<number | null>((resolve) => {
    child.once('error', () => {
      failed = true;
      if (child.pid === undefined) {
        resolve(-1);
      } else stop();
    });
    child.once('exit', (code) => {
      resolve(code);
    });
  });
  const output_ended = new Promise<void>((resolve) => {
    child.stdout.once('end', resolve);
    child.stdout.once('close', resolve);
    child.stdout.once('error', () => {
      failed = true;
      resolve();
    });
  });
  const abort = () => stop();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) stop();
  const timer = setTimeout(() => {
    failed = true;
    stop();
  }, SILENCE_TRIM_TIMEOUT_SECONDS * 1000);
  try {
    child.stdin.on('error', () => {
      /* A failed ffmpeg can close its input early. */
    });
    child.stdout.on('data', (chunk: Buffer) => {
      chunks.push(chunk);
    });
    child.stderr.resume();
    child.stdin.end(audio);
    const code = await exited;
    // Preserve buffered audio. A reaped leader's numeric group is no longer ours
    // to signal, even when a descendant retains an inherited pipe.
    if (code !== 0) stop();
    await output_ended;
    options.signal?.throwIfAborted();
    return silence_trim_result(
      audio,
      failed
        ? null
        : {
            exit_code: code ?? -1,
            contents: Buffer.concat(chunks),
          },
    );
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
    stop();
    await exited;
    child.stdin.destroy();
    child.stdout.destroy();
    child.stderr.destroy();
    children.seal();
    await children.settled();
  }
}
export const production_voice_transcription_adapters: VoiceTranscriptionAdapters = {
  trim_surrounding_silence,
  http_post: post_voice_http,
};
