/** The move routes of the old server and of the held new server, over one session each. */
import type { IncomingMessage } from 'node:http';
import type {
  ServerMoveCommitReply,
  ServerMoveFiles,
  ServerMoveInstruction,
  ServerMoveProbeJob,
  ServerMoveReport,
  ServerMoveReportReply,
} from '../../../server_move/contracts.ts';
import { read_server_location_probe, read_bounded } from '../transport/sessions.ts';
import type { HttpSessions } from '../transport/sessions.ts';
import { http_error } from '../transport/errors.ts';
import { SERVER_LOCATION_PATH } from '../../../server_link/contracts.ts';
import { HTTP_CONNECT_TIMEOUT_SECONDS } from '../configuration.ts';
import {
  MAX_SERVER_MOVE_FILE_LIST_BYTES,
  MAX_SERVER_MOVE_REPLY_BYTES,
  SERVER_MOVE_PAUSE_REQUEST_SECONDS,
  SERVER_MOVE_REPORT_REQUEST_SECONDS,
  SERVER_MOVE_REQUEST_SECONDS,
} from './configuration.ts';
import {
  parse_commit_reply,
  parse_files,
  parse_instruction,
  parse_probe_job,
  parse_report_reply,
} from './logic.ts';
import { ServerMoveFailure } from './contracts.ts';

const ROUTE = '/api/executor/server-move';
export const SERVER_FILE_SHA256_HEADER = 'x-panels-file-sha256';

function decode(bytes: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new ServerMoveFailure('A server sent an answer that is not JSON.');
  }
}
export class ServerMoveRequests {
  private readonly session: HttpSessions;
  private readonly headers: () => Readonly<Record<string, string>>;
  private readonly signal: AbortSignal;
  constructor(
    session: HttpSessions,
    headers: () => Readonly<Record<string, string>>,
    signal: AbortSignal,
  ) {
    this.session = session;
    this.headers = headers;
    this.signal = signal;
  }
  /** One JSON exchange. A 404 answers null when `absent` allows it. */
  private async json(
    method: 'GET' | 'POST',
    path: string,
    options: { body?: unknown; seconds?: number; limit?: number; absent?: boolean } = {},
  ): Promise<unknown> {
    const body = options.body === undefined ? undefined : Buffer.from(JSON.stringify(options.body));
    return this.session.request(
      {
        loop: 'lease',
        method,
        path,
        body,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        read_timeout: options.seconds ?? SERVER_MOVE_REQUEST_SECONDS,
        signal: this.signal,
      },
      { ...this.headers(), 'Accept-Encoding': 'identity' },
      async (response) => {
        const bytes = await read_bounded(response, options.limit ?? MAX_SERVER_MOVE_REPLY_BYTES);
        if (response.statusCode === 404 && options.absent) return null;
        if (response.statusCode !== 200) throw http_error(response.statusCode ?? 0, bytes);
        return decode(bytes);
      },
    );
  }
  /** The run for this machine, or null when the server has none. */
  async instruction(): Promise<ServerMoveInstruction | null> {
    const value = await this.json('GET', ROUTE, { absent: true });
    return value === null ? null : parse_instruction(value);
  }
  async files(move_id: string): Promise<ServerMoveFiles> {
    return parse_files(
      await this.json('GET', `${ROUTE}/${move_id}/files`, {
        limit: MAX_SERVER_MOVE_FILE_LIST_BYTES,
      }),
      false,
    );
  }
  async pause(move_id: string): Promise<ServerMoveFiles> {
    return parse_files(
      await this.json('POST', `${ROUTE}/${move_id}/pause`, {
        body: {},
        seconds: SERVER_MOVE_PAUSE_REQUEST_SECONDS,
        limit: MAX_SERVER_MOVE_FILE_LIST_BYTES,
      }),
      true,
    );
  }
  async report(move_id: string, report: ServerMoveReport): Promise<ServerMoveReportReply> {
    return parse_report_reply(
      await this.json('POST', `${ROUTE}/${move_id}/report`, {
        body: report,
        seconds: SERVER_MOVE_REPORT_REQUEST_SECONDS,
      }),
    );
  }
  async switched(move_id: string): Promise<void> {
    await this.json('POST', `${ROUTE}/${move_id}/switched`, { body: {} });
  }
  async probe_job(): Promise<ServerMoveProbeJob> {
    return parse_probe_job(await this.json('POST', `${ROUTE}/held/probe`, { body: {} }));
  }
  async answer_probe_job(job: ServerMoveProbeJob): Promise<void> {
    const value = await this.json('POST', `${ROUTE}/held/probe/${job.job_id}`, {
      body: { nonce: job.nonce },
    });
    if ((value as { answered?: unknown } | null)?.answered !== true)
      throw new ServerMoveFailure('The new server did not accept the test job answer.');
  }
  async commit(move_id: string): Promise<ServerMoveCommitReply> {
    return parse_commit_reply(
      await this.json('POST', `${ROUTE}/held/commit`, { body: { move_id } }),
    );
  }
  /** The server identity that this session's address answers. */
  location(): Promise<unknown> {
    return this.session.request(
      {
        loop: 'lease',
        method: 'GET',
        path: SERVER_LOCATION_PATH,
        timeout: HTTP_CONNECT_TIMEOUT_SECONDS,
        signal: this.signal,
      },
      { 'Accept-Encoding': 'identity' },
      read_server_location_probe,
    );
  }
  /** Streams one download. `consume` receives a 200 answer without transport encoding. */
  download<T>(path: string, consume: (response: IncomingMessage) => Promise<T>): Promise<T> {
    return this.session.request(
      {
        loop: 'lease',
        method: 'GET',
        path,
        headers: { 'Accept-Encoding': 'identity' },
        read_timeout: SERVER_MOVE_REQUEST_SECONDS,
        signal: this.signal,
      },
      this.headers(),
      async (response) => {
        if (response.statusCode !== 200)
          throw http_error(
            response.statusCode ?? 0,
            await read_bounded(response, MAX_SERVER_MOVE_REPLY_BYTES),
          );
        if (response.headers['content-encoding'])
          throw new ServerMoveFailure('A move download used a transport encoding.');
        return consume(response);
      },
    );
  }
  package_path(move_id: string): string {
    return `${ROUTE}/${move_id}/package`;
  }
  file_path(move_id: string, relative_path: string): string {
    return `${ROUTE}/${move_id}/file?${new URLSearchParams({ path: relative_path })}`;
  }
}
