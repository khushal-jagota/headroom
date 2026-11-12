/** Async inspection never holds a database transaction across an await. */
import { randomBytes } from 'node:crypto';
import { resolve, join } from 'node:path';
import { realpath } from 'node:fs/promises';
import type { Database, TransactionSql } from '../core/database/contracts.ts';
import * as config from './config.ts';
import * as data from './data.ts';
import { PreviewError, type PreviewRecord } from './contracts.ts';
import { require_eligible, require_port, require_vps } from './logic/eligibility.ts';
import { LinuxProcessInspector, validate_worktree } from './process.ts';
export class PreviewService {
  readonly database: Database;
  readonly public_origin: string;
  readonly panels_port: number;
  readonly server_epoch = randomBytes(16).toString('hex');
  readonly inspector = new LinuxProcessInspector();
  private outstanding = 0;
  private closed = false;
  private registering = false;
  private known = new Set<string>();
  private process_checks = new Map<string, Promise<void>>();
  private active = 0;
  private lifecycle_active = false;
  constructor(database: Database, public_origin: string, panels_port = 8767) {
    this.database = database;
    this.public_origin = public_origin.replace(/\/+$/, '');
    this.panels_port = panels_port;
  }
  private write<T>(work: (tx: TransactionSql) => T): T {
    return this.database.write(work, { busyTimeoutMs: config.DATABASE_BUSY_MILLISECONDS });
  }
  private async run<T>(operation: () => Promise<T>, lifecycle = false): Promise<T> {
    if (
      this.closed ||
      this.outstanding >=
        (lifecycle ? config.INSPECTION_OUTSTANDING : config.INSPECTION_OUTSTANDING - 1)
    )
      throw new PreviewError('Preview inspection is busy; try again shortly.', 503);
    this.outstanding++;
    const submitted = performance.now();
    try {
      while (
        lifecycle
          ? this.lifecycle_active
          : this.active >= Math.max(1, config.INSPECTION_WORKERS - 1)
      ) {
        if (performance.now() - submitted >= config.INSPECTION_SECONDS * 1000)
          throw new PreviewError('Preview inspection admission expired.', 503);
        await new Promise((r) => setTimeout(r, 1));
      }
      if (lifecycle) this.lifecycle_active = true;
      else this.active++;
      try {
        if (this.closed || performance.now() - submitted >= config.INSPECTION_SECONDS * 1000)
          throw new PreviewError('Preview inspection admission expired.', 503);
        return await operation();
      } finally {
        if (lifecycle) this.lifecycle_active = false;
        else this.active--;
      }
    } catch (error) {
      if (
        ['SQLITE_BUSY', 'SQLITE_LOCKED'].some((code) =>
          String((error as { code?: string }).code).startsWith(code),
        )
      )
        throw new PreviewError('Preview registry is busy; try again shortly.', 503);
      throw error;
    } finally {
      this.outstanding--;
    }
  }
  private enabled(): void {
    if (!this.public_origin.startsWith('https://'))
      throw new PreviewError(
        'Application previews require configured trusted Tailscale ingress.',
        503,
      );
  }
  async start(): Promise<void> {
    this.write((tx) =>
      tx.run(
        `UPDATE ${data.TABLE} SET state='ended',ended_reason='Panels restarted; prepare a new address.' WHERE state!='ended' AND server_epoch!=?`,
        [this.server_epoch],
      ),
    );
  }
  async prepare(ticket_id: string): Promise<PreviewRecord> {
    this.enabled();
    const record = await this.run(async () => {
      const ticket = this.database.read((sql) => data.eligibility(sql, ticket_id));
      require_eligible(ticket);
      const worktree = await validate_worktree(ticket.project_folder!, ticket_id);
      const record: PreviewRecord = {
        ticket_id,
        generation: randomBytes(16).toString('hex'),
        worktree,
        public_origin: this.public_origin,
        server_epoch: this.server_epoch,
        created_at: Math.floor(Date.now() / 1000),
        state: 'prepared',
        ended_reason: null,
        process: null,
      };
      this.write((tx) => data.prepare(tx, record));
      return record;
    });
    this.known.add(record.generation);
    return record;
  }
  private async inspect(
    ticket_id: string,
    generation: string,
    active = true,
  ): Promise<PreviewRecord> {
    const record = this.database.read((sql) => data.get(sql, ticket_id, generation));
    if (!record || record.state === 'ended')
      throw new PreviewError(record?.ended_reason || 'This preview address has expired.', 410);
    try {
      if (record.server_epoch !== this.server_epoch)
        throw new PreviewError('Panels restarted; prepare a new address.', 410);
      const ticket = this.database.read((sql) => data.eligibility(sql, ticket_id));
      require_eligible(ticket);
      const expected = join(resolve(ticket.project_folder!), 'worktrees', `${ticket_id}-preview`);
      if (expected !== record.worktree || (await realpath(expected)) !== expected)
        throw new PreviewError('The Ticket preview worktree changed.', 410);
      if (active) {
        if (record.state !== 'active' || !record.process)
          throw new PreviewError('The preview application has not been registered.', 410);
        await this.inspector.check(record.process);
      }
    } catch (error) {
      const failure =
        error instanceof PreviewError
          ? error
          : new PreviewError('The Ticket preview worktree is unavailable.', 410);
      if (failure.status_code !== 503)
        this.write((tx) => data.end(tx, generation, failure.message));
      throw failure;
    }
    return record;
  }
  async register(ticket_id: string, port: number): Promise<PreviewRecord> {
    this.enabled();
    require_port(port, this.panels_port);
    return this.run(async () => {
      if (this.registering)
        throw new PreviewError('Another preview registration is being inspected.', 503);
      this.registering = true;
      try {
        require_vps(this.database.read((sql) => data.eligibility(sql, ticket_id)));
        const current = this.database.read((sql) => data.get(sql, ticket_id));
        if (!current || current.state !== 'prepared')
          throw new PreviewError('Prepare a new preview address before registration.');
        await this.inspect(ticket_id, current.generation, false);
        const process = await this.inspector.discover(port, current.worktree);
        await this.inspect(ticket_id, current.generation, false);
        await this.inspector.check(process);
        this.write((tx) => data.register(tx, current, process));
        return { ...current, state: 'active', process };
      } finally {
        this.registering = false;
      }
    });
  }
  async show(ticket_id: string): Promise<PreviewRecord | null> {
    return this.run(async () => {
      require_vps(this.database.read((sql) => data.eligibility(sql, ticket_id)));
      const record = this.database.read((sql) => data.get(sql, ticket_id));
      if (record && record.state !== 'ended') {
        try {
          await this.inspect(ticket_id, record.generation, record.state === 'active');
        } catch (error) {
          if (!(error instanceof PreviewError) || error.status_code !== 410) throw error;
        }
      }
      return this.database.read((sql) => data.get(sql, ticket_id));
    });
  }
  async stop(ticket_id: string): Promise<PreviewRecord | null> {
    return this.run(async () =>
      this.write((tx) => {
        require_vps(data.eligibility(tx, ticket_id));
        const record = data.get(tx, ticket_id);
        if (record) data.end(tx, record.generation, 'The preview was stopped.');
        return data.get(tx, ticket_id);
      }),
    );
  }
  async check(ticket_id: string, generation: string): Promise<PreviewRecord> {
    return this.run(() => this.inspect(ticket_id, generation));
  }
  async before_connection(record: PreviewRecord): Promise<void> {
    await this.check(record.ticket_id, record.generation);
  }
  async after_connection(record: PreviewRecord, local_port: number): Promise<void> {
    await this.run(async () => {
      const current = await this.inspect(record.ticket_id, record.generation);
      try {
        await this.inspector.connected(current.process!, local_port);
      } catch (error) {
        if (error instanceof PreviewError && error.status_code !== 503)
          this.write((tx) => data.end(tx, current.generation, error.message));
        throw error;
      }
      await this.inspect(record.ticket_id, record.generation);
    });
  }
  async recheck_all(): Promise<string[]> {
    const records = await this.run(async () => {
      for (const record of this.database.read(data.live)) {
        try {
          await this.inspect(record.ticket_id, record.generation, false);
        } catch (error) {
          if (!(error instanceof PreviewError)) throw error;
        }
      }
      return this.database.read(data.live);
    }, true);
    const current = new Set(records.map((r) => r.generation));
    const ended = [...this.known].filter((g) => !current.has(g));
    this.known = current;
    for (const record of records) {
      if (record.state === 'active' && !this.process_checks.has(record.generation)) {
        const task = this.check(record.ticket_id, record.generation)
          .then(
            () => {},
            () => {},
          )
          .finally(() => this.process_checks.delete(record.generation));
        this.process_checks.set(record.generation, task);
      }
    }
    return ended;
  }
  async close(): Promise<void> {
    this.closed = true;
    await Promise.allSettled(this.process_checks.values());
    while (this.outstanding) await new Promise((r) => setTimeout(r, 1));
  }
}
