/** Ended generations previously stayed in memory forever. Existing transport checks prove
 * byte flow, not registry retention or a late channel callback after replacement.
 * SQLite and a fake upgrade seam prove this boundary without a connected host. */
import { EventEmitter } from 'node:events';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { expect, test, vi } from 'vitest';
import WebSocket, { WebSocketServer } from 'ws';
import { create, fixture } from './ticket_fixture.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { RemotePreviews, type PreviewMachine } from '../previews/remote.ts';
import { PreviewError, type PreviewRecord } from '../previews/contracts.ts';
import { PreviewService } from '../previews/service.ts';

function expired(previews: RemotePreviews, record: PreviewRecord): void {
  expect(previews.has(record.generation)).toBe(false);
  try {
    previews.check(record.ticket_id, record.generation);
    throw new Error('An ended preview must refuse its address');
  } catch (error) {
    expect(error).toBeInstanceOf(PreviewError);
    expect((error as PreviewError).status_code).toBe(410);
  }
}
function channel() {
  const socket = new EventEmitter();
  const terminate = vi.fn(() => {
    socket.emit('close');
  });
  return Object.assign(socket, { terminate }) as unknown as WebSocket;
}

test('replacement, stop, disconnect and recheck retain only live preview generations', async () => {
  const database = fixture();
  const ticket = create(database, { machine_id: HOME_MACHINE_ID });
  const other_ticket = create(database, { machine_id: HOME_MACHINE_ID });
  let current = true;
  const machine: PreviewMachine = {
    machine_id: HOME_MACHINE_ID,
    incarnation: 'machine-process',
    protocol: '27',
    require_current() {
      if (!current) throw new Error('replaced machine');
    },
  };
  const service = new PreviewService(database, 'https://preview.test');
  const previews = service.remote;
  // The colocated machine uses its relay. The retired direct command cannot inspect its disk.
  await expect(service.prepare(ticket.id)).rejects.toThrow(
    'Preview commands must run through the Ticket machine code.',
  );
  // Inspect storage itself: filtering ended records in has() must not hide retention.
  const retained = (previews as unknown as { entries: ReadonlyMap<string, unknown> }).entries;
  const prepare = (ticket_id = ticket.id) =>
    previews.command(ticket_id, machine, 'prepare') as PreviewRecord;
  const request = { url: '/connect?port=12345' } as IncomingMessage;
  let accepted: WebSocket | undefined;
  const upgrade = vi
    .spyOn(WebSocketServer.prototype, 'handleUpgrade')
    .mockImplementation((request, _socket, _head, callback) => {
      accepted = channel();
      callback(accepted, request);
    });
  const register = (record: PreviewRecord) => {
    previews.upgrade(machine, record.generation, request, {} as Duplex, Buffer.alloc(0));
    expect(previews.check(record.ticket_id, record.generation).state).toBe('active');
    return accepted!;
  };
  try {
    const other = prepare(other_ticket.id);
    let previous = prepare();
    expect(retained.size).toBe(2);
    for (let index = 0; index < 3; index++) {
      const next = prepare();
      expired(previews, previous);
      expect(retained.size).toBe(2);
      expect(previews.has(next.generation)).toBe(true);
      expect(previews.has(other.generation)).toBe(true);
      previous = next;
    }
    const old_socket = register(previous);
    const latest = prepare();
    expect(old_socket.terminate).toHaveBeenCalledOnce();
    expired(previews, previous);
    // Closing the replaced channel cannot delete its Ticket's newer generation.
    expect(previews.has(latest.generation)).toBe(true);
    register(latest).emit('close');
    expired(previews, latest);
    expect(retained.size).toBe(1);
    expect(previews.has(other.generation)).toBe(true);
    const stopped = prepare();
    expect(previews.command(ticket.id, machine, 'stop')).toMatchObject({ state: 'ended' });
    expired(previews, stopped);
    expect(retained.size).toBe(1);
    const invalidated = prepare();
    register(invalidated);
    current = false;
    expect(previews.recheck()).toEqual([other.generation, invalidated.generation]);
    expired(previews, invalidated);
    expired(previews, other);
    expect(retained.size).toBe(0);
  } finally {
    previews.close();
    expect(retained.size).toBe(0);
    upgrade.mockRestore();
  }
});

test('a delayed upgrade callback cannot restore a replaced preview or replace its channel', () => {
  const database = fixture();
  const ticket = create(database, { machine_id: ORIGINAL_MAC_MACHINE_ID });
  const machine: PreviewMachine = {
    machine_id: ORIGINAL_MAC_MACHINE_ID,
    incarnation: 'machine-process',
    protocol: '27',
    require_current() {},
  };
  const previews = new RemotePreviews(database, 'https://preview.test');
  const callbacks: Array<(socket: WebSocket) => void> = [];
  const upgrade = vi
    .spyOn(WebSocketServer.prototype, 'handleUpgrade')
    .mockImplementation((request, _socket, _head, callback) => {
      callbacks.push((socket) => callback(socket, request));
    });
  const request = { url: '/connect?port=12345' } as IncomingMessage;
  try {
    const first = previews.command(ticket.id, machine, 'prepare') as PreviewRecord;
    previews.upgrade(machine, first.generation, request, {} as Duplex, Buffer.alloc(0));
    const latest = previews.command(ticket.id, machine, 'prepare') as PreviewRecord;
    const stale_socket = channel();
    callbacks.shift()!(stale_socket);
    expect(stale_socket.terminate).toHaveBeenCalledOnce();
    expired(previews, first);
    expect(previews.has(latest.generation)).toBe(true);
    previews.upgrade(machine, latest.generation, request, {} as Duplex, Buffer.alloc(0));
    previews.upgrade(machine, latest.generation, request, {} as Duplex, Buffer.alloc(0));
    const kept_socket = channel(),
      duplicate_socket = channel();
    callbacks.shift()!(kept_socket);
    callbacks.shift()!(duplicate_socket);
    expect(duplicate_socket.terminate).toHaveBeenCalledOnce();
    expect(kept_socket.terminate).not.toHaveBeenCalled();
    expect(previews.check(ticket.id, latest.generation).state).toBe('active');
  } finally {
    previews.close();
    upgrade.mockRestore();
  }
});
