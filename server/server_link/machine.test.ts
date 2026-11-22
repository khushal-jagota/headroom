/** Durable monotonic memory protects restart, manual changes, and late replies. */
import { expect, test } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MachineServerLink } from './machine.ts';
import type { ServerLocation } from './contracts.ts';
import { parse_server_location, validate_server_address } from './logic.ts';
const location = (
  move_number = 0,
  server_url: string | null = null,
  state: 'active' | 'moved' = 'active',
): ServerLocation => ({ server_id: 'server', move_number, server_url, state });
const header = (value: ServerLocation) => Buffer.from(JSON.stringify(value)).toString('base64url');

test('machine remembers its server and refuses stale, foreign, and late replies after restart', async () => {
  const root = mkdtempSync(join(tmpdir(), 'server-link-memory-'));
  try {
    const first = new MachineServerLink(root, 'https://old.example');
    await first.prepare(async () => location());
    await first.accept(
      header(location(2, 'https://new.example')),
      'https://old.example',
      async () => location(2, 'https://new.example'),
    );
    const restart = new MachineServerLink(root, 'https://old.example');
    expect(restart.current()).toEqual({
      server_id: 'server',
      highest_move_number: 2,
      server_url: 'https://new.example',
    });
    restart.request_failed();
    await expect(restart.prepare(async () => location(1))).rejects.toThrow('older move number');
    await expect(
      restart.prepare(async () => ({ ...location(3), server_id: 'copy' })),
    ).rejects.toThrow('another server');
    await expect(
      first.accept(header(location(1)), 'https://old.example', async () => location()),
    ).rejects.toThrow('older move number');
    expect(restart.current().highest_move_number).toBe(2);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('manual address verifies moved target without resetting authority or saving a failed destination', async () => {
  const root = mkdtempSync(join(tmpdir(), 'server-link-manual-'));
  try {
    const first = new MachineServerLink(root, 'https://old.example');
    await first.prepare(async () => location());
    await expect(
      first.prepare(async (origin) => {
        if (origin === 'https://pointer.example')
          return location(3, 'https://unreachable.example', 'moved');
        throw new Error('unreachable');
      }, 'https://pointer.example'),
    ).rejects.toThrow('unreachable');
    expect(first.current()).toEqual({
      server_id: 'server',
      highest_move_number: 3,
      server_url: 'https://old.example',
    });
    await expect(first.prepare(async () => location(2), 'https://stale.example')).rejects.toThrow(
      'older move number',
    );
    const visited: string[] = [];
    await first.prepare(async (origin) => {
      visited.push(origin);
      return origin === 'https://pointer.example'
        ? location(3, 'https://new.example', 'moved')
        : location(3);
    }, 'https://pointer.example');
    expect(visited).toEqual(['https://pointer.example', 'https://new.example']);
    expect(first.current_url).toBe('https://new.example');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('location rejects unsafe addresses and move numbers before persistence', () => {
  for (const url of [
    'http://example.com',
    'https://user:pass@example.com',
    'https://example.com/path',
    'https://example.com?query=1',
  ])
    expect(() => validate_server_address(url)).toThrow('HTTPS origin');
  expect(validate_server_address('/home/user/data/executor-link/server.sock')).toBe(
    '/home/user/data/executor-link/server.sock',
  );
  for (const path of ['/a/../b.sock', '/a/b.sock/', '/a\nb.sock'])
    expect(() => validate_server_address(path)).toThrow('plain absolute path');
  expect(() => parse_server_location({ ...location(), move_number: -1 })).toThrow('invalid');
  expect(() =>
    parse_server_location({ ...location(), move_number: Number.MAX_SAFE_INTEGER + 1 }),
  ).toThrow('invalid');
  expect(() => parse_server_location(location(1, null, 'moved'))).toThrow('new address');
});

test('machine code on the local link keeps it while the server is active, and follows a move', async () => {
  const root = mkdtempSync(join(tmpdir(), 'server-link-local-'));
  const socket = join(root, 'executor-link', 'server.sock');
  try {
    const machine = new MachineServerLink(root, socket);
    await machine.prepare(async () => location(0, 'https://server.example'));
    expect(machine.current_url).toBe(socket);
    await machine.accept(header(location(0, 'https://server.example')), socket, async () =>
      location(0, 'https://server.example'),
    );
    expect(machine.current_url).toBe(socket);
    await expect(
      machine.accept(header(location(1, 'https://new.example', 'moved')), socket, async () =>
        location(1),
      ),
    ).rejects.toThrow('The server moved.');
    expect(machine.current_url).toBe('https://new.example');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
