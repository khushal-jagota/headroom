/** Match HTTPX's data-before-files multipart encoding. */
import { randomBytes } from 'node:crypto';
import type { VoiceTranscriptionRequest } from './contracts.ts';
function parameter(value: string): string {
  return value.replace(/["\\\x00-\x1a\x1c-\x1f]/g, (character) => {
    if (character === '"') return '%22';
    if (character === '\\') return '\\\\';
    return '%' + character.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0');
  });
}
export function voice_multipart(
  request: Pick<VoiceTranscriptionRequest, 'data' | 'file'>,
  boundary = randomBytes(16).toString('hex'),
): { parts: readonly Uint8Array[]; content_type: string; content_length: number } {
  const parts: Uint8Array[] = [];
  for (const [key, value] of Object.entries(request.data))
    parts.push(
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="${parameter(key)}"\r\n\r\n${value}\r\n`,
      ),
    );
  const filename = request.file.filename ? `; filename="${parameter(request.file.filename)}"` : '';
  parts.push(
    Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"${filename}\r\n` +
        `Content-Type: ${request.file.media_type}\r\n\r\n`,
    ),
  );
  parts.push(request.file.contents);
  parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));
  return {
    parts,
    content_type: `multipart/form-data; boundary=${boundary}`,
    content_length: parts.reduce((total, part) => total + part.byteLength, 0),
  };
}
