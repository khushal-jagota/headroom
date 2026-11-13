/** Real file operations with one virtual transfer timer and narrow OS-failure seams. */
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { existsSync, readdirSync, readFileSync, readlinkSync } from 'node:fs';
import { mkdir, writeFile, unlink, rename } from 'node:fs/promises';
import { afterEach, expect, test, vi } from 'vitest';
import { Readable } from 'node:stream';
import { fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { file_fixture, held_upload } from './testing/files.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { FILE_TRANSFER_STALL_SECONDS } from './configuration.ts';
import { ValueError } from './protocol.ts';
import { FileCleanupError, copy_verified, directory_descriptor, unlink_at } from './files.ts';

const faults = vi.hoisted(() => ({
  missing_proc: false,
  close_error: null as Error | null,
  handoff_error: null as Error | null,
  handoff_fd: null as number | null,
  root: '',
  outside: '',
  swapped: false,
  outside_reads: 0,
  handles: [] as import('node:fs/promises').FileHandle[],
}));
vi.mock('node:fs', async (original) => {
  const actual = await original<typeof import('node:fs')>();
  return {
    ...actual,
    lstatSync: ((...args: Parameters<typeof actual.lstatSync>) => {
      const status = Reflect.apply(actual.lstatSync, actual, args);
      if (faults.root && String(args[0]) === faults.root && !faults.swapped) {
        actual.renameSync(faults.root, faults.root + '-held');
        actual.symlinkSync(faults.outside, faults.root, 'dir');
        faults.swapped = true;
      }
      return status;
    }) as typeof actual.lstatSync,
  };
});
vi.mock('node:fs/promises', async (original) => {
  const actual = await original<typeof import('node:fs/promises')>();
  return {
    ...actual,
    open: async (...args: Parameters<typeof actual.open>) => {
      const path = String(args[0]);
      if (faults.missing_proc && /^\/proc\/self\/fd\/\d+\/\.$/.test(path))
        throw Object.assign(new Error('fixture missing procfs'), { code: 'ENOENT' });
      const handle = await actual.open(...args),
        close = handle.close.bind(handle),
        read = handle.read.bind(handle);
      if (faults.handoff_error && path.endsWith('/handoff'))
        faults.handoff_fd = Number(path.split('/')[4]);
      faults.handles.push(handle);
      handle.read = ((...values: unknown[]) => {
        if (faults.outside && readlinkSync(`/proc/self/fd/${handle.fd}`).startsWith(faults.outside))
          faults.outside_reads++;
        return Reflect.apply(read, handle, values);
      }) as typeof handle.read;
      handle.close = async () => {
        const descriptor = handle.fd;
        await close();
        if (faults.handoff_error && faults.handoff_fd === descriptor) {
          const failure = faults.handoff_error;
          faults.handoff_error = null;
          faults.handoff_fd = null;
          throw failure;
        }
        if (faults.close_error && path.includes('/.tmp-')) {
          const failure = faults.close_error;
          faults.close_error = null;
          throw failure;
        }
      };
      return handle;
    },
  };
});
const identity = {
  protocol_version: 22,
  application_revision: 'fixture',
  spool_format_version: 5,
  executor_incarnation: '11111111-1111-4111-8111-111111111111',
  connection_generation: 0,
};
const child = {
  executor_incarnation: identity.executor_incarnation,
  child_id: 'child',
  conversation_id: 'c',
};
const content = Buffer.from('result bytes');
const manifest = {
  scope: 'ticket' as const,
  owner_id: 'ticket-1',
  relative_path: 'out/result.bin',
  sha256: createHash('sha256').update(content).digest('hex'),
  byte_count: content.byteLength,
};
const worlds: ReturnType<typeof file_fixture>[] = [];
function world() {
  const database = fixture(),
    path = fixture_path(database);
  const schema = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as { statements: string[] };
  database.write((tx) => {
    // The shared Ticket fixture predates the mailbox migration. Reuse its canonical DDL.
    for (const name of ['mac_executor_presence', 'executor_incarnations', 'executor_commands']) {
      const ddl = schema.statements.find((value) => value.startsWith(`CREATE TABLE ${name} (`));
      if (!ddl) throw new Error(`Missing executor fixture table ${name}`);
      tx.run(ddl);
    }
    tx.run(
      "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,created_at) VALUES ('c','codex','fixture','/tmp','full',1760000000)",
    );
    tx.run('INSERT INTO mac_executor_presence(singleton,executor_incarnation) VALUES (1,?)', [
      identity.executor_incarnation,
    ]);
    tx.run("INSERT INTO mac_runs VALUES (?,?,?,'ticket','ticket-1',1,0,'open')", [
      'c',
      identity.executor_incarnation,
      'child',
    ]);
    tx.run(
      "INSERT INTO executor_child_assignments VALUES (?,?,?,'ticket','ticket-1',1760000000000,NULL)",
      [identity.executor_incarnation, 'child', 'c'],
    );
  });
  const owned = file_fixture(database, path);
  worlds.push(owned);
  return { ...owned, path };
}
function contains(error: unknown, original: unknown): boolean {
  return (
    error === original ||
    (error instanceof AggregateError && error.errors.some((value) => contains(value, original)))
  );
}
afterEach(async () => {
  vi.useRealTimers();
  for (const { service, media, mailbox } of worlds.splice(0)) {
    media.abort();
    await service.stop({ deadline: performance.now() / 1000 + 5 }).catch(() => {});
    await mailbox.stop({ deadline: performance.now() / 1000 + 5 });
  }
  expect(faults.handles.every((handle) => handle.fd === -1)).toBe(true);
  faults.handles = [];
  faults.missing_proc = false;
  faults.close_error = null;
  faults.handoff_error = null;
  faults.handoff_fd = null;
  faults.root = '';
  faults.outside = '';
  faults.swapped = false;
  faults.outside_reads = 0;
});

test('the exported stall destroys the silent Readable before return and completes real cleanup', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const { service } = world();
  const held = held_upload(content.subarray(0, 5));
  const destroyed = vi.spyOn(held.chunks, 'destroy');
  const returned = vi.spyOn(held.chunks[Symbol.asyncIterator](), 'return');
  let replied = false;
  const receipt = service
    .upload(identity, manifest.sha256, { manifest, child }, manifest.byte_count, held.chunks)
    .then(
      () => {
        replied = true;
        return null;
      },
      (error) => {
        replied = true;
        return error;
      },
    );
  try {
    await Promise.race([
      held.entered,
      receipt.then(() => {
        throw new Error('upload ended before pending next');
      }),
    ]);
    await vi.advanceTimersByTimeAsync((FILE_TRANSFER_STALL_SECONDS - 1) * 1000);
    expect(replied).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await receipt).toMatchObject({
      code: 'retry_later',
      message: 'the upload stopped moving; send it again',
    });
    expect(held.calls).toBe(2);
    const stopped = await service
      .stop({ deadline: performance.now() / 1000 + 5 })
      .catch((error) => error);
    const joined = await service.settled().catch((error) => error);
    expect(joined).toBe(stopped);
    if (joined !== undefined) expect(contains(joined, await receipt)).toBe(true);
    await held.closed;
    expect(destroyed.mock.invocationCallOrder[0]).toBeLessThan(
      returned.mock.invocationCallOrder[0]!,
    );
    expect(held.chunks.destroyed).toBe(true);
    expect(faults.handles.every((handle) => handle.fd === -1)).toBe(true);
    await expect(
      service.upload(identity, manifest.sha256, { manifest, child }, 12, held.chunks),
    ).rejects.toMatchObject({ name: 'ServerLifecycleError' });
    expect(held.returns).toBe(1);
    expect(readdirSync(service.staging).filter((name) => name.startsWith('.incoming-'))).toEqual(
      [],
    );
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    held.chunks.destroy();
    await held.closed;
  }
});

