// Open a COPY of the live metrics sidecar with the candidate's own Store (from a checkout), and report tables.
// Usage: node sidecar_open.mjs CHECKOUT SIDECAR_COPY OUT_JSON
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
const [checkout, path, out] = process.argv.slice(2);
if (path.startsWith('/home/vps/Deployments/Panels/current/data/')) throw new Error('never the live sidecar');
const Database = createRequire(checkout + '/server/core/database/index.ts')('better-sqlite3');
const tables = () => { const d = new Database(path, { readonly: true }); try { return { user_version: d.pragma('user_version', { simple: true }), tables: d.prepare("select name from sqlite_master where type='table' order by name").all().map((r) => r.name), indexes: d.prepare("select name from sqlite_master where type='index' and name like '%stream_closes%' or name like 'client_events%' order by name").all().map((r) => r.name) }; } finally { d.close(); } };
const before = tables();
const { Store } = await import(pathToFileURL(checkout + '/server/environments/vps_metrics/data/store.ts').href);
const store = new Store(path);
const started = performance.now();
store.initialize();
const initialize_ms = performance.now() - started;
store.close();
const after = tables();
const result = { before, after, initialize_ms };
writeFileSync(out, JSON.stringify(result, null, 1));
console.log(JSON.stringify(result, null, 1));
