/** Export-only private-file sequence. No executor transport or provider process. */
import { copyFile, readFile, readdir, unlink, utimes, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { existsSync, fstatSync, readFileSync, watch, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import type { SpawnOptions, ChildProcess } from 'node:child_process';
import { ChildProcesses } from '../../../../lifecycle/owned_processes.ts';
import { FileUpdateAttemptStore, UpdateConflict } from '../../attempts.ts';
import { canonical_json, parse_attempt, parse_attempt_json, parse_release } from '../../codec.ts';
import type { UpdateAttempt, PublishedUpdate, UpdateProvider } from '../../contracts.ts';
import { MacUpdateService } from '../service.ts';
import type { MacUpdateReport, MacUpdateHealthRead } from '../contracts.ts';
interface Inspection {
  release: Record<string, unknown>;
  artifact: string;
  mode?: 'eof' | 'ebadf' | 'unexpected';
  proposed?: boolean;
}

interface Operation {
  action: string;
  key?: string | null;
  sha?: string;
  provider?: boolean;
  id?: string;
  report?: MacUpdateReport;
  health?: MacUpdateHealthRead | null;
}
interface Request {
  root: string;
  releases: Record<string, Record<string, unknown>>;
  attempts: Record<string, Record<string, unknown>>;
  artifacts: Record<string, string>;
  operations: Operation[];
  inspections: Record<string, Inspection>;
}
export async function mac_update_sequence(request: Request): Promise<unknown> {
  const owner = new ChildProcesses(),
    native_spawn = owner.spawn.bind(owner);
  const entry = fileURLToPath(new URL('../../../release/archive_unpack_main.ts', import.meta.url));
  let spawned = 0,
    closed = 0,
    stopping = false;
  let shutdown_failure: unknown;
  const work = new Set<string>();
  const descriptors = new Set<number>();
  const actual_close = () => {
    if (closed !== spawned || [...work].some((path) => existsSync(path)))
      throw new Error('Mac inspection returned before actual close/cleanup');
    for (const fd of descriptors) {
      try {
        fstatSync(fd);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'EBADF') continue;
        throw error;
      }
      throw new Error('Mac inspection retained its parent archive descriptor');
    }
    descriptors.clear();
  };
  let active:
    | {
        mode?: Inspection['mode'];
        entered: boolean;
        forced: boolean;
        identity: boolean;
        error: string | null;
      }
    | undefined;
  const terminate = () => {
    stopping = true;
    owner.seal();
    try {
      owner.force_kill(performance.now() / 1000 + 5);
    } catch (error) {
      shutdown_failure = error;
    }
  };
  process.on('SIGTERM', terminate);
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (
      command !== process.execPath ||
      args[0] !== entry ||
      !Array.isArray(options.stdio) ||
      typeof options.stdio[3] !== 'number'
    )
      throw new Error('Unexpected Mac inspection launch');
    const descriptor = fstatSync(options.stdio[3]);
    descriptors.add(options.stdio[3]);
    work.add(String(options.cwd));
    const input = JSON.parse(args[1]!);
    if (
      input.purpose !== 'mac-inspection' ||
      input.spool_root !== options.cwd ||
      'destination' in input
    )
      throw new Error('Mac inspection lost its no-spool purpose');
    const evidence = active;
    const marker = join(String(options.cwd), 'inspection-marker');
    let child: ChildProcess | undefined, timer: ReturnType<typeof setTimeout> | undefined;
    let child_closed = false;
    const observed = () => {
      if (!child || child_closed || !evidence || !existsSync(marker) || evidence.entered) return;
      try {
        const actual = JSON.parse(readFileSync(marker, 'utf8'));
        evidence.identity = actual.dev === descriptor.dev && actual.ino === descriptor.ino;
        evidence.entered = true;
      } catch (error) {
        evidence.error = String(error);
      }
      child.stdin!.end();
    };
    const watcher = evidence?.mode === 'eof' ? watch(String(options.cwd), observed) : undefined;
    let launch = args;
    try {
      if (evidence?.mode) {
        const preload = join(request.root, 'inspection-preload.mjs');
        writeFileSync(
          preload,
          `import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
const mode=${JSON.stringify(evidence.mode)}, marker=${JSON.stringify(marker)};
const original=fs.createReadStream; let first=true;
fs.createReadStream=function(...args){
  if(first&&args[1]?.fd===3){first=false;
    if(mode==='ebadf')fs.closeSync(3);
    if(mode==='unexpected')throw new Error('Recorded inspection failure');
    if(mode==='eof'){
      const stream=Reflect.apply(original,this,args); let marked=false;
      stream._read=()=>{if(!marked){marked=true;const s=fs.fstatSync(3);fs.writeFileSync(marker+'.tmp',JSON.stringify({dev:s.dev,ino:s.ino}));fs.renameSync(marker+'.tmp',marker);}};
      return stream;
    }
  }
  return Reflect.apply(original,this,args);
};
syncBuiltinESMExports();\n`,
        );
        launch = ['--import', pathToFileURL(preload).href, ...args];
      }
      child = native_spawn(command, launch, options);
      spawned++;
      child.once('close', () => {
        child_closed = true;
        closed++;
        clearTimeout(timer);
        watcher?.close();
      });
      if (evidence?.mode === 'eof') {
        timer = setTimeout(() => {
          evidence.forced = true;
          try {
            owner.force_kill(performance.now() / 1000 + 5);
          } catch (error) {
            shutdown_failure = error;
          }
        }, 5000);
        observed();
      }
      return child;
    } catch (error) {
      watcher?.close();
      throw error;
    }
  }) as typeof owner.spawn;
  try {
    let emitted = 0,
      ready: PublishedUpdate | null = null;
    const downloads: unknown[] = [];
    const attempts = new FileUpdateAttemptStore(
      join(request.root, 'update-attempt.json'),
      () => emitted++,
    );
    const releases = Object.fromEntries(
      Object.entries(request.releases).map(([key, value]) => [key, parse_release(value)]),
    );
    const records = Object.fromEntries(
      Object.entries(request.attempts).map(([key, value]) => [key, parse_attempt(value)]),
    );
    const provider: UpdateProvider = {
      available: async () => null,
      async download_artifact(id, destination) {
        downloads.push(id);
        await copyFile(request.artifacts[canonical_json(id)]!, destination);
      },
    };
    const root = join(request.root, 'mac-updates');
    const make = (sha: string, available: boolean) =>
      new MacUpdateService({
        root,
        attempts,
        provider: available ? provider : null,
        current_app_sha: sha,
        child_processes: owner,
        ready_release: () => ready,
        emit_change: () => emitted++,
      });
    let service = make('a'.repeat(40), true);
    const output: unknown[] = [];
    for (const operation of request.operations) {
      if (stopping) throw new Error('Mac fixture terminated');
      try {
        let result: unknown = null;
        switch (operation.action) {
          case 'prepare':
            await service.prepare(releases[operation.key!]!);
            break;
          case 'write':
            await unlink(attempts.path).catch((error: NodeJS.ErrnoException) => {
              if (error.code !== 'ENOENT') throw error;
            });
            attempts.create(records[operation.key!]! as UpdateAttempt);
            break;
          case 'ready':
            ready = operation.key == null ? null : releases[operation.key]!;
            break;
          case 'restart':
            service = make(operation.sha!, operation.provider!);
            break;
          case 'check':
            result = service.check();
            break;
          case 'status':
            result = service.status();
            break;
          case 'report':
            result = service.report(operation.report!, operation.health ?? null);
            break;
          case 'phase':
            result = service.phase(operation.id!);
            break;
          case 'report_evidence': {
            const path = join(
              root,
              'reports',
              createHash('sha256').update(operation.id!).digest('hex') + '.json',
            );
            await utimes(path, 1_800_000_000, 1_800_000_000);
            result = {
              bytes: await readFile(path, 'utf8'),
              reported_at: service.reported_at(operation.id!),
            };
            break;
          }
          case 'archive':
            result = createHash('sha256')
              .update(await readFile(service.archive(operation.id!)))
              .digest('hex');
            break;
          case 'prefetch':
            result = createHash('sha256')
              .update(await readFile(service.prefetch_archive(operation.id!)))
              .digest('hex');
            break;
          case 'expire_applied': {
            const path = join(root, 'applied.json');
            const value = parse_attempt_json(await readFile(path, 'utf8')) as {
              release: { expires_at: string };
            };
            value.release.expires_at = '2000-01-01T00:00:00Z';
            await writeFile(path, canonical_json(value));
            break;
          }
          case 'archives':
            result = (await readdir(join(root, 'archives'))).sort();
            break;
          default:
            throw new Error('unknown Mac comparison operation');
        }
        output.push({ result });
      } catch (error) {
        output.push({
          error: error instanceof UpdateConflict ? 'UpdateConflict' : (error as Error).name,
          message: (error as Error).message,
        });
      }
      actual_close();
    }
    const parity = { output, downloads: [...downloads], emitted };
    const inspections: Record<string, unknown> = {};
    for (const [name, spec] of Object.entries(request.inspections)) {
      if (stopping) throw new Error('Mac fixture terminated');
      const service = new MacUpdateService({
        root: join(request.root, name),
        attempts,
        current_app_sha: 'a'.repeat(40),
        child_processes: owner,
        ready_release: () => null,
        emit_change: () => {},
        provider: {
          available: async () => null,
          download_artifact: async (_id, path) => {
            await copyFile(spec.artifact, path);
          },
        },
      });
      active = { mode: spec.mode, entered: false, forced: false, identity: false, error: null };
      let outcome: unknown = { result: null };
      const before = (await readdir(request.root)).sort();
      try {
        await service.prepare(parse_release(spec.release));
      } catch (error) {
        outcome = {
          error: error instanceof UpdateConflict ? 'UpdateConflict' : (error as Error).name,
          message: (error as Error).message,
          ...('errno' in Object(error)
            ? {
                errno: (error as NodeJS.ErrnoException).errno,
                code: (error as NodeJS.ErrnoException).code,
              }
            : {}),
        };
      }
      actual_close();
      const after = (await readdir(request.root))
        .filter((path) => ![name, 'inspection-preload.mjs'].includes(path))
        .sort();
      if (
        JSON.stringify(before.filter((path) => path !== 'inspection-preload.mjs')) !==
        JSON.stringify(after)
      )
        throw new Error('Mac inspection changed unrelated fixture state');
      inspections[name] = { outcome, evidence: active, proposed: spec.proposed === true };
      active = undefined;
    }
    return { parity, inspections, children: { spawned, closed } };
  } finally {
    owner.seal();
    try {
      try {
        owner.force_kill(performance.now() / 1000 + 5);
      } finally {
        await owner.settled();
      }
    } finally {
      process.off('SIGTERM', terminate);
      await unlink(join(request.root, 'inspection-preload.mjs')).catch(
        (error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOENT') throw error;
        },
      );
    }
    if (shutdown_failure) throw shutdown_failure;
  }
}
