/** Export-only identity/archive proof. Inputs are the driver's private files. */
import { lstat, readlink, stat, readdir, readFile, mkdir, copyFile } from 'node:fs/promises';
import {
  fstatSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpawnOptions } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { createReadAccess } from '../../../core/database/index.ts';
import type { ElfObject, MigrationGraph } from '../contracts.ts';
import {
  APP_FORMAT,
  ARCHIVE_SPACE_RESERVE_BYTES,
  ARCHIVE_SPOOL_BATCH_BYTES,
} from '../configuration.ts';
import { create_app_archive, extract_app_archive, hash_archive } from '../archive.ts';
import { digest_app_artifact, digest_app_source, validate_app_manifest } from '../identity.ts';
import { require_node_database_frontier, schema_change_pending } from '../migrations.ts';
import { AppValidationError } from '../contracts.ts';

import { parse_dynamic } from '../native/elf.ts';
import { resolve_provider } from '../native/providers.ts';
import { RuntimeContractError } from '../../updates/logic/runtime_contract.ts';
import { complete_node_runtime } from '../native_inventory.ts';
import { require_host_node, verify_runtime_host_facts } from '../logic/host.ts';
import type { RuntimeHostFacts } from '../logic/host.ts';
import { validate_node_probe_report } from '../admission.ts';
import { run_runtime_probe } from '../admission_process.ts';

/** Hosted-only caller pins both recorded handles before it releases this timeout callback. */
export async function admission_group_parity(root: string): Promise<unknown> {
  const owner = new ChildProcesses(),
    spawn = owner.spawn.bind(owner);
  const original_timer = globalThis.setTimeout;
  let timeout: (() => void) | undefined, timer_delay: number | undefined;
  let flight: Promise<string> | undefined;
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    const child = spawn(command, args, options);
    // run_runtime_probe synchronously registers exactly one timer after this spawn returns.
    globalThis.setTimeout = ((
      callback: (...values: unknown[]) => void,
      delay?: number,
      ...values: unknown[]
    ) => {
      globalThis.setTimeout = original_timer;
      timer_delay = delay;
      timeout = () => callback(...values);
      const handle = original_timer(() => {}, delay);
      clearTimeout(handle);
      return handle;
    }) as typeof setTimeout;
    return child;
  }) as typeof owner.spawn;
  const marker = join(root, 'group-identities.json');
  const program = `const fs=require('node:fs'),{spawn}=require('node:child_process');
    const identity=pid=>{const f=fs.readFileSync('/proc/'+pid+'/stat','utf8').split(') ').at(-1).split(' ');
      return {pid,parent:Number(f[1]),start_time:Number(f[19])}};
    const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
    child.once('spawn',()=>{try{fs.writeFileSync(${JSON.stringify(marker + '.tmp')},JSON.stringify({leader:identity(process.pid),descendant:identity(child.pid)}));fs.renameSync(${JSON.stringify(marker + '.tmp')},${JSON.stringify(marker)})}catch(error){child.kill('SIGKILL');throw error}});
    setInterval(()=>{},1000);`;
  try {
    flight = run_runtime_probe(
      [process.execPath, '-e', program],
      root,
      performance.now() / 1000 + 120,
      owner,
    );
    void flight.catch(() => {});
    const until = performance.now() + 20_000;
    while (true) {
      try {
        await stat(join(root, 'release-timeout'));
        break;
      } catch (error) {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
      }
      if (performance.now() >= until)
        throw new Error('Hosted parent did not release the pinned group timeout');
      await new Promise<void>((resolve) => original_timer(resolve, 10));
    }
    if (!timeout) throw new Error('Admission timeout callback was not reserved');
    timeout();
    let message: string | undefined;
    try {
      await flight;
    } catch (error) {
      if (!(error instanceof AppValidationError)) throw error;
      message = error.message;
    }
    if (message !== 'runtime probe failed: command timed out')
      throw new Error('Admission did not reject its timeout');
    return { message, timer_delay, timeout_callback_released_after_pin: true };
  } finally {
    globalThis.setTimeout = original_timer;
    owner.force_kill(performance.now() / 1000 + 5);
    await flight?.catch(() => {});
    await owner.settled();
  }
}

