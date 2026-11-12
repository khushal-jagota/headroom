import { posix } from 'node:path';
export const MAX_CONVERSATION_MESSAGE_IMAGE_BYTES = 3 * 1024 * 1024;
export const MAX_CONVERSATION_IMAGE_BYTES = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES;
export const MAX_CONVERSATION_MESSAGE_FILE_BYTES = 10 * 1024 * 1024;
export function validated_image_media_type(payload: Buffer): string {
  if (payload.length > MAX_CONVERSATION_IMAGE_BYTES) {
    throw new Error('a conversation image is too large');
  }
  if (payload.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
    return 'image/png';
  if (payload.subarray(0, 3).equals(Buffer.from([255, 216, 255]))) return 'image/jpeg';
  if (['GIF87a', 'GIF89a'].includes(payload.subarray(0, 6).toString())) return 'image/gif';
  if (payload.subarray(0, 4).toString() === 'RIFF' && payload.subarray(8, 12).toString() === 'WEBP')
    return 'image/webp';
  throw new Error('unsupported or invalid conversation image');
}
const MEDIA_TYPES: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.csv': 'text/csv',
  '.tsv': 'text/tab-separated-values',
  '.json': 'application/json',
  '.jsonl': 'application/x-ndjson',
};
export function validated_file_media_type(payload: Buffer, file_name: string): string {
  if (!payload.length) throw new Error('a conversation file must not be empty');
  if (
    !file_name ||
    Array.from(file_name).length > 255 ||
    file_name !== file_name.trim() ||
    posix.basename(file_name) !== file_name ||
    file_name.includes('\\') ||
    /[\x00-\x1f\x7f]/.test(file_name)
  ) {
    throw new Error('a conversation file name must be a plain, trimmed file name');
  }
  const suffix = posix.extname(file_name).toLowerCase();
  const media_type = MEDIA_TYPES[suffix];
  if (!media_type) throw new Error('unsupported conversation file type');
  if (suffix === '.pdf') {
    if (
      !payload.subarray(0, 5).equals(Buffer.from('%PDF-')) ||
      !payload.subarray(-1024).includes(Buffer.from('%%EOF'))
    ) {
      throw new Error('unsupported or invalid conversation PDF');
    }
    return media_type;
  }
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(payload);
  } catch {
    throw new Error('a conversation text or data file must be UTF-8');
  }
  if (text.includes('\0'))
    throw new Error('a conversation text or data file must not contain NUL bytes');
  if (suffix === '.json') {
    try {
      python_json_admission(text);
    } catch {
      throw new Error('a conversation JSON file must contain valid JSON');
    }
  }
  if (suffix === '.jsonl') {
    let count = 0;
    for (const [index, line] of text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/u).entries()) {
      if (!line.trim()) continue;
      count++;
      try {
        python_json_admission(line);
      } catch {
        throw new Error(`a conversation JSONL file has invalid JSON on line ${index + 1}`);
      }
    }
    if (!count) throw new Error('a conversation JSONL file must contain a JSON value');
  }
  return media_type;
}
/** Python json.loads admits its three non-finite number literals. Only syntax matters here. */
function python_json_admission(text: string): void {
  const admitted = text.replace(/"(?:\\.|[^"\\])*"|-Infinity|Infinity|NaN/g, (token) =>
    token.startsWith('"') ? token : 'null',
  );
  JSON.parse(admitted);
}
