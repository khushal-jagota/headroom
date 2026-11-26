// Runs the sealed release's real schema_step_main.ts, with timers around SQLite calls only.
// Usage: APP/node/bin/node schema_timed.mjs APP DATABASE backup|no-backup EVENTS_JSON
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { writeFileSync } from 'node:fs';
const [app, database, mode, out] = process.argv.slice(2);
const entry = app + '/server/environments/updates/schema_step_main.ts';
const Database = createRequire(app + '/server/core/database/index.ts')('better-sqlite3');
const events = [];
const now = () => performance.now();
const keep_args = (sql) => /pragma_integrity_check|foreign_key|alembic_version/.test(sql);
const probe = new Database(':memory:');
const Statement = probe.prepare('select 1').constructor;
probe.close();
for (const method of ['run', 'all', 'get', 'iterate']) {
  const original = Statement.prototype[method];
  Statement.prototype[method] = function (...args) {
    const start = now();
    try {
      return original.apply(this, args);
    } finally {
      events.push({ kind: 'statement', method, sql: this.source.slice(0, 200),
        args: keep_args(this.source) ? args.map(String) : undefined, start, ms: now() - start });
    }
  };
}
for (const method of ['exec', 'pragma']) {
  const original = Database.prototype[method];
  Database.prototype[method] = function (sql, ...rest) {
    const start = now();
    try {
      return original.call(this, sql, ...rest);
    } finally {
      events.push({ kind: method, sql: String(sql).slice(0, 200), start, ms: now() - start });
    }
  };
}
const started = now();
process.on('exit', (code) => {
  writeFileSync(out, JSON.stringify({ exit_code: code, total_ms: now() - started, events }, null, 1));
});
process.argv = [process.argv[0], entry, database, mode];
await import(pathToFileURL(entry).href);
