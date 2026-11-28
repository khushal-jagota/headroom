/** The live-updates WebSocket: admission, one session for each socket, and its close record. */
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import type { FastifyInstance } from 'fastify';
import { WebSocketServer, type RawData, type WebSocket } from 'ws';
import type { Config } from '../core/config.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { is_executor_link_connection } from '../core/http/executor_link.ts';
import { raw_header_map, trusted_ingress_login } from '../core/http/middleware.ts';
import { ClientLiveStreams } from '../core/http/live_streams/index.ts';
import {
  report_stream_close,
  type StreamCloseRecord,
  type StreamCloseSink,
  type StreamClosedBy,
} from '../core/http/live_streams/records.ts';
import { remember_client_version } from '../client_versions/data.ts';
import type { ClientIdentity } from '../client_versions/contracts.ts';
import { parse_client_identity } from '../client_versions/identity.ts';
import { client_update_required } from '../client_versions/policy.ts';
import { coalesced_change_pings, type ChangePings } from './change_pings.ts';
import {
  LIVE_BACKPRESSURE_CLOSE_CODE,
  LIVE_CLOSE_CODES,
  LIVE_FIRST_FRAME_MS,
  LIVE_HEARTBEAT_MS,
  LIVE_MAX_BUFFERED_BYTES,
  LIVE_MAX_CLIENT_FRAME_BYTES,
  LIVE_PROTOCOL_CLOSE_CODE,
  LIVE_SOCKET_PATH,
  type LiveClientFrame,
  type LiveSink,
  type LiveSubscriptionHandle,
} from './contracts.ts';
import type { LiveSubscriptions } from './registry.ts';

export interface LiveSocketServices {
  config: Config;
  database: Database;
  clock: Clock;
  changes: { subscribe(callback: () => void): () => void };
  subscriptions: LiveSubscriptions;
  stream_close_sink?: StreamCloseSink;
}

interface Subscription {
  handle?: LiveSubscriptionHandle;
  /** Set once the subscription ended, so that no frame follows and the handle closes once. */
  ended: boolean;
}

function failure_of(error: unknown): { status: number; detail: unknown } {
  if (error instanceof HttpError) return { status: error.statusCode, detail: error.detail };
  if (error instanceof PlannerError) return { status: error.statusCode, detail: error.toPayload() };
  console.error('live subscription failed to open', error);
  return { status: 500, detail: 'internal error' };
}

class LiveSession {
  readonly #socket: WebSocket;
  readonly #services: LiveSocketServices;
  readonly #counter: ClientLiveStreams;
  readonly #sessions: Set<LiveSession>;
  readonly #subscriptions = new Map<string, Subscription>();
  readonly #opened_at = Date.now();
  #client: ClientIdentity | null = null;
  #pings: ChangePings | undefined;
  #release: (() => void) | undefined;
  #heartbeat: ReturnType<typeof setInterval> | undefined;
  readonly #first_frame: ReturnType<typeof setTimeout>;
  #alive = true;
  #sequence = 0;
  #bytes = 0;
  #last_write_at = this.#opened_at;
  #failure: string | null = null;
  #shutting_down = false;
  #closed_by_server = false;
  /** The code that the server sent. The peer can vanish before the handshake ends, and ws reports 1006. */
  #sent_code: number | null = null;

  constructor(
    socket: WebSocket,
    services: LiveSocketServices,
    counter: ClientLiveStreams,
    sessions: Set<LiveSession>,
  ) {
    this.#socket = socket;
    this.#services = services;
    this.#counter = counter;
    this.#sessions = sessions;
    sessions.add(this);
    this.#first_frame = setTimeout(
      () => this.#close(LIVE_CLOSE_CODES.bad_first_frame, 'bad first frame'),
      LIVE_FIRST_FRAME_MS,
    );
    this.#first_frame.unref();
    socket.on('message', (data, binary) => {
      try {
        this.#receive(data, binary);
      } catch (error) {
        console.error('live socket frame failed', error);
        this.#close(LIVE_PROTOCOL_CLOSE_CODE, 'frame failed');
      }
    });
    socket.on('pong', () => {
      this.#alive = true;
    });
    socket.on('error', (error: Error & { code?: string }) => {
      this.#failure ??= error.code ?? error.name;
    });
    socket.on('close', (code) => this.#closed(code));
  }

