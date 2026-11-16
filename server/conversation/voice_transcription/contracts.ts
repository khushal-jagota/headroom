/** The provider boundary is the OpenAI-compatible audio transcription operation. */
export interface VoiceTranscriptionConfiguration {
  readonly base_url: string;
  readonly model: string;
  readonly api_key: string | null;
  readonly signal?: AbortSignal;
}
export interface VoiceTranscriptionRequest {
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly file: {
    readonly filename: string;
    readonly contents: Uint8Array;
    readonly media_type: string;
  };
  readonly data: Readonly<Record<string, string>>;
  readonly timeout_seconds: number;
  readonly signal?: AbortSignal;
}
export interface VoiceTranscriptionResponse {
  readonly status_code: number;
  json(): unknown | Promise<unknown>;
}
export interface VoiceTranscriptionAdapters {
  trim_surrounding_silence(
    audio: Uint8Array,
    options?: { signal?: AbortSignal },
  ): Promise<readonly [Uint8Array, boolean]>;
  http_post(request: VoiceTranscriptionRequest): Promise<VoiceTranscriptionResponse>;
}
export class VoiceTranscriptionUnconfigured extends Error {}
export class VoiceTranscriptionFailed extends Error {}
export class VoiceTranscriptionTransportError extends Error {}
