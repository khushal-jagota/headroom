// PROBE ONLY. Research ticket "Prove server-delivered app screens". Not product code.
//
// When the data folder holds `screens-probe.json`, the app runs a fixed sequence: it
// downloads screens payloads from a test server into `<userData>/screens/<digest>/`,
// checks every file against the payload digest, swaps the scheme's root at a window
// reload, falls back after a broken payload, goes back on request, and checks the copy
// on disk again at a cold start. It writes what it saw to `<userData>/probe-report.json`.
// It adds no bridge call. It reads the page only from the main process.
import { app, net, type BrowserWindow } from 'electron';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { setScreensNoStore, setScreensRoot } from './app_scheme';

type ProbeConfig = { server: string; run: number; noStore?: boolean };
type ManifestFile = { path: string; sha256: string };
type Manifest = { name: string; digest: string; files: ManifestFile[] };
type State = { current: string | null; previous: string | null };
type Step = Record<string, unknown> & { step: string; ok: boolean };

const userData = () => app.getPath('userData');
const screensDir = () => join(userData(), 'screens');
const statePath = () => join(screensDir(), 'state.json');

export function probeConfig(): ProbeConfig | null {
  try {
    const config = JSON.parse(readFileSync(join(userData(), 'screens-probe.json'), 'utf8')) as ProbeConfig;
    setScreensNoStore(config.noStore === true);
    return config;
  } catch {
    return null;
  }
}

function readState(): State {
  try {
    return JSON.parse(readFileSync(statePath(), 'utf8')) as State;
  } catch {
    return { current: null, previous: null };
  }
}

/** Write the pointer file atomically: a whole new file, then a rename over the old one. */
function writeState(state: State): void {
  mkdirSync(screensDir(), { recursive: true });
  writeFileSync(`${statePath()}.next`, JSON.stringify(state, null, 2));
  renameSync(`${statePath()}.next`, statePath());
}

function sha256(data: Buffer | string): string {
  return createHash('sha256').update(data).digest('hex');
}

/** The payload digest: SHA256 over sorted "<file sha256>  <path>" lines, as `shasum` prints them. */
function payloadDigest(files: ManifestFile[]): string {
  const lines = [...files]
    .sort((a, b) => (a.path < b.path ? -1 : 1))
    .map((f) => `${f.sha256}  ${f.path}\n`)
    .join('');
  return sha256(lines);
}

function listFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else out.push(relative(root, full).split(sep).join('/'));
    }
  };
  walk(root);
  return out;
}

/** Check a copy on disk against its own name, which is its digest. */
export function verifyCopy(digest: string): { ok: boolean; problem?: string } {
  const root = join(screensDir(), digest);
  if (!existsSync(root)) return { ok: false, problem: 'missing' };
  const files = listFiles(root).map((path) => ({ path, sha256: sha256(readFileSync(join(root, path))) }));
  const actual = payloadDigest(files);
  return actual === digest ? { ok: true } : { ok: false, problem: `digest ${actual} on disk` };
}

/** Pick the copy to serve at start: current if it checks out, else previous, else the bundle. */
export function chooseAtStart(): { served: string; reason: string } {
  const state = readState();
  for (const candidate of [state.current, state.previous]) {
    if (candidate && verifyCopy(candidate).ok) {
      setScreensRoot(join(screensDir(), candidate));
      return { served: candidate, reason: candidate === state.current ? 'current' : 'previous after a failed check' };
    }
  }
  setScreensRoot(null);
  return { served: 'bundle', reason: 'no copy on disk checks out' };
}

