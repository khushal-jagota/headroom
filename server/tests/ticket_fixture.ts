import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { afterAll, afterEach } from 'vitest';
import {
  createDatabase,
  type Database,
  type ReadSql,
  type TransactionSql,
} from '../core/database/index.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import { seed_shipped_worker_types, seed_chief_settings } from '../worker_types/shipped.ts';
import { OWNER, type Principal } from '../core/contracts.ts';
import * as data from '../tickets/data/index.ts';
import type { Ticket, TicketEdit } from '../tickets/contracts.ts';
const repository = resolve(import.meta.dirname, '../..');
const root = mkdtempSync(join(tmpdir(), 'panels-ticket-rules-'));
const seed = join(root, 'seed.sqlite');
// Follow the existing database fixture's Python-stdlib boundary. Read the fresh
// schema's literal declarations without importing Planner or Alembic. This proves
// rule writers on the current schema, but does not exercise migration/bootstrap.
execFileSync(
  'python3',
  [
    '-c',
    `import ast, pathlib, sqlite3, sys
source = pathlib.Path(sys.argv[2]) / 'src/planner/core/migrations/versions/current_schema.py'
statements = []
for node in ast.parse(source.read_text()).body:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'BASELINE_STATEMENTS':
        statements.extend(ast.literal_eval(node.value))
    elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == 'BASELINE_STATEMENTS':
        if not isinstance(node.op, ast.Add):
            raise RuntimeError('unexpected schema declaration')
        statements.extend(ast.literal_eval(node.value))
if not statements:
    raise RuntimeError('fresh schema declarations not found')
with sqlite3.connect(sys.argv[1]) as connection:
    for statement in statements:
        connection.execute(statement)
    connection.execute('INSERT INTO attention_history_metadata VALUES (1,0)')
    connection.executemany('INSERT INTO projects (id,name,created_at,updated_at) VALUES (?,?,0,0)', [('project_other','Other'),('project_vylo','Vylo')])
`,
    seed,
    repository,
  ],
  { cwd: repository, stdio: 'pipe' },
);
const seedDatabase = createDatabase(seed);
try {
  seedDatabase.write((tx) => {
    seed_packaged_skills(tx);
    seed_shipped_worker_types(tx);
    seed_chief_settings(tx);
  });
} finally {
  seedDatabase.close();
}
afterAll(() => rmSync(root, { recursive: true, force: true }));
const databases: Database[] = [];
afterEach(() => {
  for (const db of databases.splice(0)) db.close();
});
let serial = 0;
export function fixture(): Database {
  const path = join(root, `test-${serial++}.sqlite`);
  copyFileSync(seed, path);
  const db = createDatabase(path);
  db.read(load_worker_runtime_definitions);
  databases.push(db);
  return db;
}
export const now = 1783166400;
export const worker = (id: string): Principal => ({ kind: 'ticket', id });
export function create(
  db: Database,
  o: Partial<data.CreationOptions> & { settle_kickoff?: boolean } = {},
): Ticket {
  const { settle_kickoff = true, ...overrides } = o;
  let t = db.write((tx) =>
    data.create_ticket(tx, {
      title: 'Test ticket',
      worker_type: 'coding',
      brief: 'Agreed brief.',
      principal: OWNER,
      now,
      title_max_chars: 200,
      ...overrides,
    }),
  );
  if (settle_kickoff) t = accept(db, t, 'brief');
  return t;
}
export function read(db: Database, id: string): Ticket {
  return db.read((sql) => data.read_ticket(sql, id));
}
export function edit(
  db: Database,
  t: Ticket,
  edit: TicketEdit,
  principal: Principal = OWNER,
  time = now,
  notices?: Parameters<typeof data.edit_ticket>[2]['review_transfer_notices'],
): Ticket {
  return db.write((tx) =>
    data.edit_ticket(tx, t.id, {
      edit,
      title_max_chars: 200,
      principal,
      now: time,
      review_transfer_notices: notices,
    }),
  );
}
export function propose(db: Database, t: Ticket, body: string, time = now): Ticket {
  return db.write((tx) =>
    data.file_current_proposal(tx, t.id, { body, principal: worker(t.id), now: time }),
  );
}
export function accept(
  db: Database,
  t: Ticket,
  field: string,
  next_ceiling: string | null = 'none',
  edited_body: string | null = null,
  next_holder: Principal = OWNER,
  time = now,
): Ticket {
  return db.write((tx) =>
    data.accept_proposal(tx, t.id, {
      field,
      principal: OWNER,
      now: time,
      next_ceiling,
      next_holder,
      edited_body,
    }),
  );
}
export function advance(db: Database, t: Ticket, new_stage = 'done'): Ticket {
  t = edit(db, t, { ceiling: 'done' });
  while (t.stage !== new_stage) {
    if (t.pending_proposal) t = accept(db, t, t.pending_proposal.field, 'done');
    else t = propose(db, t, 'Advance');
  }
  return t;
}
export function row(db: Database, id: string): unknown {
  return db.read((sql) => sql.get('SELECT * FROM tickets WHERE id=?', [id]));
}
export function dump(db: Database): unknown {
  return db.read((sql) =>
    sql
      .all<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
      )
      .map((r) => [r.name, sql.all(`SELECT * FROM "${r.name}" ORDER BY rowid`)]),
  );
}
export function manager(db: Database, title: string): string {
  const id = `si_test_${serial++}`;
  db.write((tx) =>
    tx.run(
      "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,created_at,updated_at) VALUES (?,?,'project_vylo','codex','gpt-6.1-sol',?,?)",
      [id, title, now, now],
    ),
  );
  return id;
}
export function claim(db: Database, id: string, time = now): Ticket | null {
  return db.write((tx) =>
    data.claim_ticket_for_worker_step(tx, id, {
      now: time,
      readiness_check: (_sql: ReadSql, t: Ticket) =>
        !t.is_backlogged &&
        t.ticket_status === 'empty' &&
        !t.workflow.stages.find((s) => s.id === t.stage)!.is_terminal &&
        t.workflow.stages.find((s) => s.id === t.stage)!.ownership_mode === 'worker',
    }),
  );
}
