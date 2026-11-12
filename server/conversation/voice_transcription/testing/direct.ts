/** Export-only proof: every audio and provider answer comes from synthetic input. */
import { transcribe_conversation_audio } from '../service.ts';
import { VoiceTranscriptionTransportError } from '../contracts.ts';
import { silence_trim_result, without_whisper_hallucination } from '../logic.ts';
interface VoiceCase {
  readonly name: string;
  readonly api_key: string | null;
  readonly trim_hex: string;
  readonly silent: boolean;
  readonly status_code: number;
  readonly response_text: string;
  readonly transport_error?: boolean;
}
interface TrimCase {
  readonly name: string;
  readonly exit_code: number;
  readonly contents_hex: string;
  readonly unavailable: boolean;
}
interface Spec {
  readonly audio_hex: string;
  readonly phrases: readonly string[];
  readonly transcriptions: readonly VoiceCase[];
  readonly trim_results: readonly TrimCase[];
}
export async function compare_voice(spec: Spec): Promise<unknown> {
  const audio = Buffer.from(spec.audio_hex, 'hex');
  const transcriptions = [];
  for (const scenario of spec.transcriptions) {
    const calls: unknown[] = [];
    let answer: unknown;
    try {
      answer = {
        transcript: await transcribe_conversation_audio(
          audio,
          {
            base_url: 'https://voice.invalid/openai/v1///',
            model: 'recorded-whisper',
            api_key: scenario.api_key,
          },
          {
            async trim_surrounding_silence(contents) {
              calls.push({ operation: 'trim', audio_hex: Buffer.from(contents).toString('hex') });
              return [Buffer.from(scenario.trim_hex, 'hex'), scenario.silent];
            },
            async http_post(request) {
              calls.push({
                operation: 'http',
                url: request.url,
                headers: request.headers,
                file: {
                  filename: request.file.filename,
                  contents_hex: Buffer.from(request.file.contents).toString('hex'),
                  media_type: request.file.media_type,
                },
                data: request.data,
                timeout_seconds: request.timeout_seconds,
              });
              if (scenario.transport_error)
                throw new VoiceTranscriptionTransportError('Synthetic HTTP failure');
              return {
                status_code: scenario.status_code,
                json: () => JSON.parse(scenario.response_text) as unknown,
              };
            },
          },
        ),
      };
    } catch (error) {
      answer = {
        error: error instanceof Error ? error.constructor.name : 'unknown',
        detail: error instanceof Error ? error.message : String(error),
      };
    }
    transcriptions.push({ name: scenario.name, answer, calls });
  }
  return {
    phrases: spec.phrases.map(without_whisper_hallucination),
    transcriptions,
    trim_results: spec.trim_results.map((scenario) => {
      const [contents, silent] = silence_trim_result(
        audio,
        scenario.unavailable
          ? null
          : {
              exit_code: scenario.exit_code,
              contents: Buffer.from(scenario.contents_hex, 'hex'),
            },
      );
      return { name: scenario.name, contents_hex: Buffer.from(contents).toString('hex'), silent };
    }),
  };
}
