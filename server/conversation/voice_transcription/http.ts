/** HTTPX-style phase timeouts, with no connection pool and no redirect replay. */
import { request as http_request } from 'node:http';
import { request as https_request } from 'node:https';
import type { IncomingMessage } from 'node:http';
import type { Socket } from 'node:net';
import type { VoiceTranscriptionRequest, VoiceTranscriptionResponse } from './contracts.ts';
import { VoiceTranscriptionTransportError } from './contracts.ts';
import { require_production_voice_io } from './io.ts';
import { voice_multipart } from './multipart.ts';
import { VOICE_HTTP_WRITE_CHUNK_BYTES } from './configuration.ts';
import { decode_voice_content, parse_voice_json } from './response.ts';

function write_error(error: unknown): boolean {
  return (
    error instanceof Error &&
    'code' in error &&
    [
      'EPIPE',
      'ECONNRESET',
      'ERR_STREAM_DESTROYED',
      'ERR_STREAM_WRITE_AFTER_END',
      'ERR_STREAM_PREMATURE_CLOSE',
    ].includes(String(error.code))
  );
}

async function acquire_response(
  input: VoiceTranscriptionRequest,
): Promise<VoiceTranscriptionResponse> {
  const url = new URL(input.url);
  if (!['http:', 'https:'].includes(url.protocol))
    throw new Error('Unsupported voice provider protocol');
  const multipart = voice_multipart(input);
  const upstream = (url.protocol === 'https:' ? https_request : http_request)(url, {
    method: 'POST',
    agent: false,
    headers: {
      ...input.headers,
      'Content-Type': multipart.content_type,
      'Content-Length': String(multipart.content_length),
    },
  });
  const closed = new Promise<void>((resolve) => upstream.once('close', resolve));
  const timeout_ms = input.timeout_seconds * 1000;
  let connect_timer: ReturnType<typeof setTimeout> | undefined;
  let write_timer: ReturnType<typeof setTimeout> | undefined;
  let read_timer: ReturnType<typeof setTimeout> | undefined;
  let socket: Socket | undefined;
  let peer: IncomingMessage | undefined;
  let peer_closed: Promise<void> | undefined;
  let read_phase = false;
  const fail = (phase: string) => upstream.destroy(new Error(`Voice ${phase} timeout`));
  const arm_connect = () => {
    clearTimeout(connect_timer);
    connect_timer = setTimeout(() => fail('connect'), timeout_ms);
  };
  const arm_read = () => {
    if (!read_phase) return;
    clearTimeout(read_timer);
    read_timer = setTimeout(() => fail('read'), timeout_ms);
  };
  const connected = new Promise<void>((resolve, reject) => {
    upstream.once('error', reject);
    upstream.once('socket', (assigned) => {
      socket = assigned;
      assigned.on('data', arm_read);
      assigned.once('connect', () => {
        if (url.protocol === 'https:') arm_connect();
        else {
          clearTimeout(connect_timer);
          resolve();
        }
      });
      if (url.protocol === 'https:')
        assigned.once('secureConnect', () => {
          clearTimeout(connect_timer);
          resolve();
        });
    });
  });
  const response = new Promise<IncomingMessage>((resolve, reject) => {
    upstream.once('error', reject);
    upstream.once('response', (incoming) => {
      peer = incoming;
      peer_closed = new Promise<void>((done) => incoming.once('close', done));
      // The consumer below reads after the complete request body is sent.
      incoming.on('error', () => {});
      arm_read();
      resolve(incoming);
    });
  });
  // An error during connection or writing can reject the later response wait first.
  void connected.catch(() => {});
  void response.catch(() => {});
  const abort = () => {
    const reason = input.signal?.reason;
    upstream.destroy(reason instanceof Error ? reason : new Error(String(reason)));
    peer?.destroy();
  };
  input.signal?.addEventListener('abort', abort, { once: true });
  arm_connect();
  if (input.signal?.aborted) abort();
  const write = (part: Uint8Array | null): Promise<void> =>
    new Promise((resolve, reject) => {
      write_timer = setTimeout(() => fail('write'), timeout_ms);
      const failure = (error: Error) => {
        clearTimeout(write_timer);
        reject(error);
      };
      upstream.once('error', failure);
      const completed = (error?: Error | null) => {
        clearTimeout(write_timer);
        upstream.removeListener('error', failure);
        if (error) reject(error);
        else resolve();
      };
      try {
        if (part === null) upstream.end(completed);
        else upstream.write(part, completed);
      } catch (error) {
        completed(error instanceof Error ? error : new Error(String(error)));
      }
    });
  try {
    await connected;
    input.signal?.throwIfAborted();
    try {
      for (const part of multipart.parts) {
        for (let offset = 0; offset < part.byteLength; offset += VOICE_HTTP_WRITE_CHUNK_BYTES) {
          input.signal?.throwIfAborted();
          await write(part.subarray(offset, offset + VOICE_HTTP_WRITE_CHUNK_BYTES));
        }
      }
      await write(null);
    } catch (error) {
      input.signal?.throwIfAborted();
      // A server can refuse a large upload before its request body finishes.
      // HTTPX suppresses genuine write errors and reads that early HTTP answer.
      if (peer === undefined || !write_error(error)) throw error;
    }
    read_phase = true;
    arm_read();
    const incoming = await response;
    arm_read();
    const chunks: Buffer[] = [];
    for await (const chunk of incoming) {
      arm_read();
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    input.signal?.throwIfAborted();
    const body = decode_voice_content(Buffer.concat(chunks), incoming.headers['content-encoding']);
    return { status_code: incoming.statusCode ?? 0, json: () => parse_voice_json(body) };
  } finally {
    clearTimeout(connect_timer);
    clearTimeout(write_timer);
    clearTimeout(read_timer);
    input.signal?.removeEventListener('abort', abort);
    socket?.removeListener('data', arm_read);
    upstream.destroy();
    peer?.destroy();
    await closed;
    await peer_closed;
  }
}

export async function post_voice_http(
  input: VoiceTranscriptionRequest,
): Promise<VoiceTranscriptionResponse> {
  input.signal?.throwIfAborted();
  require_production_voice_io();
  try {
    return await acquire_response(input);
  } catch (error) {
    input.signal?.throwIfAborted();
    throw new VoiceTranscriptionTransportError('voice request failed', { cause: error });
  }
}