/** Host and report rules consume only synthetic observations from the private driver. */
export async function admission_rules_parity(
  root: string,
  spec: {
    host_cases: { version: 1 | 2; node: string; facts: RuntimeHostFacts }[];
    reports: unknown[];
  },
): Promise<unknown> {
  const v1 = (await validate_app_manifest(join(root, 'app/manifest.json'))).runtime_contract!;
  const v2 = (await validate_app_manifest(join(root, 'v2/manifest.json'))).runtime_contract!;
  if (v2.version !== 2) throw new Error('Node fixture must carry v2');
  const hosts: (string | null)[] = [];
  for (const item of spec.host_cases) {
    const contract = item.version === 1 ? v1 : v2;
    try {
      verify_runtime_host_facts(contract, item.facts);
      require_host_node(item.node, contract);
      hosts.push(null);
    } catch (error) {
      if (!(error instanceof AppValidationError)) throw error;
      hosts.push(error.message);
    }
  }
  const reports: (string | null)[] = [];
  for (const report of spec.reports) {
    try {
      await validate_node_probe_report(join(root, 'v2'), v2, report);
      reports.push(null);
    } catch (error) {
      if (!(error instanceof AppValidationError)) throw error;
      reports.push(error.message);
    }
  }
  return { hosts, reports };
}

export async function native_loader_parity(spec: {
  dynamic: readonly [string, string];
  providers: readonly { name: string; requester: ElfObject; ancestors: readonly ElfObject[] }[];
}): Promise<unknown> {
  const providers = [];
  for (const request of spec.providers) {
    try {
      providers.push({
        provider: await resolve_provider(
          request.name,
          request.requester,
          request.ancestors,
          new Map(),
        ),
      });
    } catch (error) {
      if (!(error instanceof RuntimeContractError)) throw error;
      providers.push({ error: error.message });
    }
  }
  return { dynamic: parse_dynamic(...spec.dynamic), providers };
}

interface ArchiveVector {
  name: string;
  policy: string;
  risk: string;
  expected_sha?: string;
}
interface ArchiveLifetime {
  stopping: boolean;
  active?: ChildProcesses;
  failure?: unknown;
}
async function tree(root: string): Promise<Record<string, unknown>[]> {
  const rows: Record<string, unknown>[] = [],
    inodes = new Map<string, string>();
  const walk = async (relative: string): Promise<void> => {
    for (const name of (await readdir(join(root, relative))).sort()) {
      const path = relative ? relative + '/' + name : name;
      const full = join(root, path),
        info = await lstat(full, { bigint: true });
      const row: Record<string, unknown> = { path, mode: Number(info.mode & 0o7777n) };
      if (info.isSymbolicLink())
        Object.assign(row, { kind: 'symlink', target: await readlink(full) });
      else if (info.isDirectory()) {
        Object.assign(row, { kind: 'directory', mtime_ns: info.mtimeNs.toString() });
        rows.push(row);
        await walk(path);
        continue;
      } else {
        const key = info.dev + ':' + info.ino;
        Object.assign(row, {
          kind: 'file',
          bytes: (await readFile(full)).toString('hex'),
          mtime_ns: info.mtimeNs.toString(),
          inode: key,
        });
      }
      rows.push(row);
    }
  };
  await walk('');
  rows.sort((left, right) =>
    Buffer.compare(Buffer.from(String(left.path)), Buffer.from(String(right.path))),
  );
  for (const row of rows)
    if (row.kind === 'file') {
      const key = String(row.inode);
      row.hardlink_to = inodes.get(key) ?? null;
      if (!inodes.has(key)) inodes.set(key, String(row.path));
      delete row.inode;
    }
  return rows;
}

