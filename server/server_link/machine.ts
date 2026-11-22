import {
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { flock } from '../core/locks/flock.ts';
import { ExecutorValidationError } from '../executor/protocol_errors.ts';
import type { MachineServerRecord, ServerLinkAdmission, ServerLocation } from './contracts.ts';
import { accept_server_location, parse_server_location, validate_server_address } from './logic.ts';

export function machine_server_path(root: string): string {
  return join(root, 'server-link.json');
}
export class MachineServerLink implements ServerLinkAdmission {
  private readonly root: string;
  private readonly initial_url: string;
  constructor(root: string, initial_url: string) {
    this.root = root;
    this.initial_url = initial_url;
    validate_server_address(initial_url);
  }
  private read(): MachineServerRecord {
    let value: unknown;
    try {
      value = JSON.parse(readFileSync(machine_server_path(this.root), 'utf8'));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT')
        return {
          server_url: validate_server_address(this.initial_url),
          server_id: null,
          highest_move_number: 0,
        };
      throw error;
    }
    const record = value as MachineServerRecord;
    if (
      !record ||
      typeof record !== 'object' ||
      !(
        record.server_id === null ||
        (typeof record.server_id === 'string' && !!record.server_id.trim())
      ) ||
      !Number.isSafeInteger(record.highest_move_number) ||
      record.highest_move_number < 0 ||
      typeof record.server_url !== 'string'
    )
      throw new ExecutorValidationError('The saved server address is invalid.');
    return { ...record, server_url: validate_server_address(record.server_url) };
  }
  get current_url(): string {
    return this.read().server_url;
  }
  current(): MachineServerRecord {
    return this.read();
  }
  private update(
    location: ServerLocation,
    origin: string,
    accept_address = false,
  ): MachineServerRecord {
    mkdirSync(this.root, { recursive: true, mode: 0o700 });
    const lock = openSync(join(this.root, 'server-link.lock'), 'a', 0o600);
    if (flock(lock, 'exclusive') !== 0) {
      closeSync(lock);
      throw new Error('Cannot lock the saved server address.');
    }
    try {
      const before = this.read();
      const accepted = accept_server_location(before, location, origin);
      const next = {
        ...accepted,
        server_url: accept_address ? validate_server_address(origin) : before.server_url,
      };
      if (JSON.stringify(next) !== JSON.stringify(before)) {
        const temporary = join(this.root, `.server-link-${randomUUID()}.json`);
        try {
          const file = openSync(temporary, 'wx', 0o600);
          try {
            writeFileSync(file, JSON.stringify(next) + '\n');
            fsyncSync(file);
          } finally {
            closeSync(file);
          }
          renameSync(temporary, machine_server_path(this.root));
          const directory = openSync(this.root, 'r');
          try {
            fsyncSync(directory);
          } finally {
            closeSync(directory);
          }
        } finally {
          try {
            unlinkSync(temporary);
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
          }
        }
      }
      return next;
    } finally {
      try {
        flock(lock, 'unlock');
      } finally {
        closeSync(lock);
      }
    }
  }
  async prepare(
    probe: (origin: string) => Promise<unknown>,
    manual_url?: string,
  ): Promise<MachineServerRecord> {
    let origin = manual_url === undefined ? this.current_url : validate_server_address(manual_url);
    for (let attempt = 0; attempt < 5; attempt++) {
      const location = parse_server_location(await probe(origin));
      this.update(location, origin);
      if (
        location.state === 'active' &&
        (location.server_url === null || location.server_url === origin)
      )
        return this.update(location, origin, true);
      origin = location.server_url!;
    }
    throw new ExecutorValidationError('The server address did not reach the active server.');
  }
  private parse_header(header: string | string[] | undefined): ServerLocation {
    if (typeof header !== 'string' || header.length > 16384)
      throw new ExecutorValidationError('The server did not return its identity.');
    let location: ServerLocation;
    try {
      location = parse_server_location(
        JSON.parse(Buffer.from(header, 'base64url').toString('utf8')),
      );
    } catch (error) {
      if (error instanceof ExecutorValidationError) throw error;
      throw new ExecutorValidationError('The server returned an invalid identity.');
    }
    return location;
  }
  verify(header: string | string[] | undefined, origin: string): void {
    const location = this.parse_header(header);
    accept_server_location(this.read(), location, origin);
    if (location.state !== 'active') throw new ExecutorValidationError('The server moved.');
  }
  async accept(
    header: string | string[] | undefined,
    origin: string,
    probe: (origin: string) => Promise<unknown>,
  ): Promise<void> {
    const location = this.parse_header(header);
    this.update(location, origin);
    if (location.server_url !== null && location.server_url !== origin)
      await this.prepare(probe, location.server_url);
    // A target probe can raise the floor above this response. Recheck before consumption.
    this.update(location, origin);
    if (location.state !== 'active')
      throw new ExecutorValidationError('The server moved. The next request uses its new address.');
  }
}