test('missing procfs refuses before content intake and closes the actual probe descriptor', async () => {
  const { service } = world(),
    next = vi.fn(async () => ({ value: content, done: false }));
  const before = existsSync(service.staging) ? readdirSync(service.staging).sort() : [];
  faults.missing_proc = true;
  try {
    const error = await service
      .upload(identity, manifest.sha256, { manifest, child }, 12, {
        [Symbol.asyncIterator]: () => ({ next }),
        destroy() {},
      })
      .catch((error) => error);
    expect(error).toBeInstanceOf(ValueError);
    expect(error.cause).toMatchObject({ code: 'ENOENT' });
    expect(next).not.toHaveBeenCalled();
    expect(existsSync(service.staging) ? readdirSync(service.staging).sort() : []).toEqual(before);
  } finally {
    faults.missing_proc = false;
  }
});

test('a late Readable destroy failure stays owned after local incoming cleanup', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const { service } = world(),
    held = held_upload(content.subarray(0, 5));
  const late = new Error('fixture late source cleanup');
  let complete: ((error: Error | null) => void) | undefined;
  held.chunks._destroy = (_error, callback) => {
    complete = callback;
  };
  const release = () => {
    const callback = complete;
    complete = undefined;
    callback?.(late);
  };
  const receipt = service
    .upload(identity, manifest.sha256, { manifest, child }, 12, held.chunks)
    .catch((error) => error);
  const actual = service.settled().catch((error) => error);
  try {
    await held.entered;
    await vi.advanceTimersByTimeAsync(FILE_TRANSFER_STALL_SECONDS * 1000);
    expect(await receipt).toMatchObject({ code: 'retry_later' });
    await vi.waitFor(() => {
      expect(faults.handles.every((handle) => handle.fd === -1)).toBe(true);
      expect(readdirSync(service.staging)).toEqual([]);
    });
    await expect(service.stop({ deadline: performance.now() / 1000 })).rejects.toMatchObject({
      name: 'ShutdownDeadlineExceeded',
    });
    release();
    expect(contains(await actual, late)).toBe(true);
  } finally {
    held.chunks.destroy();
    release();
    await held.closed;
  }
});

