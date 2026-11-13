/** Bounded manifest decoding; broader Python byte encodings remain a proposed restriction. */
import { AppValidationError, MacArchiveJsonError } from '../../release/contracts.ts';
import { parse_attempt_json } from '../codec.ts';
export function decode_mac_manifest(bytes: Buffer): unknown {
  // json.detect_encoding checks UTF32 before UTF16, then BOM-less NUL positions.
  if (
    bytes.subarray(0, 4).equals(Buffer.from([0, 0, 0xfe, 0xff])) ||
    bytes.subarray(0, 2).equals(Buffer.from([0xff, 0xfe])) ||
    bytes.subarray(0, 2).equals(Buffer.from([0xfe, 0xff])) ||
    (bytes.length >= 4 && (bytes[0] === 0 || bytes[1] === 0)) ||
    (bytes.length === 2 && (bytes[0] === 0 || bytes[1] === 0))
  )
    throw new AppValidationError('Mac manifest encoding is unsupported');
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new AppValidationError('Mac manifest encoding is unsupported');
  }
  try {
    return parse_attempt_json(text);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new MacArchiveJsonError(error);
  }
}
