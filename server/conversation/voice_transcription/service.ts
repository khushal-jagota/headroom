/** Audio stays browser-owned. A successful transcription returns only its words. */
import type { VoiceTranscriptionAdapters, VoiceTranscriptionConfiguration } from './contracts.ts';
import {
  VoiceTranscriptionFailed,
  VoiceTranscriptionTransportError,
  VoiceTranscriptionUnconfigured,
} from './contracts.ts';
import { VOICE_TRANSCRIPTION_TIMEOUT_SECONDS } from './configuration.ts';
import { without_whisper_hallucination } from './logic.ts';
import { production_voice_transcription_adapters } from './production.ts';
export { without_whisper_hallucination } from './logic.ts';
export async function transcribe_conversation_audio(
  audio: Uint8Array,
  configuration: VoiceTranscriptionConfiguration,
  adapters: VoiceTranscriptionAdapters = production_voice_transcription_adapters,
): Promise<string> {
  configuration.signal?.throwIfAborted();
  if (configuration.api_key === null)
    throw new VoiceTranscriptionUnconfigured(
      'voice transcription needs PLAN_GROQ_API_KEY, which is not set',
    );
  const [trimmed, silent] = await adapters.trim_surrounding_silence(audio, {
    signal: configuration.signal,
  });
  configuration.signal?.throwIfAborted();
  if (silent) return '';
  let response;
  try {
    response = await adapters.http_post({
      url: configuration.base_url.replace(/\/+$/, '') + '/audio/transcriptions',
      headers: { Authorization: `Bearer ${configuration.api_key}` },
      file: { filename: 'audio.ogg', contents: trimmed, media_type: 'audio/ogg' },
      data: { model: configuration.model, response_format: 'json' },
      timeout_seconds: VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
      signal: configuration.signal,
    });
  } catch (error) {
    configuration.signal?.throwIfAborted();
    if (!(error instanceof VoiceTranscriptionTransportError)) throw error;
    throw new VoiceTranscriptionFailed('the transcription provider could not be reached', {
      cause: error,
    });
  }
  configuration.signal?.throwIfAborted();
  if (response.status_code < 200 || response.status_code >= 300)
    throw new VoiceTranscriptionFailed(
      `the transcription provider answered ${response.status_code}`,
    );
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    configuration.signal?.throwIfAborted();
    if (!(error instanceof SyntaxError)) throw error;
    throw new VoiceTranscriptionFailed(
      'the transcription provider answered something that is not JSON',
      { cause: error },
    );
  }
  configuration.signal?.throwIfAborted();
  const text =
    body !== null && typeof body === 'object' && !Array.isArray(body)
      ? (body as Record<string, unknown>).text
      : undefined;
  if (typeof text !== 'string')
    throw new VoiceTranscriptionFailed("the transcription provider's answer carries no transcript");
  return without_whisper_hallucination(text);
}
export const transcribe_voice = transcribe_conversation_audio;