async function download(server: string, name: string): Promise<{ manifest: Manifest; ms: number }> {
  const started = Date.now();
  const manifest = (await (await net.fetch(`${server}/screens/${name}/manifest.json`)).json()) as Manifest;
  if (payloadDigest(manifest.files) !== manifest.digest) throw new Error('manifest digest does not match its files');
  const final = join(screensDir(), manifest.digest);
  if (existsSync(final) && verifyCopy(manifest.digest).ok) return { manifest, ms: Date.now() - started };
  const staging = join(screensDir(), `.staging-${manifest.digest}`);
  rmSync(staging, { recursive: true, force: true });
  for (const file of manifest.files) {
    if (file.path.split('/').includes('..') || file.path.startsWith('/')) throw new Error(`bad path ${file.path}`);
    const response = await net.fetch(`${server}/screens/${name}/files/${file.path}`);
    if (!response.ok) throw new Error(`${file.path}: ${response.status}`);
    const body = Buffer.from(await response.arrayBuffer());
    if (sha256(body) !== file.sha256) throw new Error(`${file.path}: checksum differs`);
    const target = join(staging, file.path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, body);
  }
  rmSync(final, { recursive: true, force: true });
  renameSync(staging, final);
  return { manifest, ms: Date.now() - started };
}

function xattrs(digest: string): string {
  try {
    return execFileSync('xattr', ['-lr', join(screensDir(), digest)], { encoding: 'utf8' }).slice(0, 2000);
  } catch (error) {
    return `xattr failed: ${(error as Error).message}`;
  }
}

function forceQuarantine(digest: string): string {
  const value = `0083;${Math.floor(Date.now() / 1000).toString(16)};Safari;`;
  try {
    execFileSync('xattr', ['-r', '-w', 'com.apple.quarantine', value, join(screensDir(), digest)]);
    return 'set';
  } catch (error) {
    return `failed: ${(error as Error).message}`;
  }
}

