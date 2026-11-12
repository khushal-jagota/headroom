/** Only these production adapters can start ffmpeg or contact the provider. */
import { spawn } from 'node:child_process';
import type { VoiceTranscriptionAdapters } from './contracts.ts';
import { VoiceTranscriptionTransportError } from './contracts.ts';
import { FFMPEG_PATH, SILENCE_TRIM_FILTER, SILENCE_TRIM_TIMEOUT_SECONDS } from './configuration.ts';
import { silence_trim_result } from './logic.ts';
function require_production_io(): void {
  if (
    process.env.PLAN_TEST_MODE === '1' ||
    process.env.VITEST === 'true' ||
    process.env.PYTEST_CURRENT_TEST !== undefined
  )
    throw new Error('Production voice I/O is unavailable in tests. Inject recording adapters.');
}
export async function trim_surrounding_silence(
  audio: Uint8Array,
  options: { signal?: AbortSignal } = {},
): Promise<readonly [Uint8Array, boolean]> {
  options.signal?.throwIfAborted();
  require_production_io();
  const child = spawn(
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
  let settled = false;
  const kill_group = () => {
    try {
      if (child.pid !== undefined) process.kill(-child.pid, 'SIGKILL');
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH'))
        child.kill('SIGKILL');
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
        settled = true;
        resolve(-1);
      } else stop();
    });
    child.once('exit', (code) => {
      settled = true;
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
    // Preserve buffered audio while the owned group's inherited pipe holders stop.
    kill_group();
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
    if (!settled) stop();
    await exited;
    child.stdin.destroy();
    child.stdout.destroy();
    child.stderr.destroy();
  }
}
export const production_voice_transcription_adapters: VoiceTranscriptionAdapters = {
  trim_surrounding_silence,
  async http_post(request) {
    request.signal?.throwIfAborted();
    require_production_io();
    const form = new FormData();
    form.append(
      'file',
      new Blob([Uint8Array.from(request.file.contents)], {
        type: request.file.media_type,
      }),
      request.file.filename,
    );
    for (const [key, value] of Object.entries(request.data)) form.append(key, value);
    let response: Response;
    let body: string;
    try {
      const timeout = AbortSignal.timeout(request.timeout_seconds * 1000);
      const signal = request.signal ? AbortSignal.any([request.signal, timeout]) : timeout;
      response = await fetch(request.url, {
        method: 'POST',
        headers: request.headers,
        body: form,
        redirect: 'manual',
        signal,
      });
      body = await response.text();
    } catch (error) {
      request.signal?.throwIfAborted();
      throw new VoiceTranscriptionTransportError('voice request failed', { cause: error });
    }
    return { status_code: response.status, json: () => JSON.parse(body) as unknown };
  },
};
