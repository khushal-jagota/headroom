// Pack the screens into the app build: the `web/` build output, the `assets/` and
// `static/` files that its index.html loads, and the address page.
import { cpSync, existsSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = join(dirname(fileURLToPath(import.meta.url)), '..');
const repository = join(desktop, '..');
const packed = process.argv[2] ? resolve(process.argv[2]) : join(desktop, 'build/packed');

if (!existsSync(join(repository, 'web/dist/index.html')))
  throw new Error('web/dist is missing. Build web/ first: npm --prefix web run build');

rmSync(packed, { recursive: true, force: true });
cpSync(join(repository, 'web/dist'), join(packed, 'web/dist'), { recursive: true });
cpSync(join(repository, 'assets'), join(packed, 'assets'), { recursive: true });
cpSync(join(repository, 'static'), join(packed, 'static'), { recursive: true });
cpSync(join(desktop, 'server_update_page'), join(packed, 'server_update_page'), { recursive: true });
