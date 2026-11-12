/** Voice input has no conversation dependency and never stores the audio. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { body_fields } from '../http/intake.ts';
import { HttpError } from '../../core/http/errors.ts';
import {
  transcribe_conversation_audio,
  VoiceTranscriptionUnconfigured,
  VoiceTranscriptionFailed,
} from './index.ts';
import type { VoiceTranscriptionAdapters } from './index.ts';
export function createVoiceRoutes(
  options: { adapters?: VoiceTranscriptionAdapters; signal?: AbortSignal } = {},
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.post('/voice-transcriptions', async (request, reply) => {
      const fields = body_fields(request.body);
      const audio = fields.text('audio');
      const media_type = fields.text('media_type', false, false) ?? 'audio/webm';
      fields.extras(['audio', 'media_type']);
      fields.finish();
      if (
        !['audio/webm', 'audio/mp4', 'audio/ogg', 'audio/wav'].includes(
          media_type.split(';', 1)[0].trim().toLowerCase(),
        )
      )
        throw new HttpError(422, `unsupported voice media type ${media_type}`);
      const maximum = 25 * 1024 * 1024;
      if (audio!.length > 4 * Math.ceil(maximum / 3))
        throw new HttpError(422, 'a voice clip is too large');
      if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(audio!))
        throw new HttpError(422, "a piece's data is not base64");
      const bytes = Buffer.from(audio!, 'base64');
      if (bytes.length > maximum) throw new HttpError(422, 'a voice clip is too large');
      const controller = new AbortController();
      const abort = () => controller.abort(new Error('Voice request cancelled'));
      const closed = () => {
        if (!reply.raw.writableFinished) abort();
      };
      request.raw.once('aborted', abort);
      reply.raw.once('close', closed);
      const signal = options.signal
        ? AbortSignal.any([options.signal, controller.signal])
        : controller.signal;
      try {
        return {
          transcript: await transcribe_conversation_audio(
            bytes,
            {
              base_url: services.config.voice_transcription_base_url,
              model: services.config.voice_transcription_model,
              api_key: services.config.voice_transcription_api_key,
              signal,
            },
            options.adapters,
          ),
        };
      } catch (error) {
        if (error instanceof VoiceTranscriptionUnconfigured)
          throw new HttpError(503, error.message);
        if (error instanceof VoiceTranscriptionFailed) throw new HttpError(502, error.message);
        throw error;
      } finally {
        request.raw.off('aborted', abort);
        reply.raw.off('close', closed);
      }
    });
  };
}
