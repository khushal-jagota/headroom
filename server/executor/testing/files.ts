/** Pure file proof fixtures. Importing this module never starts its CLI. */
import { createHash } from 'node:crypto';
import { readFileSync, existsSync, readdirSync, readlinkSync } from 'node:fs';
import {
  appendFile,
  mkdir,
  open,
  readFile,
  rename,
  symlink,
  unlink,
  writeFile,
  stat,
  link,
} from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { finished } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Database } from '../../core/database/contracts.ts';
import { createDatabase } from '../../core/database/index.ts';
import { PlannerError } from '../../core/contracts.ts';
import { ConversationMessageFiles } from '../../conversation/record/message_files.ts';
import type { Principal } from '../../core/contracts.ts';
import type { HeldFileLocation, VpsFileInbox } from '../contracts.ts';
import type { WireTypes } from '../wire.generated.ts';
import { SqliteCommandMailbox } from '../vps_mailbox.ts';
import { FileStaging, VpsFiles } from '../vps_files.ts';
import * as files from '../files.ts';
import { ExecutorValidationError, parse_identity, decode_value } from '../protocol.ts';
import { FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS } from '../configuration.ts';
import { is_shutdown_business_cancellation } from '../../conversation/shutdown.ts';

export function file_fixture(database: Database, path: string) {
  const staging = new FileStaging(path),
    mailbox = new SqliteCommandMailbox(database);
  const media = new AbortController();
  const authority = (child: WireTypes['ChildReference']): Principal => {
    const row = database.read((sql) =>
      sql.get<{ principal_kind: Principal['kind']; principal_id: string }>(
        'SELECT principal_kind,principal_id FROM executor_child_assignments WHERE executor_incarnation=? AND child_id=? AND conversation_id=? AND closed_at_ms IS NULL',
        [child.executor_incarnation, child.child_id, child.conversation_id],
      ),
    );
    if (!row) throw new PlannerError('agent_forbidden', 'child has no VPS assignment');
    return { kind: row.principal_kind, id: row.principal_id };
  };
  const inbox: VpsFileInbox = {
    runs: { authority },
    async authorize_upload(upload) {
      const principal = authority(upload.child);
      await staging.file_target(upload.manifest);
      if (principal.kind !== 'ticket' || principal.id !== upload.manifest.owner_id)
        throw new PlannerError('agent_forbidden', 'artifact belongs to another Ticket');
    },
    async store_upload(upload, source, options) {
      await inbox.authorize_upload(upload);
      await staging.store_upload(upload.manifest, source, options);
    },
  };
  const service = new VpsFiles(path, inbox, mailbox, new ConversationMessageFiles(path), {
    signal: media.signal,
  });
  return { service, staging, inbox, mailbox, media };
}
export function held_upload(first: Uint8Array) {
  let enter!: () => void,
    calls = 0,
    returns = 0;
  const entered = new Promise<void>((ready) => {
    enter = ready;
  });
  const chunks = new Readable({ read() {} });
  chunks.push(first);
  const closed = finished(chunks, { cleanup: true }).catch(() => {});
  const iterator = chunks[Symbol.asyncIterator]();
  const next = iterator.next.bind(iterator),
    end = iterator.return!.bind(iterator);
  iterator.next = (...args) => {
    if (++calls === 2) enter();
    return next(...args);
  };
  iterator.return = (...args) => {
    returns++;
    return end(...args);
  };
  chunks[Symbol.asyncIterator] = () => iterator;
  return {
    chunks,
    entered,
    closed,
    get calls() {
      return calls;
    },
    get returns() {
      return returns;
    },
  };
}
const hash = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
async function rejected(work: () => Promise<unknown>, message?: string): Promise<boolean> {
  try {
    await work();
    return false;
  } catch (error) {
    if (message && (!(error instanceof ExecutorValidationError) || error.message !== message))
      throw error;
    return true;
  }
}
async function outcome(work: () => Promise<unknown>): Promise<unknown> {
  try {
    await work();
    return null;
  } catch (error) {
    if (error instanceof PlannerError) return { code: error.code, message: error.message };
    if (!(error instanceof Error)) throw error;
    return { type: error.name, message: error.message };
  }
}
function location(parent: FileHandle, leaf: string, path: string): HeldFileLocation {
  return { parent, leaf, path: join(path, leaf) };
}

