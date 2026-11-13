import { EFFECTIVELY_SILENT_BYTES, WHISPER_HALLUCINATION_PHRASES } from './configuration.ts';
const SURROUNDING_PUNCTUATION = new Set(Array.from(' \t\n\r.,!?…"\'“”‘’-—'));
export function without_whisper_hallucination(transcript: string): string {
  const stripped = transcript.trim();
  const characters = Array.from(stripped);
  let start = 0;
  let end = characters.length;
  while (start < end && SURROUNDING_PUNCTUATION.has(characters[start]!)) start += 1;
  while (end > start && SURROUNDING_PUNCTUATION.has(characters[end - 1]!)) end -= 1;
  const comparable = characters.slice(start, end).join('').toLowerCase();
  return !comparable || WHISPER_HALLUCINATION_PHRASES.has(comparable) ? '' : stripped;
}
/** An unsuccessful trim preserves the original clip and never declares silence. */
export function silence_trim_result(
  audio: Uint8Array,
  result: { exit_code: number; contents: Uint8Array } | null,
): readonly [Uint8Array, boolean] {
  if (result === null || result.exit_code !== 0 || !result.contents.length) return [audio, false];
  return [result.contents, result.contents.length < EFFECTIVELY_SILENT_BYTES];
}
