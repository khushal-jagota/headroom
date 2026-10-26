import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import { spawn } from 'node:child_process';
import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { build } from 'vite';
import { scratchDirectory } from './support/scratch.mjs';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const root = join(webRoot, '..');
const scratch = await scratchDirectory();
const out = await mkdtemp(join(tmpdir(), 'panels-navigation-browser-'));
const entry = join(scratch, `navigation-entry-${process.pid}.ts`);
const html = join(scratch, `navigation-index-${process.pid}.html`);
try {
  await writeFile(entry, `import {mount} from 'svelte'; import Host from '../tests/support/navigation/Host.svelte'; mount(Host,{target:document.getElementById('app')});`);
  await writeFile(html, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${entry.split('/').at(-1)}"></script></body></html>`);
  const built = await build({ root:webRoot, base:'./', configFile:false, logLevel:'warn', plugins:[svelte()],
    build:{emptyOutDir:true,outDir:out,rollupOptions:{input:html}} });
  const chunks = (Array.isArray(built) ? built : [built]).flatMap(result => result.output);
  const gzipBundleBytes = chunks.reduce((sum,chunk) => sum + (chunk.type === 'chunk' ? gzipSync(chunk.code).byteLength : 0),0);
  const bundleBytes = chunks.reduce((sum,chunk) => sum + (chunk.type === 'chunk' ? Buffer.byteLength(chunk.code) : 0),0);
  const index = (await readdir(out,{recursive:true})).find(path => path.endsWith('.html'));
  assert.ok(index);
  const result = await new Promise((resolve,reject) => {
    const child = spawn(join(root,'.venv/bin/python'), ['scripts/navigation_browser_harness.py','--static-dir',out,'--index',index,
      '--runtime',`data/verification/navigation-browser-runtime-${process.pid}`,
      '--output','data/verification/navigation-browser-evidence.json'], {cwd:root,stdio:'inherit'});
    child.on('error',reject); child.on('exit',code => resolve(code));
  });
  assert.equal(result,0);
  await writeFile(join(root,'data/verification/navigation-harness-bundle.json'),JSON.stringify({
    bundleBytes,gzipBundleBytes,description:'Entire test host + Svelte + adapter/owner bundle, not production incremental bundle or original-browser comparison'
  },null,2));
} finally {
  await Promise.all([rm(out,{recursive:true,force:true}),rm(entry,{force:true}),rm(html,{force:true})]);
}