async function observed_unpack(
  lifetime: ArchiveLifetime,
  root: string,
  name: string,
  source: string,
  checksum: string,
  sha: string,
  mode = 'ordinary',
): Promise<Record<string, unknown>> {
  if (lifetime.stopping) throw new Error('archive fixture stopped before another operation');
  const work = join(root, 'work-' + name);
  await mkdir(work, { mode: 0o700 });
  const input = join(work, 'input.tar.gz'),
    destination = join(work, 'output');
  await copyFile(source, input);
  const compressed_bytes = BigInt((await stat(input)).size);
  const owner = new ChildProcesses(),
    spawn = owner.spawn.bind(owner);
  let spawned = 0,
    closed = 0,
    validated = false,
    space = 0,
    expanded = 0;
  let descriptor_held = true,
    spool_private = true,
    output_private = false;
  let replaced_before_read = false,
    replaced_after_hash = false,
    killed = false;
  let channel_closed = false,
    channel_close_timed_out = false,
    channel_close_ms: number | null = null;
  let fixture_error: unknown;
  const capacity: { phase: string; available: string }[] = [];
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (lifetime.stopping) throw new Error('archive fixture refused spawn after termination');
    const entry = fileURLToPath(new URL('../archive_unpack_main.ts', import.meta.url));
    if (
      command !== process.execPath ||
      args[0] !== entry ||
      !Array.isArray(options.stdio) ||
      typeof options.stdio[3] !== 'number'
    )
      throw new Error('archive fixture refused an unowned/unexpected executable');
    const descriptor = options.stdio[3],
      original = fstatSync(descriptor, { bigint: true });
    const same_descriptor = () => {
      const info = fstatSync(descriptor, { bigint: true });
      return info.ino === original.ino && info.dev === original.dev;
    };
    const request = JSON.parse(args[1]!) as { spool_root: string; destination: string };
    if (request.destination !== destination || dirname(request.spool_root) !== work)
      throw new Error('archive helper escaped its owned fixture directory');
    if (mode === 'replace') {
      unlinkSync(input);
      writeFileSync(input, 'replaced after open');
      replaced_before_read = true;
    }
    const child = spawn(command, args, options);
    spawned++;
    let lines = '';
    const decoder = new StringDecoder('utf8');
    child.stdout!.prependListener('data', (chunk: Buffer) => {
      try {
        lines += decoder.write(chunk);
        let end: number;
        while ((end = lines.indexOf('\n')) !== -1) {
          if (Buffer.byteLength(lines.slice(0, end)) > 64 * 1024)
            throw new Error('unbounded archive fixture record');
          const record = JSON.parse(lines.slice(0, end)) as {
            type: string;
            bytes?: number;
            expanded_bytes?: number;
          };
          lines = lines.slice(end + 1);
          if (record.type === 'space') space = record.bytes!;
          if (record.type === 'validated') {
            validated = true;
            expanded = record.expanded_bytes!;
            descriptor_held &&= same_descriptor();
            spool_private &&= (lstatSync(request.spool_root).mode & 0o777) === 0o700;
            const files = readdirSync(request.spool_root);
            spool_private &&= files.length === 1;
            for (const file of files)
              spool_private &&= (lstatSync(join(request.spool_root, file)).mode & 0o777) === 0o600;
            if (mode === 'replace') {
              unlinkSync(input);
              writeFileSync(input, 'replaced after hash');
              replaced_after_hash = true;
            }
            if (mode === 'appeared') {
              mkdirSync(destination);
              writeFileSync(join(destination, 'sentinel'), 'competitor');
            }
          }
        }
        if (Buffer.byteLength(lines) > 64 * 1024)
          throw new Error('unbounded archive fixture record');
      } catch (error) {
        fixture_error = error;
        try {
          owner.force_kill(performance.now() / 1000 + 5);
        } catch (force) {
          fixture_error = new AggregateError([error, force], 'archive fixture cleanup failed');
        }
      }
    });
    const stdin = child.stdin!;
    const write = stdin.write.bind(stdin);
    let channel_started = 0,
      channel_timer: ReturnType<typeof setTimeout> | undefined;
    stdin.write = ((...values: unknown[]) => {
      if (validated) {
        output_private = (lstatSync(destination).mode & 0o777) === 0o700;
        if (mode === 'kill' && !killed) {
          killed = true;
          owner.force_kill(performance.now() / 1000 + 5);
        }
      }
      const written = Reflect.apply(write, stdin, values);
      if (validated && mode === 'channel-loss' && !channel_closed) {
        channel_closed = true;
        channel_started = performance.now();
        stdin.end();
        channel_timer = setTimeout(() => {
          channel_close_timed_out = true;
          try {
            owner.force_kill(performance.now() / 1000 + 5);
          } catch (error) {
            fixture_error = error;
          }
        }, 5_000);
      }
      return written;
    }) as typeof stdin.write;
    child.once('close', () => {
      if (channel_timer) clearTimeout(channel_timer);
      if (channel_closed) channel_close_ms = performance.now() - channel_started;
      try {
        descriptor_held &&= same_descriptor();
      } catch (error) {
        descriptor_held = false;
        fixture_error = error;
      }
      closed++;
    });
    return child;
  }) as typeof owner.spawn;
  const free = async (): Promise<bigint> => {
    const reserve = BigInt(ARCHIVE_SPACE_RESERVE_BYTES);
    const phase = validated ? 'output' : spawned ? 'spool' : 'initial';
    const required =
      reserve +
      (phase === 'initial'
        ? compressed_bytes * 2n
        : phase === 'spool'
          ? BigInt(space || ARCHIVE_SPOOL_BATCH_BYTES)
          : BigInt(expanded));
    const value = mode === 'low-' + phase ? required - 1n : 1n << 50n;
    capacity.push({ phase, available: value.toString() });
    return value;
  };
  let result: Record<string, unknown>;
  let settled_at_return = false;
  lifetime.active = owner;
  try {
    try {
      const manifest = await extract_app_archive(input, destination, checksum, sha, owner, free);
      settled_at_return = closed === spawned;
      result = {
        status: 'restored',
        manifest: { format: APP_FORMAT, ...manifest },
        tree: await tree(destination),
      };
    } catch (error) {
      settled_at_return = closed === spawned;
      result = {
        status: 'refused',
        error_type: (error as Error).name,
        message: (error as Error).message.slice(0, 512),
      };
    }
  } finally {
    if (closed !== spawned) owner.force_kill(performance.now() / 1000 + 5);
    await owner.settled();
    if (lifetime.active === owner) lifetime.active = undefined;
  }
  if (lifetime.stopping) throw new Error('archive fixture terminated', { cause: lifetime.failure });
  if (fixture_error) throw fixture_error;
  let destination_exists = false;
  try {
    await lstat(destination);
    destination_exists = true;
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  return {
    ...result,
    destination_exists,
    spawned,
    closed,
    settled_at_return,
    descriptor_held,
    validated,
    spool_private,
    output_private,
    spools_absent: !(await readdir(work)).some((value) => value.startsWith('.app-unpack-')),
    capacity,
    replaced_before_read,
    replaced_after_hash,
    killed,
    channel_closed,
    channel_close_timed_out,
    channel_close_ms,
    ...(mode === 'replace' ? { replacement: readFileSync(input, 'utf8') } : {}),
    ...(mode === 'appeared'
      ? { competitor: readFileSync(join(destination, 'sentinel'), 'utf8') }
      : {}),
  };
}

