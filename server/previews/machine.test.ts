/** The native roundtrip covers ownership. This seam isolates close races during awaited proof. */
import { expect, test, vi } from 'vitest';
import net from 'node:net';
import { Duplex, PassThrough } from 'node:stream';
import { realpath } from 'node:fs/promises';
import { Deferred } from '../core/deferred.ts';
import type { MacHttpClient } from '../executor/mac/transport/http.ts';
import type { MacRunningChild } from '../executor/mac/runner/contracts.ts';
import type { ProcessIdentity, ProcessInspector } from './contracts.ts';
import type { PreviewRelay } from './relay.ts';
import { MachinePreviews } from './machine.ts';

interface Entry {
  ended: boolean;
  generation: string;
  worktree: string;
  child: MacRunningChild;
  process: ProcessIdentity;
  relay: PreviewRelay;
}
interface Access {
  prepared: Map<string, Entry>;
  inspector: ProcessInspector;
  sockets: Set<net.Socket>;
  connect(entry: Entry, stream: Duplex): Promise<void>;
}

test('a browser close during admission retires only its connection and permits the next connection', async () => {
  const began = performance.now();
  const owner = new MachinePreviews({} as MacHttpClient);
  const access = owner as unknown as Access;
  const worktree = await realpath(process.cwd());
  const close_preview = vi.fn();
  const entry: Entry = {
    ended: false,
    generation: 'generation',
    worktree,
    child: { ticket_id: 'ticket' } as MacRunningChild,
    process: { boot_id: 'boot', pid: 1, start_ticks: 1, listener_inode: 1, port: 1234, worktree },
    relay: { close: close_preview } as unknown as PreviewRelay,
  };
  access.prepared.set(entry.child.ticket_id, entry);
  const sockets: FakeSocket[] = [];
  let auto_connect = true;
  let connection_entered = new Deferred<void>();
  class FakeSocket extends Duplex {
    localPort = 12345;
    connect(): this {
      connection_entered.resolve();
      if (auto_connect) queueMicrotask(() => this.emit('connect'));
      return this;
    }
    setNoDelay(): this {
      return this;
    }
    override _read(): void {}
    override _write(_chunk: Buffer, _encoding: BufferEncoding, done: () => void): void {
      done();
    }
  }
  const constructor = vi.spyOn(net, 'Socket').mockImplementation(function () {
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket as unknown as net.Socket;
  });
  const check = vi.spyOn(access.inspector, 'check').mockResolvedValue();
  const proof = vi.spyOn(access.inspector, 'connected').mockResolvedValue();
  const flush = () => new Promise<void>((resolve) => setImmediate(resolve));
  try {
    // Initial inspection has no socket yet. Later phases already own one.
    for (const phase of [
      'initial inspection',
      'TCP connect',
      'connection proof',
      'final inspection',
    ]) {
      connection_entered = new Deferred<void>();
      const entered = new Deferred<void>(),
        release = new Deferred<void>();
      check.mockReset().mockResolvedValue();
      proof.mockReset().mockResolvedValue();
      auto_connect = phase !== 'TCP connect';
      if (phase === 'initial inspection')
        check.mockImplementationOnce(async () => {
          entered.resolve();
          await release.promise;
        });
      if (phase === 'connection proof')
        proof.mockImplementationOnce(async () => {
          entered.resolve();
          await release.promise;
        });
      if (phase === 'final inspection')
        check.mockResolvedValueOnce().mockImplementationOnce(async () => {
          entered.resolve();
          await release.promise;
        });
      const stream = new PassThrough();
      const pending = access.connect(entry, stream);
      const refused = expect(pending).rejects.toThrow('Preview connection ended.');
      await (phase === 'TCP connect' ? connection_entered.promise : entered.promise);
      stream.destroy();
      release.resolve();
      await refused;
      await flush();
      expect(access.sockets.size, phase).toBe(0);
      expect(
        sockets.every((socket) => socket.destroyed),
        phase,
      ).toBe(true);
      expect(entry.ended, phase).toBe(false);
      expect(close_preview, phase).not.toHaveBeenCalled();
    }
    // A temporary process-inspection failure also belongs to one browser attempt.
    check.mockRejectedValueOnce(new Error('inspection is busy'));
    await expect(access.connect(entry, new PassThrough())).rejects.toThrow('inspection is busy');
    expect(entry.ended).toBe(false);
    expect(close_preview).not.toHaveBeenCalled();
    auto_connect = true;
    check.mockReset().mockResolvedValue();
    proof.mockReset().mockResolvedValue();
    const next = new PassThrough();
    await access.connect(entry, next);
    expect(access.sockets.size).toBe(1);
    next.destroy();
    await flush();
    expect(access.sockets.size).toBe(0);
  } finally {
    await owner.stop();
    constructor.mockRestore();
    check.mockRestore();
    proof.mockRestore();
    console.info(
      'MACHINE_PREVIEW_ADMISSION',
      JSON.stringify({ total_ms: performance.now() - began, new_listeners: 0, new_processes: 0 }),
    );
  }
});
