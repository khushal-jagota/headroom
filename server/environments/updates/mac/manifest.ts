/** Decodes the Mac runtime manifest as strict UTF-8 JSON. */
import { AppValidationError, MacArchiveJsonError } from '../../release/contracts.ts';
export function decode_mac_manifest(bytes: Buffer): unknown {
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new AppValidationError('Mac manifest encoding is unsupported');
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new MacArchiveJsonError(error);
  }
}
