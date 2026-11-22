/** The server keeps preview destinations; native process evidence stays on the machine. */
import { randomBytes } from 'node:crypto';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer } from 'ws';
import type { Database } from '../core/database/contracts.ts';
import { eligibility } from './data.ts';
import { PreviewError, type PreviewRecord } from './contracts.ts';
import { base_path } from './contracts.ts';
import { PreviewRelay } from './relay.ts';
import { CHUNK_BYTES, RESERVED_PANELS_PORT } from './config.ts';
import { require_port } from './logic/eligibility.ts';
import { SERVER_LOCATION_HEADER } from '../server_link/contracts.ts';
import { read_server_location } from '../server_link/data.ts';
export interface PreviewMachine {
  readonly machine_id: string;
  readonly incarnation: string;
  readonly protocol: string;
  readonly require_current: () => void;
}
interface Entry {
  record: PreviewRecord;
  machine: PreviewMachine;
  relay?: PreviewRelay;
}
export class RemotePreviews {
  private readonly entries = new Map<string, Entry>();
  private readonly server = new WebSocketServer({
    noServer: true,
    maxPayload: CHUNK_BYTES + 4,
    perMessageDeflate: false,
  });
  private readonly epoch = randomBytes(16).toString('hex');
  private closed = false;
  readonly database: Database;
  readonly origin: string;
  constructor(database: Database, origin: string) {
    this.database = database;
    this.origin = origin.replace(/\/+$/, '');
    this.server.on('headers', (headers) => {
      const location = database.read((sql) => read_server_location(sql)!);
      headers.push(
        `${SERVER_LOCATION_HEADER}: ${Buffer.from(JSON.stringify(location)).toString('base64url')}`,
      );
    });
  }
  private eligible(ticket_id: string, machine: PreviewMachine): void {
    if (this.closed) throw new PreviewError('Previews are stopped.', 503);
    if (!this.origin.startsWith('https://'))
      throw new PreviewError(
        'Application previews require configured trusted Tailscale ingress.',
        503,
      );
    machine.require_current();
    const ticket = this.database.read((sql) => eligibility(sql, ticket_id));
    if (ticket.terminal || ticket.machine_id !== machine.machine_id)
      throw new PreviewError('The Ticket no longer runs on this machine.', 410);
  }
  private end(entry: Entry, reason: string): void {
    if (this.entries.get(entry.record.generation) === entry)
      this.entries.delete(entry.record.generation);
    entry.record.state = 'ended';
    entry.record.ended_reason = reason;
    const relay = entry.relay;
    entry.relay = undefined;
    relay?.close();
  }
  command(ticket_id: string, machine: PreviewMachine, operation: unknown): object {
    this.eligible(ticket_id, machine);
    if (!['prepare', 'show', 'stop'].includes(operation as string))
      throw new PreviewError('Unknown preview operation.');
    const previous = [...this.entries.values()]
      .reverse()
      .find((e) => e.record.ticket_id === ticket_id);
    if (operation === 'prepare') {
      if (previous) this.end(previous, 'Replaced by a new preparation.');
      const record: PreviewRecord = {
        ticket_id,
        generation: randomBytes(16).toString('hex'),
        public_origin: this.origin,
        server_epoch: this.epoch,
        created_at: Math.floor(Date.now() / 1000),
        state: 'prepared',
        ended_reason: null,
        worktree: '',
        process: null,
        machine_id: machine.machine_id,
      };
      this.entries.set(record.generation, { record, machine });
      return this.result(record);
    }
    if (!previous) return { preview: null };
    if (operation === 'stop') this.end(previous, 'The preview was stopped.');
    else if (previous.record.state === 'active') this.check(ticket_id, previous.record.generation);
    return this.result(previous.record);
  }
  result(record: PreviewRecord): object {
    const { worktree: _worktree, process: _process, target_port: _port, ...public_record } = record;
    return {
      ...public_record,
      base_path: base_path(record),
      url: record.public_origin + base_path(record),
    };
  }
  has(generation: string): boolean {
    return this.entries.has(generation);
  }
  check(ticket_id: string, generation: string): PreviewRecord {
    const entry = this.entries.get(generation);
    if (!entry || entry.record.ticket_id !== ticket_id || entry.record.state === 'ended')
      throw new PreviewError(
        entry?.record.ended_reason ?? 'This preview address has expired.',
        410,
      );
    try {
      this.eligible(ticket_id, entry.machine);
      if (entry.record.state !== 'active' || !entry.relay)
        throw new PreviewError('The preview application is not registered.', 410);
    } catch (error) {
      this.end(entry, 'The preview application or machine is unavailable.');
      throw error;
    }
    return entry.record;
  }
  async connect(record: PreviewRecord): Promise<Duplex> {
    this.check(record.ticket_id, record.generation);
    return this.entries.get(record.generation)!.relay!.connect();
  }
  upgrade(
    machine: PreviewMachine,
    generation: string,
    request: IncomingMessage,
    socket: Duplex,
    head: Buffer,
  ): void {
    const entry = this.entries.get(generation);
    if (
      !entry ||
      entry.record.state !== 'prepared' ||
      entry.machine.machine_id !== machine.machine_id ||
      entry.machine.incarnation !== machine.incarnation
    )
      throw new PreviewError('This preview preparation has ended.', 410);
    this.eligible(entry.record.ticket_id, machine);
    const port = Number(new URL(request.url!, 'http://executor').searchParams.get('port'));
    require_port(port, RESERVED_PANELS_PORT);
    if (!Number.isInteger(port)) throw new PreviewError('Preview port is invalid.');
    this.server.handleUpgrade(request, socket, head, (ws) => {
      if (this.entries.get(generation) !== entry || entry.record.state !== 'prepared') {
        ws.terminate();
        return;
      }
      entry.record.target_port = port;
      entry.record.state = 'active';
      entry.relay = new PreviewRelay(ws);
      ws.once('close', () => this.end(entry, 'The preview machine disconnected.'));
    });
  }
  recheck(): string[] {
    const ended: string[] = [];
    for (const entry of this.entries.values()) {
      if (entry.record.state === 'ended') continue;
      try {
        this.eligible(entry.record.ticket_id, entry.machine);
      } catch {
        this.end(entry, 'The Ticket or machine is unavailable.');
        ended.push(entry.record.generation);
      }
    }
    return ended;
  }
  close(): void {
    this.closed = true;
    for (const entry of this.entries.values()) this.end(entry, 'Previews stopped.');
    this.server.close();
  }
}
