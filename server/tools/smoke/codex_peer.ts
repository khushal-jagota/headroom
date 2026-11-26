/** The scripted Codex app server that answers the smoke's conversation on the default backend. */
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** What the scripted app server answers a turn that names no scripted case. */
export const DEFAULT_BACKEND_PEER_REPLY = 'native output';

/** Writes the scripted executable on the private account’s machine PATH, with private traces. */
export async function write_default_backend_peer(
  root: string,
  source: string,
  bin: string,
): Promise<void> {
  const peer = pathToFileURL(join(source, 'server/drivers/codex/testing/scripted_app_server.ts'));
  const environment = {
    UNIT17_TRACE: join(root, 'codex-transcript.jsonl'),
    UNIT17_CORE_PROOF: '1',
    UNIT17_CATALOG_MODEL: 'TEST',
    UNIT17_WORKER_ARTIFACTS: join(
      root,
      'installed/data/files/tickets/t_update_qualification/artifacts',
    ),
  };
  const requester_trace =
    process.platform === 'darwin' && process.env.QUALIFY_MACHINE_SERVICE === '1'
      ? `
if (process.argv.includes('app-server')) {
  const { execFileSync } = await import('node:child_process');
  const { appendFileSync } = await import('node:fs');
  for (let pid = process.ppid; pid > 1;) {
    const row = execFileSync('/bin/ps', ['-p', String(pid), '-o', 'ppid=,comm='],
      { encoding: 'utf8', timeout: 2000 }).trim();
    const match = /^(\\d+)\\s+(.+)$/.exec(row);
    if (!match) break;
    if (match[2].endsWith('.app/Contents/MacOS/Panels')) {
      appendFileSync(${JSON.stringify(join(root, 'evidence/mac-worker-requesters.jsonl'))},
        JSON.stringify({ pid, executable: match[2] }) + '\\n');
      break;
    }
    pid = Number(match[1]);
  }
}
`
      : '';
  await writeFile(
    join(bin, 'codex'),
    `#!${process.execPath}\n` +
      `Object.assign(process.env, ${JSON.stringify(environment)});\n` +
      requester_trace +
      `await import(${JSON.stringify(peer.href)});\n`,
    { flag: 'wx', mode: 0o700 },
  );
}
