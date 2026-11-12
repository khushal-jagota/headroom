export const VOICE_TRANSCRIPTION_TIMEOUT_SECONDS = 60;
export const SILENCE_TRIM_TIMEOUT_SECONDS = 30;
export const FFMPEG_PATH = '/usr/bin/ffmpeg';
export const EFFECTIVELY_SILENT_BYTES = 1024;
export const SILENCE_TRIM_FILTER =
  'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.3,' +
  'areverse,' +
  'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.3,' +
  'areverse';
export const WHISPER_HALLUCINATION_PHRASES: ReadonlySet<string> = new Set([
  'thank you',
  'thank you for watching',
  'thanks for watching',
  'subscribe',
  'please subscribe',
  'bye',
  'you',
  'the end',
  'so',
]);
