/** A successful transfer cannot prove cleanup when shutdown interrupts a waiting stream. */
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { expect, test } from 'vitest';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { createVideoUploadRoutes } from '../conversation/video_uploads/routes.ts';
import { createVoiceRoutes } from '../conversation/voice_transcription/routes.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';

// Reuse the ordinary app and Ticket fixture. No listener, host, or media process is needed.
test('shutdown waits for upload settlement and a disconnect records Python interruption text', async () => {
  const original = fixture();
  const controller = new AbortController();
  let failed!: () => void;
  const failure_committed = new Promise<void>((resolve) => {
    failed = resolve;
  });
  let progressed!: () => void;
  const progress_committed = new Promise<void>((resolve) => {
    progressed = resolve;
  });
  let scheduled = false;
  const database: Database = {
    read: (work) => original.read(work),
    write(work, options) {
      const result = original.write(work, options);
      const progress = original.read((sql) =>
        sql.get<{ received_bytes: number; state: string }>(
          'SELECT received_bytes,state FROM conversation_video_uploads LIMIT 1',
        ),
      );
      if (progress?.state === 'failed') failed();
      if (!scheduled && progress !== undefined && progress.received_bytes >= 8) {
        scheduled = true;
        queueMicrotask(progressed);
      }
      return result;
    },
    close() {},
  };
  const service = new VideoUploadService(database, fixture_path(original), {
    now: () => 100,
    signal: controller.signal,
    disk_free_bytes: () => 1024,
    require_probe: () => '/recorded/ffprobe',
    probe_video: async () => {
      throw new Error('An incomplete transfer must not reach the video probe');
    },
    limits: {
      max_video_bytes: 32,
      video_budget_bytes: 32,
      free_space_floor_bytes: 0,
      transfer_chunk_bytes: 4,
      progress_record_bytes: 8,
    },
  });
  const reserved = service.reserve({
    destination: { kind: 'chief', id: 'chief', expected_conversation_id: null },
    filename: 'synthetic.mp4',
    byte_count: 12,
  });
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(original),
  });
  const voice_calls: unknown[] = [];
  const app = await createApp(config, {
    database,
    areas: [
      {
        prefix: '/api/conversation',
        routes: createVoiceRoutes({
          adapters: {
            trim_surrounding_silence: async (audio) => [audio, false] as const,
            http_post: async (request) => {
              voice_calls.push(request);
              return { status_code: 200, json: () => ({ text: 'Recorded words.' }) };
            },
          },
        }),
      },
      {
        prefix: '/api/conversation/video-uploads',
        routes: createVideoUploadRoutes(service, null, controller.signal),
      },
    ],
  });
  let sent = false;
  const stream = new Readable({
    read() {
      if (!sent) {
        sent = true;
        this.push(Buffer.from('partpart'));
      }
      // The source intentionally waits forever for its next chunk and never sends EOF.
    },
  });
  try {
    // Voice needs no core. An absent key must reach its ordinary unconfigured answer.
    const voice = await app.inject({
      method: 'POST',
      url: '/api/conversation/voice-transcriptions',
      payload: { audio: 'aGVsbG8=', media_type: 'audio/webm' },
    });
    expect(voice.statusCode).toBe(503);
    expect(voice.json()).toEqual({
      detail: 'voice transcription needs PLAN_GROQ_API_KEY, which is not set',
    });
    expect(voice_calls).toEqual([]);
    config.voice_transcription_api_key = 'synthetic-voice-key';
    const transcribed = await app.inject({
      method: 'POST',
      url: '/api/conversation/voice-transcriptions',
      payload: { audio: 'aGVsbG8=', media_type: 'audio/webm;codecs=opus' },
    });
    expect(transcribed.statusCode).toBe(200);
    expect(transcribed.json()).toEqual({ transcript: 'Recorded words.' });
    expect(voice_calls).toMatchObject([
      {
        headers: { Authorization: 'Bearer synthetic-voice-key' },
        file: { contents: Buffer.from('hello'), media_type: 'audio/ogg' },
      },
    ]);
    expect(
      original.read((sql) =>
        sql.get<{ count: number }>('SELECT count(*) AS count FROM conversations'),
      )?.count,
    ).toBe(0);
    const response = app
      .inject({
        method: 'PUT',
        url: `/api/conversation/video-uploads/${reserved.upload_id}/bytes`,
        headers: { 'content-type': 'application/octet-stream', 'transfer-encoding': 'chunked' },
        payload: stream,
      })
      .then(
        (value) => ({ response: value }),
        (error: unknown) => ({ error }),
      );
    await progress_committed;
    const unfinished = await service.drain({ deadline: performance.now() / 1000 + 0.01 }).then(
      () => null,
      (error: unknown) => error,
    );
    expect(unfinished).toBeInstanceOf(ServerLifecycleError);
    expect(unfinished).toHaveProperty(
      'message',
      'Video transfers remain unfinished at the shutdown deadline.',
    );
    expect(service.status(reserved.upload_id)).toMatchObject({
      state: 'transferring',
      received_bytes: 8,
      error: null,
    });
    expect(existsSync(join(service.root, reserved.upload_id))).toBe(true);
    // Graceful shutdown retains the transfer. Client disconnect performs cleanup.
    stream.destroy(new Error('aborted'));
    const disconnected = await response;
    expect('error' in disconnected && disconnected.error).toBeInstanceOf(Error);
    await failure_committed;
    await service.drain({ deadline: performance.now() / 1000 + 1 });
    expect(service.status(reserved.upload_id)).toMatchObject({
      state: 'failed',
      received_bytes: 8,
      error: 'Upload interrupted.',
    });
    expect(existsSync(join(service.root, reserved.upload_id))).toBe(false);
  } finally {
    stream.destroy();
    controller.abort();
    await service.drain({ deadline: performance.now() / 1000 + 1 });
    await app.close();
  }
});
