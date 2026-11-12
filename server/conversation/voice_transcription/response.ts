/** HTTPX content decoding and json.loads(bytes) encoding selection. */
import { constants, gunzipSync, inflateRawSync, inflateSync } from 'node:zlib';

export function decode_voice_content(body: Uint8Array, content_encoding = ''): Uint8Array {
  let decoded: Uint8Array = body;
  for (const encoding of content_encoding
    .split(',')
    .map((value) => value.trim().toLowerCase())
    .reverse()) {
    if (encoding === 'gzip') decoded = gunzipSync(decoded, { finishFlush: constants.Z_SYNC_FLUSH });
    else if (encoding === 'deflate') {
      try {
        decoded = inflateSync(decoded, { finishFlush: constants.Z_SYNC_FLUSH });
      } catch {
        decoded = inflateRawSync(decoded, { finishFlush: constants.Z_SYNC_FLUSH });
      }
    }
    // The pinned Python HTTPX environment has no optional Brotli or Zstandard decoder.
    // HTTPX leaves unknown or unavailable content encodings unchanged.
  }
  return decoded;
}

function utf8(body: Uint8Array): string {
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  try {
    return decoder.decode(body);
  } catch {
    // Python json.loads uses surrogatepass. Preserve raw surrogate triples while
    // the strict decoder still refuses every other malformed UTF-8 sequence.
    const pieces: string[] = [];
    let start = 0;
    for (let offset = 0; offset + 2 < body.length; offset++) {
      const second = body[offset + 1]!;
      const third = body[offset + 2]!;
      if (body[offset] !== 0xed || second < 0xa0 || second > 0xbf || third < 0x80 || third > 0xbf)
        continue;
      pieces.push(decoder.decode(body.subarray(start, offset)));
      pieces.push(String.fromCharCode(0xd000 | ((second & 0x3f) << 6) | (third & 0x3f)));
      offset += 2;
      start = offset + 1;
    }
    pieces.push(decoder.decode(body.subarray(start)));
    return pieces.join('');
  }
}

function json_text(raw: Uint8Array): string {
  const bytes = Buffer.from(raw);
  let encoding: 'utf8' | 'utf16le' | 'utf16be' | 'utf32le' | 'utf32be' = 'utf8';
  let offset = 0;
  if (bytes.length >= 4 && bytes.readUInt32BE(0) === 0x0000feff) {
    encoding = 'utf32be';
    offset = 4;
  } else if (bytes.length >= 4 && bytes.readUInt32LE(0) === 0x0000feff) {
    encoding = 'utf32le';
    offset = 4;
  } else if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    encoding = 'utf16be';
    offset = 2;
  } else if (bytes[0] === 0xff && bytes[1] === 0xfe) {
    encoding = 'utf16le';
    offset = 2;
  } else if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) offset = 3;
  else if (bytes.length >= 4) {
    if (bytes[0] === 0) encoding = bytes[1] === 0 ? 'utf32be' : 'utf16be';
    else if (bytes[1] === 0) encoding = bytes[2] || bytes[3] ? 'utf16le' : 'utf32le';
  } else if (bytes.length === 2) {
    if (bytes[0] === 0) encoding = 'utf16be';
    else if (bytes[1] === 0) encoding = 'utf16le';
  }
  const body = bytes.subarray(offset);
  if (encoding === 'utf8') return utf8(body);
  if (encoding.startsWith('utf16')) {
    if (body.length % 2) throw new SyntaxError('Truncated UTF-16 JSON body');
    if (encoding === 'utf16le') return body.toString('utf16le');
    return Buffer.from(body).swap16().toString('utf16le');
  }
  if (body.length % 4) throw new SyntaxError('Truncated UTF-32 JSON body');
  const characters: string[] = [];
  for (let index = 0; index < body.length; index += 4) {
    const point = encoding === 'utf32le' ? body.readUInt32LE(index) : body.readUInt32BE(index);
    if (point > 0x10ffff) throw new SyntaxError('Invalid UTF-32 JSON code point');
    characters.push(String.fromCodePoint(point));
  }
  return characters.join('');
}

export function parse_voice_json(body: Uint8Array): unknown {
  try {
    return JSON.parse(json_text(body)) as unknown;
  } catch (error) {
    if (error instanceof SyntaxError) throw error;
    throw new SyntaxError('Invalid JSON byte encoding', { cause: error });
  }
}
