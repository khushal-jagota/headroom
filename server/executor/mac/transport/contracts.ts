/** HTTP custody ports. Wire values remain with the generated shared contract. */
import type { ConnectionOptions } from 'node:tls';
import type { IncomingMessage } from 'node:http';
import type { MacHttpConnectorSettings } from '../configuration.ts';

export type MacLoop = 'lease' | 'poll' | 'outcomes' | 'items' | 'downloads' | 'reads' | 'live';
export interface HttpRequest {
  readonly loop: MacLoop;
  readonly method: string;
  readonly path: string;
  readonly timeout?: number;
  readonly read_timeout?: number;
  readonly stall_timeout?: number;
  readonly headers?: Readonly<Record<string, string>>;
  readonly body?: Uint8Array | AsyncIterable<Uint8Array>;
  readonly byte_count?: number;
  readonly signal?: AbortSignal;
}
export interface MacHttpOptions {
  readonly tls?: Pick<ConnectionOptions, 'ca' | 'cert' | 'key' | 'servername'>;
}
export type ReplyReader<T> = (response: IncomingMessage) => Promise<T>;
export type ConnectorTable = Readonly<Record<MacLoop, MacHttpConnectorSettings>>;