/** Reload the window and wait for the page to finish, collecting console errors on the way. */
async function reloadAndRead(window: BrowserWindow, expectReal: boolean): Promise<Record<string, unknown>> {
  const errors: string[] = [];
  const onConsole = (...args: unknown[]) => {
    const first = args[0] as { level?: string; message?: string } | undefined;
    const level = first?.level ?? args[1];
    const message = first?.message ?? args[2];
    if (level === 'error' || level === 3 || level === 'warning' || level === 2) errors.push(String(message));
  };
  const failures: string[] = [];
  const onFail = (_e: unknown, code: number, description: string, url: string) => failures.push(`${code} ${description} ${url}`);
  window.webContents.on('console-message', onConsole as never);
  window.webContents.on('did-fail-load', onFail as never);
  const finished = new Promise<void>((resolve) => window.webContents.once('did-finish-load', () => resolve()));
  window.webContents.reload();
  await finished;
  let marker: unknown = null;
  let bodyText = 0;
  for (let i = 0; i < 40; i += 1) {
    marker = await window.webContents.executeJavaScript('window.__screens ?? null', true).catch(() => null);
    bodyText = await window.webContents
      .executeJavaScript('document.body ? document.body.innerText.trim().length : 0', true)
      .catch(() => 0);
    if (marker || (expectReal && bodyText > 0)) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  if (expectReal) await new Promise((r) => setTimeout(r, 1500));
  window.webContents.off('console-message', onConsole as never);
  window.webContents.off('did-fail-load', onFail as never);
  return { url: window.webContents.getURL(), marker, bodyText, consoleErrors: errors.slice(0, 20), loadFailures: failures };
}

async function shot(window: BrowserWindow, name: string): Promise<void> {
  const image = await window.webContents.capturePage().catch(() => null);
  if (!image) return;
  mkdirSync(join(userData(), 'probe-shots'), { recursive: true });
  writeFileSync(join(userData(), 'probe-shots', `${name}${probeNoStore ? '-nostore' : ''}.png`), image.toPNG());
}

function serve(digest: string | null): void {
  setScreensRoot(digest ? join(screensDir(), digest) : null);
}

const healthy = (seen: Record<string, unknown>, name: string) =>
  (seen.marker as { version?: string } | null)?.version === name;

let probeNoStore = false;
export async function runProbe(window: BrowserWindow, config: ProbeConfig, atStart: { served: string; reason: string }): Promise<void> {
  probeNoStore = config.noStore === true;
  const steps: Step[] = [];
  const report = () =>
    writeFileSync(
      join(userData(), `probe-report-run${config.run}${config.noStore ? '-nostore' : ''}.json`),
      JSON.stringify({ noStore: config.noStore === true, electron: process.versions.electron, chrome: process.versions.chrome, run: config.run, atStart, steps }, null, 2),
    );
  try {
    await new Promise<void>((resolve) =>
      window.webContents.isLoading() ? window.webContents.once('did-finish-load', () => resolve()) : resolve(),
    );
    if (config.run === 1) {
      const base = await reloadAndRead(window, true);
      steps.push({ step: 'baseline from the bundle', ok: (base.bodyText as number) > 0, ...base });
      await shot(window, 'run1-0-baseline');

      const install = async (name: string, quarantine = false) => {
        const { manifest, ms } = await download(config.server, name);
        const q = quarantine ? forceQuarantine(manifest.digest) : 'not forced';
        const check = verifyCopy(manifest.digest);
        return { manifest, ms, q, check };
      };
      // Swap to A, then to B, at a reload.
      for (const [name, quarantine] of [['A', false], ['B', true]] as const) {
        const { manifest, ms, q, check } = await install(name, quarantine);
        const before = readState();
        writeState({ current: manifest.digest, previous: before.current });
        serve(manifest.digest);
        const seen = await reloadAndRead(window, false);
        steps.push({
          step: `swap to ${name} at a reload`,
          ok: healthy(seen, name) && check.ok,
          downloadMs: ms,
          digest: manifest.digest,
          quarantine: q,
          xattrs: xattrs(manifest.digest),
          ...seen,
        });
        await shot(window, `run1-${name}`);
      }
      // C is broken: its index.html loads a script that is missing. The check fails and the app goes back.
      {
        const { manifest, check } = await install('C');
        const before = readState();
        writeState({ current: manifest.digest, previous: before.current });
        serve(manifest.digest);
        const seen = await reloadAndRead(window, false);
        const failed = !healthy(seen, 'C');
        writeState(before);
        serve(before.current);
        const back = await reloadAndRead(window, false);
        steps.push({ step: 'broken C fails its check, app goes back to B', ok: failed && healthy(back, 'B'), cCheck: check, cSeen: seen, back });
        await shot(window, 'run1-C-then-back');
      }
      // Go back on request: previous becomes current.
      {
        const before = readState();
        writeState({ current: before.previous, previous: before.current });
        serve(before.previous);
        const seen = await reloadAndRead(window, false);
        steps.push({ step: 'go back to the previous copy (A) on request', ok: healthy(seen, 'A'), ...seen });
        await shot(window, 'run1-back-to-A');
      }
      // The real screens of this build, delivered as a payload.
      {
        const { manifest, ms, check } = await install('R');
        const before = readState();
        writeState({ current: manifest.digest, previous: before.current });
        serve(manifest.digest);
        const seen = await reloadAndRead(window, true);
        steps.push({ step: 'swap to the real screens (R) from the data folder', ok: (seen.bodyText as number) > 0 && check.ok, downloadMs: ms, digest: manifest.digest, ...seen });
        await shot(window, 'run1-R');
        // Leave A as current for the cold start of run 2.
        const now = readState();
        writeState({ current: now.previous, previous: now.current });
      }
    } else {
      const seen = await reloadAndRead(window, false);
      steps.push({ step: 'cold start serves the stored current copy (A)', ok: atStart.reason === 'current' && healthy(seen, 'A'), ...seen });
      await shot(window, 'run2-cold-start');
      // Tamper with the current copy on disk. The start check must refuse it and serve previous (R).
      const state = readState();
      const victim = join(screensDir(), state.current ?? 'none', 'web/dist/index.html');
      writeFileSync(victim, readFileSync(victim, 'utf8') + '\n<!-- tampered -->\n');
      const chosen = chooseAtStart();
      const after = await reloadAndRead(window, true);
      steps.push({ step: 'a changed file in current fails the start check, app serves previous', ok: chosen.served === state.previous, chosen, ...after });
      await shot(window, 'run2-after-tamper');
    }
  } catch (error) {
    steps.push({ step: 'probe error', ok: false, error: (error as Error).stack });
  }
  report();
  app.quit();
}