async function refused(work: () => unknown): Promise<boolean> {
  try {
    await work();
    return false;
  } catch {
    return true;
  }
}
async function compare_release(
  root: string,
  vectors: readonly ArchiveVector[],
  lifetime: ArchiveLifetime,
): Promise<unknown> {
  const app = join(root, 'app');
  const manifest = await validate_app_manifest(join(app, 'manifest.json'), {
    require_runtime: true,
  });
  const archive = join(root, 'python.tar.gz');
  const checksum = await hash_archive(archive);
  const ordinary = await observed_unpack(
    lifetime,
    root,
    'python-producer',
    archive,
    checksum,
    manifest.app_sha,
  );
  const sealed = await create_app_archive(app, join(root, 'node.tar.gz'));
  const node_ordinary = await observed_unpack(
    lifetime,
    root,
    'node-producer',
    join(root, 'node.tar.gz'),
    sealed,
    manifest.app_sha,
  );
  const archive_table = [];
  for (const vector of vectors) {
    const path = join(root, vector.name + '.tar.gz');
    archive_table.push({
      ...vector,
      result: await observed_unpack(
        lifetime,
        root,
        vector.name,
        path,
        await hash_archive(path),
        vector.expected_sha ?? manifest.app_sha,
      ),
    });
  }
  const owned = [];
  for (const mode of [
    'replace',
    'appeared',
    'kill',
    'low-initial',
    'low-spool',
    'low-output',
    'channel-loss',
  ])
    owned.push({
      mode,
      result: await observed_unpack(
        lifetime,
        root,
        mode,
        archive,
        checksum,
        manifest.app_sha,
        mode,
      ),
    });
  const graph: MigrationGraph = {
    version: 1,
    source_digest: 'a'.repeat(64),
    revisions: { initial: [], data_only: ['initial'] },
  };
  const frontier = [];
  for (const name of ['current', 'ancestor', 'missing', 'unknown']) {
    const access = createReadAccess(join(root, name + '.sqlite'));
    try {
      let pending: boolean | null = null;
      let pending_error: string | null = null,
        node_error: string | null = null;
      try {
        pending = access.read((sql) => schema_change_pending(sql, graph));
      } catch (error) {
        if (!(error instanceof AppValidationError)) throw error;
        pending_error = error.message;
      }
      try {
        access.read((sql) => require_node_database_frontier(sql, graph));
      } catch (error) {
        if (!(error instanceof AppValidationError)) throw error;
        node_error = error.message;
      }
      frontier.push({ pending, pending_error, node_refused: node_error !== null, node_error });
    } finally {
      access.close();
    }
  }
  let native_tree_admission: string;
  try {
    await complete_node_runtime(join(root, 'native-link-provider'));
    throw new Error('native packaging admitted an outside write target');
  } catch (error) {
    if (
      !(error instanceof AppValidationError) ||
      error.message !== 'app contains a symlink outside its root'
    )
      throw error;
    native_tree_admission = error.message;
  }
  return {
    native_tree_admission,
    identity: {
      source: await digest_app_source(app),
      artifact: await digest_app_artifact(app),
      manifest: { format: APP_FORMAT, ...manifest },
    },
    legacy: {
      format: APP_FORMAT,
      ...(await validate_app_manifest(join(root, 'legacy/manifest.json'))),
    },
    restored: ordinary.manifest,
    producer_trees: { python: ordinary, typescript: node_ordinary },
    archive_table,
    owned,
    node_archive_sha256: sealed,
    exclusive_refused: await refused(() => create_app_archive(app, join(root, 'node.tar.gz'))),
    hash_refusal: await observed_unpack(
      lifetime,
      root,
      'bad-hash',
      join(root, 'damaged.tar.gz'),
      '0'.repeat(64),
      manifest.app_sha,
    ),
    node_protocol: {
      accepted: !(await refused(() =>
        validate_app_manifest(join(root, 'v2/manifest.json'), { require_runtime: true }),
      )),
      missing_refused: await refused(() =>
        validate_app_manifest(join(root, 'v2-missing/manifest.json'), { require_runtime: true }),
      ),
      python_field_refused: await refused(() =>
        validate_app_manifest(join(root, 'v2-extra/manifest.json'), { require_runtime: true }),
      ),
      invalid_contract_before_tree: await (async () => {
        try {
          await validate_app_manifest(join(root, 'v2-invalid-both/manifest.json'), {
            require_runtime: true,
          });
          return null;
        } catch (error) {
          return (error as Error).message;
        }
      })(),
    },
    frontier,
  };
}

export async function release_parity(
  root: string,
  vectors: readonly ArchiveVector[],
): Promise<unknown> {
  const lifetime: ArchiveLifetime = { stopping: false };
  const terminate = (): void => {
    if (lifetime.stopping) return;
    lifetime.stopping = true;
    process.exitCode = 1;
    try {
      lifetime.active?.force_kill(performance.now() / 1000 + 5);
    } catch (error) {
      lifetime.failure = error;
    }
  };
  process.on('SIGTERM', terminate);
  try {
    return await compare_release(root, vectors, lifetime);
  } finally {
    process.off('SIGTERM', terminate);
  }
}