  /** The server is stopping. The client reconnects. */
  shut_down(): void {
    this.#shutting_down = true;
    this.#close(LIVE_CLOSE_CODES.server_restarting, 'server restarting');
  }

  #close(code: number, reason: string): void {
    if (this.#socket.readyState !== this.#socket.OPEN) return;
    this.#closed_by_server = true;
    this.#sent_code = code;
    this.#socket.close(code, reason);
    // A client that never answers the close handshake must not hold the server open.
    setTimeout(() => this.#socket.terminate(), 2000).unref();
  }

  #send(sub: string | null, kind: string, data?: string): void {
    if (this.#socket.readyState !== this.#socket.OPEN) return;
    if (this.#socket.bufferedAmount > LIVE_MAX_BUFFERED_BYTES) {
      this.#close(LIVE_BACKPRESSURE_CLOSE_CODE, 'client too slow');
      return;
    }
    this.#sequence += 1;
    const text =
      `{"seq":${this.#sequence},"sub":${sub === null ? 'null' : JSON.stringify(sub)},` +
      `"kind":${JSON.stringify(kind)}${data === undefined ? '' : `,"data":${data}`}}`;
    this.#bytes += Buffer.byteLength(text);
    this.#last_write_at = Date.now();
    this.#socket.send(text);
  }

  #receive(data: RawData, binary: boolean): void {
    let frame: LiveClientFrame | null = null;
    try {
      if (!binary) frame = JSON.parse((data as Buffer).toString('utf8')) as LiveClientFrame;
    } catch {
      // Unreadable text counts as a bad frame below.
    }
    if (this.#client === null) return this.#admit(frame);
    if (frame?.op === 'subscribe') return void this.#subscribe(frame);
    if (frame?.op === 'unsubscribe' && typeof frame.id === 'string')
      return this.#unsubscribe(frame.id);
    this.#close(LIVE_PROTOCOL_CLOSE_CODE, 'unknown frame');
  }

  /** The first frame declares the client. The version gate is the same as for requests. */
  #admit(frame: LiveClientFrame | null): void {
    let client: ClientIdentity | null = null;
    try {
      if (frame?.op === 'hello' && typeof frame.client === 'object')
        client = parse_client_identity([JSON.stringify(frame.client)]);
    } catch {
      // An invalid identity is a bad first frame.
    }
    if (client === null) return this.#close(LIVE_CLOSE_CODES.bad_first_frame, 'bad first frame');
    clearTimeout(this.#first_frame);
    this.#client = client;
    remember_client_version(this.#services.database, client, this.#services.clock.nowUnix());
    if (client_update_required(client))
      return this.#close(LIVE_CLOSE_CODES.update_needed, 'update needed');
    this.#release = this.#counter.open(client, LIVE_SOCKET_PATH);
    this.#send(null, 'ready', JSON.stringify({ heartbeat_ms: LIVE_HEARTBEAT_MS }));
    this.#pings = coalesced_change_pings(this.#services.changes, () => {
      this.#send(null, 'change');
      return true;
    });
    this.#heartbeat = setInterval(() => {
      // A client that answered no ping since the last one is dead.
      if (!this.#alive) return this.#socket.terminate();
      this.#alive = false;
      this.#socket.ping();
      this.#send(null, 'heartbeat', JSON.stringify(new Date().toISOString()));
    }, LIVE_HEARTBEAT_MS);
    this.#heartbeat.unref();
  }

  #end(id: string, subscription: Subscription, data: unknown): void {
    if (subscription.ended) return;
    subscription.ended = true;
    if (this.#subscriptions.get(id) === subscription) this.#subscriptions.delete(id);
    this.#send(id, 'ended', JSON.stringify(data));
  }

  async #subscribe(frame: Extract<LiveClientFrame, { op: 'subscribe' }>): Promise<void> {
    const { id } = frame;
    const subscription: Subscription = { ended: false };
    const refuse = (status: number, detail: string) =>
      this.#send(typeof id === 'string' ? id : null, 'ended', JSON.stringify({ status, detail }));
    if (typeof id !== 'string' || !id || id.length > 128)
      return refuse(422, 'subscription id is required');
    if (this.#subscriptions.has(id)) return refuse(409, 'subscription id is already in use');
    const opener = this.#services.subscriptions.get(frame.kind);
    if (!opener) return refuse(422, 'unknown subscription kind');
    const params = frame.params ?? {};
    if (
      typeof params !== 'object' ||
      params === null ||
      Object.values(params).some((value) => typeof value !== 'string') ||
      (frame.conversation_id !== undefined && typeof frame.conversation_id !== 'string')
    )
      return refuse(422, 'subscription parameters must be strings');
    this.#subscriptions.set(id, subscription);
    const sink: LiveSink = {
      write: (next) => {
        if (next && !subscription.ended) this.#send(id, next.event, next.data);
        return true;
      },
      drain: async () => {},
      end: () => this.#end(id, subscription, { reason: 'closed' }),
      // The session closes the handle when the subscription or the socket ends.
      on_closed: () => {},
    };
    try {
      subscription.handle = await opener(
        { id, conversation_id: frame.conversation_id ?? null, params },
        sink,
      );
    } catch (error) {
      this.#end(id, subscription, failure_of(error));
      return;
    }
    // An unsubscribe or a close can arrive while the opener ran.
    if (subscription.ended) subscription.handle.close();
  }

  #unsubscribe(id: string): void {
    const subscription = this.#subscriptions.get(id);
    if (!subscription) return;
    subscription.ended = true;
    this.#subscriptions.delete(id);
    subscription.handle?.close();
  }

  #closed(code: number): void {
    clearTimeout(this.#first_frame);
    clearInterval(this.#heartbeat);
    this.#pings?.stop();
    this.#release?.();
    this.#sessions.delete(this);
    for (const subscription of this.#subscriptions.values()) {
      subscription.ended = true;
      subscription.handle?.close();
    }
    this.#subscriptions.clear();
    const closed_at = Date.now();
    const closed_by: StreamClosedBy =
      this.#failure !== null && this.#failure !== 'ECONNRESET'
        ? 'write_error'
        : this.#shutting_down
          ? 'server_pre_close'
          : this.#closed_by_server
            ? 'server_ended'
            : 'peer';
    const record: StreamCloseRecord = {
      route: LIVE_SOCKET_PATH,
      client_kind: this.#client?.kind ?? 'unknown',
      device_id: this.#client?.device_id ?? null,
      opened_at: this.#opened_at,
      closed_at,
      age_ms: closed_at - this.#opened_at,
      bytes_written: this.#bytes,
      closed_by,
      last_write_ago_ms: closed_at - this.#last_write_at,
      error_code: this.#failure,
      close_code: this.#sent_code ?? code,
      last_sequence: this.#sequence,
    };
    report_stream_close(record, this.#services.stream_close_sink);
  }
}

const FORBIDDEN = 'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n';

/**
 * Accepts upgrades at `LIVE_SOCKET_PATH`. The Tailscale login at the upgrade is the gate, and a
 * present Origin must be the web origin or `panels://app`. A socket with no Origin is a native
 * client. Raw upgrades skip the request hooks, so this runs the same login check itself.
 */
export function install_live_socket(app: FastifyInstance, services: LiveSocketServices): void {
  const server = new WebSocketServer({ noServer: true, maxPayload: LIVE_MAX_CLIENT_FRAME_BYTES });
  const sessions = new Set<LiveSession>();
  const counter = new ClientLiveStreams();
  let stopping = false;
  const upgrade = (request: IncomingMessage, socket: Duplex, head: Buffer): void => {
    if ((request.url ?? '').split('?')[0] !== LIVE_SOCKET_PATH) return;
    if (stopping) {
      socket.end(
        'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n',
      );
      return;
    }
    try {
      if (is_executor_link_connection(request.socket))
        throw new PlannerError(
          'agent_forbidden',
          'the local executor link serves executor routes only',
        );
      const login = trusted_ingress_login(services.config, raw_header_map(request), 'websocket');
      // The login check passes a request with no login header. This gate does not.
      if (services.config.trusted_ingress_provider && login === null)
        throw new PlannerError('agent_forbidden', 'the Tailscale login is required');
    } catch (error) {
      if (!(error instanceof PlannerError)) throw error;
      socket.end(FORBIDDEN);
      return;
    }
    server.handleUpgrade(request, socket, head, (ws) => {
      new LiveSession(ws, services, counter, sessions);
    });
  };
  app.server.on('upgrade', upgrade);
  app.addHook('preClose', async () => {
    stopping = true;
    for (const session of sessions) session.shut_down();
  });
  app.addHook('onClose', async () => {
    app.server.off('upgrade', upgrade);
    server.close();
  });
}
