import { StringDecoder } from 'node:string_decoder';
import { CLAUDE_SDK_MAX_BUFFER_SIZE } from './configuration.ts';
import { Deferred } from '../../../core/deferred.ts';

export function raw_uuid_set(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [],
  );
}
export function raw_user_message_uuids(message: Record<string, unknown>): Set<string> {
  const values = raw_uuid_set(message.user_message_uuids);
  if (typeof message.user_message_uuid === 'string') values.add(message.user_message_uuid);
  return values;
}

/** Deliver nonblank, right-trimmed stderr lines. */
export class ObservedStderr {
  private readonly decoder = new StringDecoder('utf8');
  private pending = '';
  private length = 0;
  private ended = false;
  private readonly callback: (line: string) => void;
  constructor(callback: (line: string) => void) {
    this.callback = callback;
  }
  private emit(line: string): void {
    const stripped = line.trimEnd();
    if (!stripped) return;
    try {
      this.callback(stripped);
    } catch {
      /* A failed callback cannot stop later diagnostics. */
    }
  }
  private take_text(text: string): void {
    const pieces = text.split('\n');
    for (let index = 0; index < pieces.length; index += 1) {
      const piece = pieces[index]!;
      this.pending += piece;
      for (const _character of piece) this.length += 1;
      if (index < pieces.length - 1) {
        this.emit(this.pending);
        this.pending = '';
        this.length = 0;
      }
    }
    if (this.length > CLAUDE_SDK_MAX_BUFFER_SIZE) {
      this.emit(this.pending);
      this.pending = '';
      this.length = 0;
    }
  }
  write(chunk: Buffer): void {
    if (!this.ended) this.take_text(this.decoder.write(chunk));
  }
  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.take_text(this.decoder.end());
    this.emit(this.pending);
    this.pending = '';
    this.length = 0;
  }
}

export { is_record as object } from '../../../core/records.ts';
