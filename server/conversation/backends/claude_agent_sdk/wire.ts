import { is_record as object } from '../../../core/records.ts';
import { StringDecoder } from 'node:string_decoder';
import { Transform } from 'node:stream';
import { PromptWriteFailed } from '../contracts.ts';
import { CLAUDE_SDK_MAX_BUFFER_SIZE } from './configuration.ts';

export class Pending<T> {
  readonly promise: Promise<T>;
  settled = false;
  value: T | undefined;
  private resolve_value!: (value: T) => void;
  private reject_value!: (cause: unknown) => void;
  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.resolve_value = resolve;
      this.reject_value = reject;
    });
    // Producers can fail before their owner begins to await the result.
    void this.promise.catch(() => {});
  }
  resolve(value: T): void {
    if (this.settled) return;
    this.settled = true;
    this.value = value;
    this.resolve_value(value);
  }
  reject(cause: unknown): void {
    if (this.settled) return;
    this.settled = true;
    this.reject_value(cause);
  }
}

export interface CommandLifecycle {
  type: 'command_lifecycle';
  command_uuid: string;
  state: string;
}
export function raw_uuid_set(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [],
  );
}
export function cancellation_uuid_set(value: unknown): Set<string> {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new PromptWriteFailed('claude did not confirm queued command cancellation');
  }
  return new Set(value as string[]);
}
export function raw_user_message_uuids(message: Record<string, unknown>): Set<string> {
  const values = raw_uuid_set(message.user_message_uuids);
  if (typeof message.user_message_uuid === 'string') values.add(message.user_message_uuid);
  return values;
}

/** Match Python's per-line Unicode-character bound before the SDK consumes stdout. */
export class ObservedStdout extends Transform {
  private readonly decoder = new StringDecoder('utf8');
  private pending = '';
  private length = 0;
  private readonly observe: (frame: Record<string, unknown>) => void;
  constructor(observe: (frame: Record<string, unknown>) => void) {
    super();
    this.observe = observe;
  }
  private take_text(text: string): void {
    const pieces = text.split('\n');
    for (let index = 0; index < pieces.length; index += 1) {
      const piece = pieces[index]!;
      this.pending += piece;
      for (const _character of piece) this.length += 1;
      if (this.length > CLAUDE_SDK_MAX_BUFFER_SIZE) {
        throw new Error(
          `Failed to decode JSON: JSON message exceeded maximum buffer size of ${CLAUDE_SDK_MAX_BUFFER_SIZE} bytes...`,
        );
      }
      if (index < pieces.length - 1) {
        const line = this.pending;
        this.pending = '';
        this.length = 0;
        if (this.frame(line, false)) this.push(Buffer.from(line + '\n', 'utf8'));
      }
    }
  }
  private frame(line: string, final_tail: boolean): boolean {
    const stripped = line.trim();
    if (!stripped.startsWith('{')) return false;
    let value: unknown;
    try {
      value = JSON.parse(stripped);
    } catch {
      if (final_tail) return false;
      throw new Error(`Failed to decode JSON: ${[...stripped].slice(0, 100).join('')}...`);
    }
    if (object(value)) {
      this.observe(value);
    }
    return true;
  }
  override _transform(
    chunk: Buffer,
    _encoding: BufferEncoding,
    callback: (error?: Error | null) => void,
  ): void {
    try {
      this.take_text(this.decoder.write(chunk));
      callback();
    } catch (cause) {
      callback(cause instanceof Error ? cause : new Error(String(cause)));
    }
  }
  override _flush(callback: (error?: Error | null) => void): void {
    try {
      this.take_text(this.decoder.end());
      if (this.pending && this.frame(this.pending, true))
        this.push(Buffer.from(this.pending, 'utf8'));
      callback();
    } catch (cause) {
      callback(cause instanceof Error ? cause : new Error(String(cause)));
    }
  }
}

/** Python's stderr callback receives nonblank right-trimmed lines, not chunks. */
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