async function safety(root: string, manifest: WireTypes['FileManifest']): Promise<unknown> {
  const source = join(root, 'source'),
    target = join(root, 'target'),
    outside = join(root, 'outside');
  await mkdir(source, { recursive: true });
  await mkdir(outside);
  await writeFile(join(source, 'bytes'), 'result bytes');
  await writeFile(join(outside, 'sentinel'), 'untouched');
  const incoming = await files.directory_descriptor(source),
    parent = await files.directory_descriptor(target, { create: true });
  let result: Record<string, unknown>;
  try {
    await rename(source, source + '-held');
    await symlink(outside, source);
    await rename(target, target + '-held');
    await symlink(outside, target);
    const held = location(incoming, 'bytes', source),
      dest = (leaf: string) => location(parent, leaf, target);
    const read = await files.regular_descriptor(held);
    try {
      await read.read(Buffer.alloc(1), 0, 1, null);
      const [sha, count] = await files.descriptor_hash(read);
      const next = Buffer.alloc(1);
      await read.read(next, 0, 1, null);
      if (sha !== manifest.sha256 || count !== 12 || next.toString() !== 'e')
        throw new Error('hash consumed the implicit offset');
    } finally {
      await read.close();
    }
    await files.copy_verified(held, dest('copy'), manifest);
    const corrupt = await rejected(
      () => files.copy_verified(held, dest('bad'), { ...manifest, sha256: '0'.repeat(64) }),
      'incomplete or corrupt file',
    );
    await files.install_verified(held, dest('installed'), manifest);
    for (const leaf of ['hardlink', 'device']) {
      const path = join(target + '-held', leaf);
      if (leaf === 'hardlink') await link(join(outside, 'sentinel'), path);
      else await symlink('/dev/null', path);
      if (!(await rejected(() => files.copy_verified(held, dest(leaf), manifest))))
        throw new Error('Exclusive copy replaced destination entry');
      await files.install_verified(held, dest(leaf), manifest);
      if ((await readFile(path)).toString() !== 'result bytes')
        throw new Error('Install did not replace entry');
      await unlink(path);
    }
    const created = await files.create_file_at(dest('created'));
    try {
      await created.write(Buffer.from('private'));
    } finally {
      await created.close();
    }
    await symlink(join(outside, 'sentinel'), join(target + '-held', 'link'));
    const leaf_refused = await rejected(() => files.regular_descriptor(dest('link')));
    const exclusive = await rejected(() => files.create_file_at(dest('link')));
    await files.unlink_at(parent, 'link');
    await files.unlink_at(parent, 'created');
    await files.rename_at(incoming, 'bytes', parent, 'moved');
    await files.unlink_at(parent, 'moved');
    await files.fsync_directory(parent);
    const verified = await files.verified_version(dest('installed'), manifest);
    const version = files.file_version_at(parent, 'installed');
    await writeFile(join(target + '-held', 'installed'), 'changed');
    result = {
      copied: (await readFile(join(target + '-held', 'copy'))).toString() === 'result bytes',
      corrupt,
      cleaned: !existsSync(join(target + '-held', 'bad')),
      leaf_refused,
      exclusive,
      verified: verified !== null && version !== null && verified.every((v, i) => v === version[i]),
      changed: (await files.verified_version(dest('installed'), manifest)) === null,
      private_mode: (await stat(join(target + '-held', 'copy'))).mode & 0o777,
      directory_mode: (await stat(target + '-held')).mode & 0o777,
      namespace_refused:
        (await rejected(() => files.directory_descriptor(source))) &&
        (await rejected(() => files.file_digest(join(source, 'sentinel')))),
      outside: readdirSync(outside)
        .sort()
        .map((name) => [name, readFileSync(join(outside, name)).toString()]),
    };
  } finally {
    await incoming.close();
    await parent.close();
  }
  await files.assert_file_support();
  const fifo = await rejected(() => files.regular_descriptor(join(root, 'fifo')));
  if (!fifo) throw new Error('FIFO admission did not refuse');
  return result;
}
type Spec = {
  device: string;
  paths: [string, string, string][];
  root: string;
  path: string;
  identity: WireTypes['ExecutorIdentity'];
  child: WireTypes['ChildReference'];
  manifest: WireTypes['FileManifest'];
  big: WireTypes['FileManifest'];
  ids: string[];
  commands: WireTypes['ChildCommand'][];
  uploads: {
    name: string;
    upload: WireTypes['FileUploadRequest'];
    sha: string;
    length: number;
    chunks: string[];
    drop?: boolean;
  }[];
};
async function main(raw: Spec): Promise<void> {
  const spec = { ...raw, identity: parse_identity(raw.identity) };
  const database = createDatabase(spec.path),
    fixture = file_fixture(database, spec.path);
  const { service, staging, mailbox, media, inbox } = fixture;
  const result: Record<string, unknown> = {};
  try {
    result.safety = await safety(join(spec.root, 'safety'), spec.manifest);
    const device_before = await stat(spec.device, { bigint: true });
    const device_parent = await files.directory_descriptor(dirname(spec.device));
    const source_parent = await files.directory_descriptor(
      join(spec.root, 'safety', 'target-held'),
    );
    try {
      const error = await outcome(() =>
        files.copy_verified(
          location(source_parent, 'copy', ''),
          location(device_parent, basename(spec.device), dirname(spec.device)),
          spec.manifest,
        ),
      );
      const device_after = await stat(spec.device, { bigint: true });
      result.device = {
        error,
        contained:
          (await files.contained_path(dirname(spec.device), basename(spec.device))) === spec.device,
        unchanged:
          device_before.dev === device_after.dev &&
          device_before.ino === device_after.ino &&
          device_after.isCharacterDevice(),
      };
    } finally {
      await source_parent.close();
      await device_parent.close();
    }
    const path_root = join(spec.root, 'paths');
    await mkdir(path_root);
    await symlink('.', join(path_root, 'middle'));
    await symlink('loop', join(path_root, 'loop'));
    result.paths = [];
    for (const [name, text, prefix] of spec.paths)
      (result.paths as unknown[]).push({
        name,
        error: await outcome(() => files.contained_path(join(path_root, prefix), text)),
      });
    result.constructor_root =
      service.staging === join(spec.root, 'executor-uploads') &&
      staging.staging === service.staging;
    result.constants = [FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS];
    const reordered_manifest = decode_value(
      Object.fromEntries(Object.entries(spec.manifest).reverse()),
      'FileManifest',
    );
    if (await staging.staged_file(spec.manifest) !== await staging.staged_file(reordered_manifest))
      throw new Error('Manifest property order changed its staged filename');
    result.staging_keys = [
      basename(await staging.staged_file(spec.manifest)),
      basename(await staging.staged_file(spec.big)),
    ];
    result.uploads = [];
    for (const entry of spec.uploads) {
      async function* chunks() {
        for (const piece of entry.chunks) yield Buffer.from(piece, 'base64');
        if (entry.drop) throw new TypeError('fixture disconnect');
      }
      const error = await outcome(() =>
        service.upload(
          spec.identity,
          entry.sha,
          entry.upload,
          entry.length,
          Readable.from(chunks()),
        ),
      );
      (result.uploads as unknown[]).push({
        name: entry.name,
        error,
        incoming: existsSync(staging.staging)
          ? readdirSync(staging.staging).filter((name) => name.startsWith('.incoming-')).length
          : 0,
      });
    }
    result.staged = hash(await readFile(await staging.staged_file(spec.manifest)));
    result.served = existsSync(await staging.file_target(spec.manifest));
    for (const command of spec.commands) await mailbox.put_child_command(command);
    const physical = join(spec.root, 'database-target');
    await mkdir(physical);
    await writeFile(join(physical, 'state.sqlite'), '');
    const filename_alias = join(spec.root, 'filename.sqlite');
    await symlink(join(physical, 'state.sqlite'), filename_alias);
    const alternate = new VpsFiles(
      filename_alias,
      inbox,
      mailbox,
      new ConversationMessageFiles(filename_alias),
      { signal: media.signal },
    );
    try {
      const alternate_input = await alternate.input_file(spec.identity, 'prompt', spec.ids[0]!);
      result.filename_roots = [
        alternate.staging === join(physical, 'executor-uploads') &&
          new FileStaging(filename_alias).staging === alternate.staging,
        alternate_input.sha256 ===
          (
            await files.file_digest(
              new ConversationMessageFiles(spec.path).path_of('c', spec.ids[0]!),
            )
          )[0],
      ];
      await alternate_input.close();
    } finally {
      await alternate.stop({ deadline: performance.now() / 1000 + 5 });
    }
    const input = (id: string, file = spec.ids[0]!, identity = spec.identity) =>
      service.input_file(identity, id, file);
    const first = await input('prompt');
    result.input = [first.sha256, first.byte_count];
    await first.close();
    await rename(first.path, first.path + '-held');
    try {
      await symlink(basename(first.path) + '-held', first.path);
      const linked = await input('prompt');
      result.leaf_symlinks = [[linked.sha256, linked.byte_count]];
      await linked.close();
      await unlink(first.path);
      await symlink(join(spec.root, 'safety', 'outside', 'sentinel'), first.path);
      (result.leaf_symlinks as unknown[]).push(
        await outcome(async () => {
          const file = await input('prompt');
          await file.close();
        }),
      );
    } finally {
      await unlink(first.path);
      await rename(first.path + '-held', first.path);
    }
    await mailbox.poll(spec.identity, 0, 0);
    const delivered = await input('prompt');
    await delivered.close();
    result.missing = [
      await outcome(() => input('prompt', spec.ids[1])),
      await outcome(() =>
        input('prompt', spec.ids[0], { ...spec.identity, executor_incarnation: 'other' }),
      ),
    ];
    await mailbox.record_results(spec.identity, {
      outcomes: [
        {
          command_id: 'prompt',
          child_outcome: {
            command_id: 'prompt',
            child: spec.child,
            disposition: 'succeeded',
            result: {},
          },
        },
      ],
    });
    (result.missing as unknown[]).push(await outcome(() => input('prompt')));
    database.write((tx) =>
      tx.run("UPDATE executor_commands SET state='waiting' WHERE command_id='withdrawn'"),
    );
    await mailbox.withdraw_waiting('withdrawn', 'delivery_withdrawn');
    (result.missing as unknown[]).push(await outcome(() => input('withdrawn')));
    const steer = await input('steer');
    await steer.close();
    // One large distinct-block round trip, metering actual positional reads and writes.
    const large_path = new ConversationMessageFiles(spec.path).path_of('c', spec.ids[0]!);
    const status = await files.file_digest(large_path);
    const large = {
      ...spec.manifest,
      relative_path: 'out/large.bin',
      sha256: status[0],
      byte_count: Number(status[1]),
    };
    const sample = await open(large_path, 'r'),
      prototype = Object.getPrototypeOf(sample);
    const original_read = prototype.read,
      original_write = prototype.write;
    let max_read = 0,
      max_write = 0,
      max_gap_ms = 0,
      running = true,
      previous = performance.now();
    let heartbeat: ReturnType<typeof setImmediate>;
    const beat = () => {
      const time = performance.now();
      max_gap_ms = Math.max(max_gap_ms, time - previous);
      previous = time;
      if (running) heartbeat = setImmediate(beat);
    };
    const start = performance.now();
    heartbeat = setImmediate(beat);
    prototype.read = function (this: FileHandle, ...args: unknown[]) {
      if (typeof args[2] === 'number') max_read = Math.max(max_read, args[2]);
      return Reflect.apply(original_read, this, args);
    };
    prototype.write = function (this: FileHandle, ...args: unknown[]) {
      if (typeof args[2] === 'number') max_write = Math.max(max_write, args[2]);
      return Reflect.apply(original_write, this, args);
    };
    const transfer = createReadStream(large_path, { highWaterMark: 1024 * 1024 });
    const transfer_closed = finished(transfer, { cleanup: true }).catch(() => {});
    try {
      await sample.close();
      await service.upload(
        spec.identity,
        large.sha256,
        { manifest: large, child: spec.child },
        large.byte_count,
        transfer,
      );
      const held = await input('steer');
      await rename(large_path, large_path + '-held');
      await writeFile(large_path, 'replacement');
      await appendFile(large_path + '-held', 'appended');
      const retained: Uint8Array[] = [];
      for await (const chunk of held.chunks) retained.push(chunk);
      const digest = createHash('sha256');
      let count = 0,
        max_chunk = 0;
      for (const chunk of retained) {
        digest.update(chunk);
        count += chunk.byteLength;
        max_chunk = Math.max(max_chunk, chunk.byteLength);
      }
      result.large = { sha256: digest.digest('hex'), count, max_chunk, max_read, max_write };
      if (
        count !== held.byte_count ||
        max_chunk > FILE_CHUNK_BYTES ||
        max_read > FILE_CHUNK_BYTES ||
        max_write > FILE_CHUNK_BYTES
      )
        throw new Error('unbounded or changed input');
      const truncated = await input('steer');
      await writeFile(large_path, '');
      let premature: unknown;
      try {
        for await (const _piece of truncated.chunks) {
          /* consume */
        }
      } catch (error) {
        premature = error;
      }
      if (
        !(premature instanceof Error) ||
        premature.message !== 'input file is shorter than its digest' ||
        !('code' in premature) ||
        premature.code !== 'EIO'
      )
        throw new Error('truncated input lost its operational error');
      await truncated.close().catch(() => {});
    } finally {
      transfer.destroy();
      await transfer_closed;
      prototype.read = original_read;
      prototype.write = original_write;
      running = false;
      clearImmediate(heartbeat);
    }
    result.performance = { elapsed_ms: performance.now() - start, max_gap_ms };
    // Incoming cleanup retains its acquired parent even after the inbox moves that pathname.
    const original_store = inbox.store_upload;
    inbox.store_upload = async () => {
      await rename(staging.staging, staging.staging + '-held');
      await symlink(join(spec.root, 'safety', 'outside'), staging.staging);
      throw new TypeError('fixture inbox failure');
    };
    try {
      await outcome(() =>
        service.upload(
          spec.identity,
          spec.manifest.sha256,
          { manifest: spec.manifest, child: spec.child },
          spec.manifest.byte_count,
          Readable.from([Buffer.from('result bytes')]),
        ),
      );
      result.upload_swap = readdirSync(staging.staging + '-held').every(
        (name) => !name.startsWith('.incoming-'),
      );
    } finally {
      inbox.store_upload = original_store;
      await unlink(staging.staging);
      await rename(staging.staging + '-held', staging.staging);
    }
    // A never-consumed capability stays owned until the supplied invocation cutoff.
    const capability = await input('steer');
    const request = new AbortController();
    const held = held_upload(Buffer.from('resul'));
    try {
      const descriptor_target = (fd: string) => {
        try {
          return readlinkSync(`/proc/self/fd/${fd}`);
        } catch (error) {
          if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT')
            return null;
          throw error;
        }
      };
      const descriptors = readdirSync('/proc/self/fd').filter(
        (fd) => descriptor_target(fd) === capability.path,
      );
      if (descriptors.length !== 1) throw new Error('unused input descriptor is not uniquely live');
      const upload = service
        .upload(
          spec.identity,
          spec.manifest.sha256,
          { manifest: spec.manifest, child: spec.child },
          spec.manifest.byte_count,
          held.chunks,
          { signal: request.signal },
        )
        .catch((error) => error);
      await Promise.race([
        held.entered,
        upload.then(() => {
          throw new Error('request ended before held next');
        }),
      ]);
      request.abort(new Error('fixture request gone'));
      const observer = await upload;
      if (
        !is_shutdown_business_cancellation(observer) ||
        held.calls !== 2 ||
        held.returns !== 1 ||
        !held.chunks.destroyed
      )
        throw new Error('request cancellation did not destroy and return the actual iterator');
      const close = service.stop({ deadline: performance.now() / 1000 });
      result.zero_budget = await rejected(() => close);
      let joined = false;
      void service.settled().then(
        () => {
          joined = true;
        },
        () => {
          joined = true;
        },
      );
      await Promise.resolve();
      if (joined) throw new Error('unconsumed capability escaped owner');
      media.abort();
      await service.settled();
      if (descriptors.some((fd) => descriptor_target(fd) === capability.path))
        throw new Error('cutoff settled without closing unused input');
      if (
        Number(held.returns) !== 1 ||
        readdirSync(staging.staging).some((name) => name.startsWith('.incoming-'))
      )
        throw new Error('cutoff did not settle iterator and cleanup');
      result.sealed = await rejected(() => input('steer'));
    } finally {
      media.abort();
      held.chunks.destroy();
      await held.closed;
      await capability.close().catch((error) => {
        if (!is_shutdown_business_cancellation(error)) throw error;
      });
    }
    process.stdout.write(JSON.stringify(result));
  } finally {
    media.abort();
    await service.stop({ deadline: performance.now() / 1000 + 5 }).catch(() => {});
    await mailbox.stop({ deadline: performance.now() / 1000 + 5 });
    database.close();
  }
}
if (process.argv[1] === fileURLToPath(import.meta.url))
  await main(JSON.parse(readFileSync(0, 'utf8')) as unknown as Spec);
