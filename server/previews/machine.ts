/** Native preview custody belongs to the machine that runs the Ticket. */
import type { StopOptions } from '../lifecycle/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { realpath } from 'node:fs/promises';
import { join } from 'node:path';
import net from 'node:net';
import { once } from 'node:events';
import type { Duplex } from 'node:stream';
import type { MacHttpClient } from '../executor/mac/transport/http.ts';
import type { MacRunningChild } from '../executor/mac/runner/contracts.ts';
import { child_ticket_id } from '../executor/mac/runner/logic/child_owner.ts';
import type { JsonObject } from '../executor/wire.generated.ts';
import { PreviewError, type ProcessIdentity } from './contracts.ts';
import { process_inspector } from './platform.ts';
import { PreviewRelay } from './relay.ts';
import { require_port } from './logic/eligibility.ts';
import { CONNECT_SECONDS, RESERVED_PANELS_PORT, LIFECYCLE_SECONDS } from './config.ts';
interface Prepared {
  ticket_id: string;
  ended: boolean;
  generation: string;
  worktree: string;
  child: MacRunningChild;
  relay?: PreviewRelay;
  process?: ProcessIdentity;
}
export class MachinePreviews {
  private readonly inspector = process_inspector();
  private readonly prepared = new Map<string, Prepared>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly sockets = new Set<net.Socket>();
  private timer: ReturnType<typeof setInterval>;
  private closed = false;
  private closing: Promise<void> | undefined;
  readonly client: MacHttpClient;
  constructor(client: MacHttpClient) {
    this.client = client;
    let checking = false;
    this.timer = setInterval(() => {
      if (checking || this.closed) return;
      checking = true;
      const task = Promise.all(
        [...this.prepared.values()].map(async (entry) => {
          if (!entry.process || !entry.relay) return;
          try {
            await this.check(entry);
          } catch {
            this.end(entry);
          }
        }),
      ).finally(() => {
        checking = false;
      });
      this.own(task);
    }, LIFECYCLE_SECONDS * 1000);
    this.timer.unref();
  }
  private own<T>(task: Promise<T>): Promise<T> {
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      () => this.operations.delete(task),
    );
    return task;
  }
  command(child: MacRunningChild, payload: JsonObject): Promise<JsonObject> {
    return this.own(this.dispatch(child, payload));
  }
  private async dispatch(child: MacRunningChild, payload: JsonObject): Promise<JsonObject> {
    if (this.closed) throw new PreviewError('Previews are stopped.', 503);
    const ticket_id = child_ticket_id(child.owner);
    if (ticket_id === null) throw new PreviewError('Only a Ticket Worker shows previews.', 403);
    const operation = payload.operation;
    if (!['prepare', 'register', 'show', 'stop'].includes(operation as string))
      throw new PreviewError('Unknown preview operation.');
    if (operation !== 'register') {
      const result = await this.client.preview({
        operation,
        child: child.reference as unknown as JsonObject,
      });
      if (this.closed) throw new PreviewError('Previews are stopped.', 503);
      if (operation === 'prepare') {
        const worktree = await realpath(
          child.workspace
            ? join(child.localized_start.workspace_folder, 'worktree')
            : child.localized_start.workspace_folder,
        );
        const previous = this.prepared.get(ticket_id);
        if (previous) this.end(previous);
        if (typeof result.generation !== 'string') throw new Error('Invalid preview preparation');
        this.prepared.set(ticket_id, {
          ticket_id,
          ended: false,
          generation: result.generation,
          worktree,
          child,
        });
        return { ...result, worktree };
      }
      if (operation === 'stop') {
        const entry = this.prepared.get(ticket_id);
        if (entry) this.end(entry);
      }
      const local = this.prepared.get(ticket_id);
      return local ? { ...result, worktree: local.worktree } : result;
    }
    const entry = this.prepared.get(ticket_id);
    if (!entry || entry.ended || entry.child !== child || entry.relay)
      throw new PreviewError('Prepare a new preview address before registration.');
    const port = payload.port;
    if (typeof port !== 'number' || !Number.isInteger(port))
      throw new PreviewError('Preview port is invalid.');
    require_port(port, RESERVED_PANELS_PORT);
    entry.process = await this.inspector.discover(port, entry.worktree);
    await this.check(entry);
    const socket = await this.client.preview_channel(entry.generation, port);
    if (this.closed || this.prepared.get(ticket_id) !== entry) {
      socket.terminate();
      throw new Error('Preview preparation ended');
    }
    entry.relay = new PreviewRelay(socket, (stream) => this.own(this.connect(entry, stream)));
    socket.once('close', () => this.end(entry));
    return {
      ...(await this.client.preview({
        operation: 'show',
        child: child.reference as unknown as JsonObject,
      })),
      worktree: entry.worktree,
    };
  }
  private async check(entry: Prepared): Promise<void> {
    if (
      this.closed ||
      entry.ended ||
      this.prepared.get(entry.ticket_id) !== entry ||
      !entry.process ||
      (await realpath(entry.worktree)) !== entry.worktree
    )
      throw new PreviewError('Preview worktree is unavailable.', 410);
    await this.inspector.check(entry.process);
  }
  private async connect(entry: Prepared, stream: Duplex): Promise<void> {
    let socket: net.Socket | undefined;
    const require_open = (): void => {
      if (
        stream.destroyed ||
        this.closed ||
        entry.ended ||
        this.prepared.get(entry.ticket_id) !== entry
      )
        throw new PreviewError('Preview connection ended.', 410);
    };
    // Close can precede any awaited inspection or the creation of the local socket.
    stream.once('close', () => socket?.destroy(new PreviewError('Preview connection ended.', 410)));
    try {
      require_open();
      await this.check(entry);
      require_open();
      socket = new net.Socket();
      const connected = socket;
      this.sockets.add(connected);
      this.own(new Promise<void>((resolve) => connected.once('close', () => resolve())));
      connected.on('error', () => stream.destroy());
      connected.once('close', () => {
        this.sockets.delete(connected);
        stream.destroy();
      });
      connected.setNoDelay();
      connected.connect({ host: '127.0.0.1', port: entry.process!.port });
      connected.pause();
      await once(connected, 'connect', { signal: AbortSignal.timeout(CONNECT_SECONDS * 1000) });
      require_open();
      await this.inspector.connected(entry.process!, connected.localPort!);
      require_open();
      await this.check(entry);
      require_open();
      connected.pipe(stream).pipe(connected);
      connected.resume();
    } catch (error) {
      socket?.destroy();
      stream.destroy();
      throw error;
    }
  }
  private end(entry: Prepared): void {
    entry.ended = true;
    entry.relay?.close();
    entry.relay = undefined;
  }
  stop(options?: StopOptions): Promise<void> {
    if (!this.closing) {
      this.closed = true;
      clearInterval(this.timer);
      for (const entry of this.prepared.values()) this.end(entry);
      for (const socket of this.sockets) socket.destroy();
      this.closing = this.settled();
    }
    return options
      ? observe_shutdown(
          this.closing,
          options,
          'Preview work remains unfinished at the shutdown deadline.',
        )
      : this.closing;
  }
  async settled(): Promise<void> {
    while (this.operations.size) await Promise.allSettled([...this.operations]);
  }
}