test('a staging close failure removes its created temporary and remains observable after work completes', async () => {
  const { service, staging, path } = world(),
    failure = new Error('fixture close failure');
  faults.close_error = failure;
  const error = await service
    .upload(identity, manifest.sha256, { manifest, child }, 12, Readable.from([content]))
    .catch((error) => error);
  expect(error).toBeInstanceOf(FileCleanupError);
  expect(error.cleanup_errors).toContain(failure);
  expect(readdirSync(service.staging)).toEqual([]);
  await Promise.resolve();
  expect(
    contains(
      await service.stop({ deadline: performance.now() / 1000 + 5 }).catch((error) => error),
      failure,
    ),
  ).toBe(true);
  expect(contains(await service.settled().catch((error) => error), failure)).toBe(true);
  const handoff = new Error('fixture parent handoff close failure');
  await mkdir(join(dirname(path), 'handoff'), { recursive: true });
  faults.handoff_error = handoff;
  const parent_error = await directory_descriptor(join(dirname(path), 'handoff')).catch(
    (error) => error,
  );
  expect(parent_error).toBeInstanceOf(FileCleanupError);
  expect(parent_error.cleanup_errors).toContain(handoff);
  expect(faults.handles.every((handle) => handle.fd === -1)).toBe(true);
  const source_path = join(dirname(path), 'source-close-fixture');
  await writeFile(source_path, content);
  const parent = await directory_descriptor(staging.staging),
    source_parent = await directory_descriptor(dirname(path));
  try {
    await writeFile(join(staging.staging, 'unowned'), 'untouched');
    await expect(
      copy_verified(
        { parent: source_parent, leaf: 'source-close-fixture', path: source_path },
        { parent, leaf: 'unowned', path: join(staging.staging, 'unowned') },
        manifest,
      ),
    ).rejects.toMatchObject({ code: 'EEXIST' });
    expect(readFileSync(join(staging.staging, 'unowned')).toString()).toBe('untouched');
    await unlink_at(parent, 'unowned');
  } finally {
    await parent.close();
    await source_parent.close();
  }
});

test('a message-root swap during the actual lookup cannot redefine input byte authority', async () => {
  const { service, mailbox, path } = world(),
    messages = new ConversationMessageFiles(path);
  const kept = await messages.keep('c', content, {
    media_type: 'text/plain',
    sender_message_id: 'root-swap',
  });
  await mailbox.put_child_command({
    command_id: 'scope',
    child,
    turn: { conversation_id: 'c', turn_number: 1 },
    operation: 'write_prompt',
    arguments: {
      kind: 'write_prompt',
      content: [],
      sender_content: [
        {
          stored_file_id: kept.stored_file_id,
          media_type: 'text/plain',
          file_name: 'input',
          byte_count: 12,
        },
      ],
      sender_label: 'owner',
      mode: 'queue',
    },
  });
  const outside = join(dirname(path), 'outside-message-root');
  await mkdir(join(outside, 'c'), { recursive: true });
  const foreign = join(outside, 'c', kept.stored_file_id);
  await writeFile(foreign, 'outside sentinel');
  faults.root = join(dirname(path), 'files', 'conversations');
  faults.outside = outside;
  try {
    await expect(service.input_file(identity, 'scope', kept.stored_file_id)).rejects.toBeDefined();
    expect(faults.swapped).toBe(true);
    expect(faults.outside_reads).toBe(0);
    expect(readFileSync(foreign).toString()).toBe('outside sentinel');
  } finally {
    const root = faults.root;
    faults.root = '';
    if (faults.swapped) {
      await unlink(root);
      await rename(root + '-held', root);
    }
  }
});
