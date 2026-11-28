// Unpack the hosted candidate archive and admit it with the candidate's own release code.
// Run from the candidate checkout: node unpack_and_admit.ts SERVER_DIR ARCHIVE DESTINATION SHA OUT_JSON
import { createHash } from 'node:crypto';
import { createReadStream, statSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const [server, archive, destination, sha, out] = process.argv.slice(2) as [string, string, string, string, string];
const load = (path: string) => import(pathToFileURL(`${server}/${path}`).href);
const { extract_app_archive } = await load('environments/release/archive.ts');
const { admit_runtime } = await load('environments/release/admission.ts');
const { ChildProcesses } = await load('lifecycle/owned_processes.ts');
const digest = await new Promise<string>((resolve, reject) => {
  const hash = createHash('sha256');
  createReadStream(archive).on('data', (chunk) => hash.update(chunk)).on('end', () => resolve(hash.digest('hex'))).on('error', reject);
});
const children = new ChildProcesses();
let started = performance.now();
const manifest = await extract_app_archive(archive, destination, digest, sha, children, undefined, { child_processes: children, signal: new AbortController().signal, deadline: null });
const extract_seconds = (performance.now() - started) / 1000;
started = performance.now();
await admit_runtime(destination, manifest, children);
const admission_seconds = (performance.now() - started) / 1000;
const result = { archive_sha256: digest, archive_bytes: statSync(archive).size, app_sha: manifest.app_sha, extract_seconds, admission_seconds, admitted: true };
writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result);
