/** Export-only connected proofs. Every path belongs to the direct driver's private tree. */
import fs from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  createDatabaseFromSeed,
  createDatabase,
  createReadAccess,
  checkpointDatabase,
} from '../../../core/database/index.ts';
import type {
  ApplicationSchemaSeed,
  Database,
  TransactionSql,
} from '../../../core/database/contracts.ts';
import { bootstrap_application } from '../../bootstrap.ts';
import { create_database_backup, apply_verified_snapshot_retention } from '../capture.ts';
import { restore_database_snapshot } from '../restore.ts';
import { verified_snapshot_retention_plan } from '../evidence.ts';
import { OWNER } from '../../../core/contracts.ts';
import { read_ticket, edit_ticket } from '../../../tickets/data/index.ts';
import { with_worker_runtime_definitions } from '../../../worker_types/configuration.ts';
import type { Ticket } from '../../../tickets/contracts.ts';
import { ticket_json } from '../../../tickets/views.ts';
import { backup_with_interleaved_commits } from '../../../core/database/testing/backup_interleaving.ts';
import { environment_restore } from '../../cli.ts';
import { create_production_operator_dependencies } from '../../operator_dependencies.ts';

interface Request {
  action: 'backup' | 'restore' | 'fresh' | 'retention' | 'concurrent' | 'restore-command';
  root: string;
  source?: string;
  destination?: string;
  revision?: string;
  snapshot?: string;
  live_stopped?: boolean;
  fail_target?: string;
  fail_publication?: boolean;
  ticket?: { id: string; title: string; now: number };
}
export async function backup_probe(request: Request): Promise<unknown> {
  const rename = fs.rename;
  let injected = false;
  fs.rename = async (source, destination) => {
    if (
      !injected &&
      ((request.fail_target !== undefined && destination.toString() === request.fail_target) ||
        (request.fail_publication && source.toString().includes('/.backup-')))
    ) {
      injected = true;
      throw new Error('synthetic rename failure');
    }
    return rename(source, destination);
  };
  try {
    if (request.action === 'concurrent')
      return await backup_with_interleaved_commits(request.source!, request.destination!);
    if (request.action === 'restore-command')
      return await environment_restore(
        {
          snapshot: request.snapshot!,
          destination_db: request.destination!,
          live_stopped: true,
        },
        await create_production_operator_dependencies('restore', undefined, { ...process.env }),
      );
    if (request.action === 'backup')
      return {
        snapshot: await create_database_backup(
          request.source!,
          join(request.root, 'backups'),
          request.revision ?? 'typescript',
        ),
      };
    if (request.action === 'restore') {
      await restore_database_snapshot(request.snapshot!, request.destination!, {
        live_stopped: request.live_stopped ?? true,
      });
      return request.ticket === undefined
        ? { restored: true }
        : {
            restored: true,
            ticket: ticket_probe(request.destination!, request.ticket),
          };
    }
    if (request.action === 'retention') {
      const root = join(request.root, 'backups');
      await apply_verified_snapshot_retention(root, await verified_snapshot_retention_plan(root));
      return { retained: true };
    }
    return fresh_probe(request.root);
  } catch (error) {
    return { error: (error as Error).message, injected };
  } finally {
    fs.rename = rename;
  }
}
function ticket_probe(path: string, ticket: NonNullable<Request['ticket']>) {
  const payload = (value: Ticket) => ({
    ...ticket_json(value, value.updated_at),
    worker_step_claim: value.worker_step_claim,
    worker_step_claim_revision: value.worker_step_claim_revision,
    pending_proposal_revision: value.pending_proposal_revision,
  });
  // Opening an existing image must not repair it with migrations or bootstrap.
  const database = createDatabase(path);
  let before: Ticket;
  try {
    before = database.read((sql) =>
      with_worker_runtime_definitions(sql, () => {
        const saved = read_ticket(sql, ticket.id);
        database.write((tx) =>
          edit_ticket(tx, ticket.id, {
            edit: { title: ticket.title },
            principal: OWNER,
            now: ticket.now,
            title_max_chars: 200,
          }),
        );
        return saved;
      }),
    );
  } finally {
    database.close();
  }
  const reopened = createReadAccess(path);
  try {
    return {
      before: payload(before),
      after: reopened.read((sql) => payload(read_ticket(sql, ticket.id))),
    };
  } finally {
    reopened.close();
  }
}
function fresh_probe(root: string): unknown {
  const seed = JSON.parse(
    readFileSync(
      new URL('../../../core/database/application_schema.json', import.meta.url),
      'utf8',
    ),
  ) as ApplicationSchemaSeed;
  const databases: Database[] = [];
  try {
    for (const index of [0, 1])
      databases.push(
        createDatabaseFromSeed(join(root, `fresh-${index}.sqlite`), seed, (tx) =>
          bootstrap_application(tx, 42),
        ),
      );
    const states = databases.map((database) =>
      database.read((sql) => {
        const schema = sql.all<{ type: string; name: string; sql: string }>(
          "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name",
        );
        const inventory: Record<string, number> = {};
        for (const table of sql.all<{ name: string }>(
          "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )) {
          const count = sql.get<{ n: number }>(
            `SELECT count(*) AS n FROM "${table.name.replaceAll('"', '""')}"`,
          )!.n;
          if (count) inventory[table.name] = count;
        }
        return {
          schema,
          inventory,
          file_pragmas: Object.fromEntries(
            ['page_size', 'auto_vacuum', 'user_version', 'journal_mode'].map((name) => [
              name,
              Object.values(sql.get(`SELECT ${name} FROM pragma_${name}`)!)[0],
            ]),
          ),
          revision: sql.get('SELECT version_num FROM alembic_version'),
          navigation: sql.get('SELECT * FROM navigation_meta'),
          retention: sql.all('SELECT * FROM navigation_delivery_retention'),
          web_push: sql.get('SELECT * FROM notification_web_push_identity'),
          projects: sql.all('SELECT * FROM projects ORDER BY id'),
          launch: sql.get('SELECT * FROM manager_launch_defaults'),
          attention: sql.get('SELECT * FROM attention_history_metadata'),
          chief: sql.get('SELECT * FROM chief_page'),
          preferences: sql.all('SELECT * FROM notification_preferences'),
        };
      }),
    );
    const existing_files = ['', '-wal', '-shm'].map(
      (suffix) => join(root, 'fresh-0.sqlite') + suffix,
    );
    const bytes = () =>
      existing_files.map((path) =>
        existsSync(path) ? readFileSync(path).toString('base64') : null,
      );
    const before = bytes();
    let existing_refused = false,
      retained: TransactionSql | undefined;
    try {
      createDatabaseFromSeed(join(root, 'fresh-0.sqlite'), seed, () => {
        throw new Error('must not execute');
      });
    } catch {
      existing_refused = true;
    }
    const existing_unchanged = JSON.stringify(bytes()) === JSON.stringify(before);
    const errors: Record<string, string> = {};
    for (const kind of ['throw', 'async'] as const) {
      const path = join(root, `failed-${kind}.sqlite`);
      try {
        createDatabaseFromSeed(
          path,
          seed,
          kind === 'throw'
            ? (tx) => {
                retained = tx;
                tx.run('INSERT INTO chief_page(singleton) VALUES (1)');
                throw new Error('synthetic bootstrap failure');
              }
            : async (tx) => {
                tx.run('INSERT INTO chief_page(singleton) VALUES (1)');
              },
        );
      } catch (error) {
        errors[kind] = (error as Error).message;
      }
      const read = createReadAccess(path);
      try {
        errors[kind + '_tables'] = String(
          read.read(
            (sql) =>
              sql.get<{ n: number }>(
                "SELECT count(*) AS n FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
              )!.n,
          ),
        );
      } finally {
        read.close();
      }
    }
    try {
      retained!.run('SELECT 1');
    } catch (error) {
      errors.retained = (error as Error).message;
    }
    databases[0].write(() => {
      try {
        checkpointDatabase(databases[0], 'PASSIVE');
      } catch (error) {
        errors.write_checkpoint = (error as Error).message;
      }
    });
    databases[0].read(() => {
      try {
        checkpointDatabase(databases[0], 'PASSIVE');
      } catch (error) {
        errors.read_checkpoint = (error as Error).message;
      }
    });
    return {
      states,
      existing_refused,
      existing_unchanged,
      errors,
      checkpoint: checkpointDatabase(databases[0], 'TRUNCATE'),
    };
  } finally {
    for (const database of databases) database.close();
  }
}
