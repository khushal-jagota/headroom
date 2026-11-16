/** Bounded local ffprobe adapter. A valid stream is not a full decode proof. */
import { is_record as object } from '../../core/records.ts';
import { ChildProcesses, signal_child } from '../../lifecycle/owned_processes.ts';
import { accessSync, constants, statSync } from 'node:fs';
import { open } from 'node:fs/promises';
import { delimiter, extname, isAbsolute, join, resolve } from 'node:path';
import { VideoUploadError } from './contracts.ts';
import {
  CONTAINER_HEADER_BYTES,
  PROBE_OUTPUT_BYTES,
  PROBE_TIMEOUT_SECONDS,
} from './configuration.ts';
export function require_probe(): string {
  for (const folder of (process.env.PATH ?? '').split(delimiter)) {
    const candidate = join(folder || '.', 'ffprobe');
    try {
      if (!statSync(candidate).isFile()) continue;
      accessSync(candidate, constants.X_OK);
      return isAbsolute(candidate) ? candidate : resolve(candidate);
    } catch {
      /* Continue through PATH. */
    }
  }
  throw new VideoUploadError('Video uploads are unavailable: ffprobe is not installed.', 503);
}
export async function probe_video(
  path: string,
  filename: string,
  options: { signal?: AbortSignal } = {},
): Promise<string> {
  options.signal?.throwIfAborted();
  const children = new ChildProcesses();
  const child = children.spawn(
    require_probe(),
    [
      '-v',
      'error',
      '-protocol_whitelist',
      'file',
      '-show_entries',
      'format=format_name:format_tags=major_brand:stream=codec_type',
      '-of',
      'json',
      path,
    ],
    { stdio: ['ignore', 'pipe', 'ignore'], detached: true },
  );
  const chunks: Buffer[] = [];
  let bytes = 0;
  let timed_out = false;
  let overflow = false;
  const exited = new Promise<number | null>((resolve, reject) => {
    child.once('error', (error) => {
      reject(error);
    });
    child.once('exit', (code) => {
      resolve(code);
    });
  });
  const output_ended = new Promise<void>((resolve) => {
    child.stdout!.once('end', resolve);
    child.stdout!.once('close', resolve);
    child.stdout!.once('error', () => resolve());
  });
  const stop = () => {
    child.stdout!.destroy();
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
  const abort = () => stop();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) stop();
  const timer = setTimeout(() => {
    timed_out = true;
    stop();
  }, PROBE_TIMEOUT_SECONDS * 1000);
  try {
    child.stdout!.on('data', (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > PROBE_OUTPUT_BYTES) {
        overflow = true;
        stop();
      } else chunks.push(chunk);
    });
    const code = await exited;
    if (code !== 0) stop();
    await output_ended;
    options.signal?.throwIfAborted();
    if (overflow) throw new VideoUploadError('Video validation produced too much output.');
    if (timed_out) throw new VideoUploadError('Video validation failed or timed out.');
    if (code !== 0) throw new VideoUploadError('This file could not be validated as video.');
    let value: unknown;
    try {
      value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      throw new VideoUploadError('Video validation failed or timed out.');
    }
    if (!object(value)) throw new VideoUploadError('Video validation failed or timed out.');
    const format = value.format === undefined ? {} : value.format;
    const streams = value.streams === undefined ? [] : value.streams;
    if (!object(format) || !Array.isArray(streams) || !streams.every(object))
      throw new VideoUploadError('Video validation failed or timed out.');
    const names = format.format_name === undefined ? '' : format.format_name;
    if (typeof names !== 'string')
      throw new VideoUploadError('Video validation failed or timed out.');
    const formats = new Set(names.split(','));
    if (!streams.some((stream) => stream.codec_type === 'video'))
      throw new VideoUploadError('This file has no video stream.');
    const suffix = extname(filename).toLowerCase();
    if (['.mp4', '.mov'].includes(suffix) && formats.has('mov')) {
      const tags = format.tags === undefined ? {} : format.tags;
      if (!object(tags)) throw new VideoUploadError('Video validation failed or timed out.');
      const brand = tags.major_brand === undefined ? '' : tags.major_brand;
      if (typeof brand !== 'string')
        throw new VideoUploadError('Video validation failed or timed out.');
      return brand.trim() === 'qt' || !brand ? 'video/quicktime' : 'video/mp4';
    }
    if (suffix === '.webm' && formats.has('webm')) {
      const input = await open(path, 'r');
      let header: Buffer;
      try {
        options.signal?.throwIfAborted();
        const buffer = Buffer.alloc(CONTAINER_HEADER_BYTES);
        const { bytesRead } = await input.read(buffer, 0, buffer.length, 0);
        options.signal?.throwIfAborted();
        header = buffer.subarray(0, bytesRead);
      } finally {
        await input.close();
      }
      const marker = header.indexOf(Buffer.from([0x42, 0x82]));
      if (marker >= 0 && marker + 2 < header.length) {
        const offset = marker + 2;
        const first = header[offset]!;
        let width = 0;
        for (let candidate = 1; candidate <= 8; candidate++) {
          if (first & (1 << (8 - candidate))) {
            width = candidate;
            break;
          }
        }
        if (width && offset + width <= header.length) {
          let length = first & ((1 << (8 - width)) - 1);
          for (let index = offset + 1; index < offset + width; index++)
            length = length * 256 + header[index]!;
          if (
            length === 4 &&
            header.subarray(offset + width, offset + width + 4).equals(Buffer.from('webm'))
          )
            return 'video/webm';
        }
      }
    }
    throw new VideoUploadError('The video container does not match MP4, MOV, or WebM.');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
    stop();
    await exited.catch(() => {});
    child.stdout!.destroy();
    children.seal();
    await children.settled();
  }
}
